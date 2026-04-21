"""
Train maker exit v12 — Break the long-only local minimum.

DIAGNOSTICO v10/v11:
  - v10_100k/150k: primer PnL positivo con L+S (descubrio shorts brevemente)
  - v10_200k+: regreso a long-only -> convergencia a minimo local
  - v11: frozen at long-only desde el primer checkpoint (LR muy bajo, no escapa)
  - Root cause: v20b_final tiene priors profundos de long-only
    Fine-tuning a LR=1-2e-6 no puede superar esos priors en 300k steps

CAMBIOS CLAVE vs v10/v11:
  - LR: 1e-5 (5x mas alto) -> mas actualizacion, puede "reescribir" los priors
  - ent_coef: 0.02 (fue 0/0.01) -> mantiene exploracion durante el aprendizaje
  - use_selective_entry_short_v1=False -> SIN veto de shorts
    El bb_pos > 0.65 filtraba la mayoria de oportunidades de short en BTC bajista
    El modelo aprende CUANDO shortear desde sus propias features (166 dims)
  - use_selective_entry_long_v2=True -> Mantener veto de longs (funciono en v10)
  - 500k steps (mas tiempo para estabilizar L+S)
  - Base: v20b_final (limpio, sin sesgo pre-existente de L+S)

RIESGO: LR=1e-5 puede degradar la logica de long entry aprendida en v20b_final.
  Si se degrada: bajamos a 5e-6 en v13.

MATH esperado ($1200, 10x, 10%):
  - Fee burden: 0.7bps/trade (10% x 7bps)
  - Con 50/50 L+S en dataset bajista (-1.38% train, -0.86% val):
    Shorts capturan la bajada, longs son mas selectivos
  - Target: 30+ trades/100k steps con Net/trade > 0.10
"""
import os, sys, gc, time, subprocess
os.chdir(r"C:\Bot mk3")
sys.path.insert(0, 'python')
sys.path.insert(0, 'python/bot_ml')

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from bot_ml.grpc_env import GrpcTradingEnv

BASE_MODEL = r"C:\Bot mk3\python\runs_train\training_v9_selective_v20b\model_v20b_final.zip"
BASE_VENV  = r"C:\Bot mk3\python\runs_train\training_v9_selective_v20b\venv_v20b_final.pkl"
OUT_DIR    = r"C:\Bot mk3\python\runs_train\maker_exit_v12"
LR = 1e-5           # KEY: 5x mayor que v10 - rompe el minimo local long-only
ENT_COEF = 0.02     # KEY: mayor exploracion sostenida
INITIAL_EQUITY = 1200.0
VAL_STEPS = 100000

CFG = dict(
    # === Capital ($1200, 10x, 10%) ===
    initial_equity=1200.0,
    max_leverage=10.0,
    max_pos_frac=0.10,

    # === Exit (mismo que v10) ===
    profit_floor_bps=5.0,
    stop_loss_bps=5.0,

    # === Reward ===
    reward_fee_cost_weight=0.25,
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0003,
    reward_trailing_mfe_penalty_weight=0.04,
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,

    # === Long veto (mantener - funciono en v10) ===
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,
    long_veto_bb_pos_5m_threshold=0.40,   # ligeramente relajado vs v10 (0.35->0.40)
    long_veto_regime_dead_threshold=0.60,

    # === Short: SIN veto (KEY CHANGE) ===
    # El bb_pos>0.65 era demasiado restrictivo en dataset bajista
    # El modelo aprende el timing de shorts desde sus 166 features directamente
    use_selective_entry_short_v1=False,

    # === Fill model ===
    fill_model=2,

    # === Exit curriculum D1 ===
    use_exit_curriculum_d1=True,
    exit_fallback_loss_bps=5.0,
    exit_fallback_mfe_giveback_bps=4.0,
    exit_fallback_thesis_decay_threshold=2.0,
    maker_first_exit_timeout_ms=20000,
    exit_maker_pricing_multiplier=1.0,
    reward_exit_maker_bonus_weight=0.10,
    reward_exit_taker_penalty_weight=0.06,
)

def mask_fn(env): return env.action_masks()

