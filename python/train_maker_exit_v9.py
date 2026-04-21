"""
Train maker exit v9 — Trend Riding Strategy.
Parte de v20b_final.

CAMBIO ESTRATEGICO:
  Scalping de 15bps (v7/v8) es matematicamente negativo sin alpha fuerte.
  Las features de regimen (BB pos 5m, regime score) predicen movimientos de 30-50bps.

  Nueva estrategia: entrar solo en regimenes fuertes, esperar el movimiento grande.

  Math con floor=30bps, stop=20bps:
    - Random walk P(win) = 20/(20+30) = 40%
    - Con alpha de regimen estimamos 55%+
    - EV 55%: 55%*(30-7) + 45%*(-20-5) = +12.65 - 11.25 = +$1.40/trade
    - 30 trades * $1.40 = +$42 por 100k val steps -> POSITIVO

  Cambios clave vs v8:
    - profit_floor_bps: 15 → 30 (captura movimiento real del regimen)
    - stop_loss_bps: 10 → 20 (mas espacio, evita stops prematuros en ruido)
    - exit_fallback_loss_bps: 10 → 20 (consistent)
    - exit_fallback_mfe_giveback_bps: 8 → 12 (mayor trailing para movimientos grandes)
    - maker_first_exit_timeout_ms: 20000 → 30000 (30s para fill en movimientos grandes)
    - reward_trailing_mfe_penalty_weight: 0.03 → 0.05 (lock in profits stronger)
    - Training: 300k steps desde v20b_final (mas tiempo para aprender regimen)
    - Audit: 100k val steps

  Entrada mas agresiva en regimen:
    - long_veto_regime_dead_threshold: 0.50 → 0.60 (mas exigente - solo entra en regimen fuerte)
    - long_veto_bb_pos_5m_threshold: 0.40 → 0.35 (solo entra cuando BB pos es bajo = momentum)
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
OUT_DIR    = r"C:\Bot mk3\python\runs_train\maker_exit_v9"
LR = 2e-6
INITIAL_EQUITY = 10000.0
VAL_STEPS = 100000

CFG = dict(
    profit_floor_bps=30.0,                       # KEY: 15 → 30 (captura movimiento de regimen)
    stop_loss_bps=20.0,                          # KEY: 10 → 20 (evita stops prematuros)
    reward_fee_cost_weight=0.25,
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0003,         # menor penalidad por hold largo
    reward_trailing_mfe_penalty_weight=0.05,     # 0.03 → 0.05 (lock in profits)
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,
    long_veto_bb_pos_5m_threshold=0.35,          # 0.40 → 0.35 (momentum entry)
    long_veto_regime_dead_threshold=0.60,        # 0.50 → 0.60 (solo regimen fuerte)
    fill_model=2,
    use_exit_curriculum_d1=True,
    exit_fallback_loss_bps=20.0,                 # consistent con stop
    exit_fallback_mfe_giveback_bps=12.0,         # 8 → 12 (trailing para movimientos grandes)
    exit_fallback_thesis_decay_threshold=3.0,    # 2.0 → 3.0 (mas tolerante)
    maker_first_exit_timeout_ms=30000,           # 20s → 30s
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
    """Audit correcto: equity-based PnL, 100k steps."""
    print(f"\n[AUDIT] {label}")
    cfg_a = {**CFG, "reward_exit_maker_bonus_weight": 0.0, "reward_exit_taker_penalty_weight": 0.0}
    def make_val():
        return ActionMasker(GrpcTradingEnv("localhost:50051",
            dataset_id="golden_l2_v1_val", symbol="BTCUSDT", **cfg_a), mask_fn)
    venv = DummyVecEnv([make_val])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False; venv.norm_reward = False

    trades = em = et = fb = 0
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
            if not is_exit and not in_pos: trades += 1
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
    win_rate = em / tot * 100 if tot > 0 else 0  # rough: maker exits tend to be winners
    print(f"  Trades={trades}({trades/100.0:.1f}/1k)  Exits={tot}  Maker={mk:.0f}%  ({em}M/{et}T)  "
          f"Net={total_net:+.2f}  Net/trade={net_per_trade:+.4f}  D1={fb}")
    return {"trades": trades, "exits": tot, "maker_pct": round(mk,1),
            "net_pnl": round(total_net,4), "net_per_trade": round(net_per_trade,6), "d1": fb}

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    server = start_server()
    try:
        train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
        train_venv = VecNormalize.load(BASE_VENV, train_venv)
        model = MaskablePPO.load(BASE_MODEL, env=train_venv, device="cuda",
                                 custom_objects={"learning_rate": LR})

        print(f"[TRAIN] v9: 300k steps from v20b_final | TREND RIDING")
        print(f"  floor=30bps stop=20bps (R/R 1.5:1) | regime_threshold=0.60 | LR={LR}")
        print(f"  Math target: 55% win rate -> +$1.40/trade -> +$42 per 100k val steps")
        ckpts = [50000, 100000, 150000, 200000, 250000, 300000]
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
            quick_audit(model, venv_path, f"v9_{ckpt//1000}k")

            if ckpt < ckpts[-1]:
                server.terminate(); time.sleep(3); server = start_server()
                train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
                train_venv = VecNormalize.load(venv_path, train_venv)
                model.set_env(train_venv)
    finally:
        server.terminate()