def start_server():
    os.system("taskkill //F //IM bot-server.exe 2>NUL")
    time.sleep(2)
    proc = subprocess.Popen([r"C:\Bot mk3\target\release\bot-server.exe"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc

def make_env(dataset_id):
    return ActionMasker(
        GrpcTradingEnv("localhost:50051", dataset_id=dataset_id, symbol="BTCUSDT", **CFG), mask_fn)

def quick_audit(model, venv_path, label):
    print(f"\n[AUDIT] {label}")
    cfg_a = {**CFG, "reward_exit_maker_bonus_weight": 0.0, "reward_exit_taker_penalty_weight": 0.0}
    def make_val():
        return ActionMasker(GrpcTradingEnv("localhost:50051",
            dataset_id="golden_l2_v1_val", symbol="BTCUSDT", **cfg_a), mask_fn)
    venv = DummyVecEnv([make_val])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False; venv.norm_reward = False

    trades = em = et = fb = 0
    long_trades = short_trades = 0
    in_pos = False; cs = 0
    total_net = 0.0
    last_done = False
    info = {}

    obs = venv.reset()
    for _ in range(VAL_STEPS):
        masks = venv.env_method("action_masks")[0]
        act, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, _, done, info_list = venv.step(act)
        info = info_list[0]
        last_done = done[0]

        if info.get("exit_fallback_triggered", 0): fb += 1
        for fill in info.get("fills", []):
            s = fill.get("side",""); l = fill.get("liquidity","")
            is_exit = (cs>0 and "Sell" in s) or (cs<0 and "Buy" in s)
            if not is_exit and not in_pos:
                trades += 1
                if "Buy" in s: long_trades += 1
                else: short_trades += 1
            if is_exit:
                if "Maker" in l: em += 1
                else: et += 1
        pos = info.get("position_qty", 0.0)
        if abs(pos) > 1e-9:
            if not in_pos: in_pos = True; cs = 1 if pos > 0 else -1
        else:
            if in_pos: in_pos = False; cs = 0

        if done[0]:
            ep_equity = info.get("equity", INITIAL_EQUITY)
            total_net += ep_equity - INITIAL_EQUITY
            obs = venv.reset(); in_pos = False; cs = 0

    if not last_done:
        ep_equity = info.get("equity", INITIAL_EQUITY)
        ep_unrealized = info.get("unrealized_pnl", 0.0)
        total_net += ep_equity - ep_unrealized - INITIAL_EQUITY

    venv.close(); gc.collect()

    tot = em + et
    mk = em / tot * 100 if tot > 0 else 0
    net_per_trade = total_net / trades if trades > 0 else 0.0
    steps_k = VAL_STEPS / 1000.0
    print(f"  Trades={trades}({trades/steps_k:.1f}/1k)  L={long_trades} S={short_trades}  "
          f"Exits={tot}  Maker={mk:.0f}%  ({em}M/{et}T)  "
          f"Net={total_net:+.2f}  Net/trade={net_per_trade:+.4f}  D1={fb}")
    return {"trades": trades, "long": long_trades, "short": short_trades,
            "exits": tot, "maker_pct": round(mk,1),
            "net_pnl": round(total_net,4), "net_per_trade": round(net_per_trade,6), "d1": fb}

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    server = start_server()
    try:
        train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
        train_venv = VecNormalize.load(BASE_VENV, train_venv)
        model = MaskablePPO.load(BASE_MODEL, env=train_venv, device="cuda",
                                 custom_objects={"learning_rate": LR,
                                                 "ent_coef": ENT_COEF})

        print(f"[TRAIN] v12: LR={LR} ent={ENT_COEF} | NO short veto | from v20b_final")
        print(f"  Goal: break long-only local min, achieve stable L+S profitable policy")
        print(f"  $1200 / 10x / 10% | floor=5bps stop=5bps | 500k steps")
        ckpts = [50000, 100000, 150000, 200000, 300000, 400000, 500000]
        trained = 0
        for ckpt in ckpts:
            model.learn(total_timesteps=ckpt - trained, progress_bar=True,
                        reset_num_timesteps=False)
            trained = ckpt
            path = f"{OUT_DIR}/model_{ckpt//1000}k.zip"
            venv_path = f"{OUT_DIR}/venv_{ckpt//1000}k.pkl"
            model.save(path); train_venv.save(venv_path)
            train_venv.close()
            print(f"[SAVED] {path}")

            server.terminate(); time.sleep(3); server = start_server()
            quick_audit(model, venv_path, f"v12_{ckpt//1000}k")

            if ckpt < ckpts[-1]:
                server.terminate(); time.sleep(3); server = start_server()
                train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
                train_venv = VecNormalize.load(venv_path, train_venv)
                model.set_env(train_venv)
    finally:
        server.terminate()
