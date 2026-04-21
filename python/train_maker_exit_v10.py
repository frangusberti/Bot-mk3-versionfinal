"""
Train maker exit v10 — Long+Short Dual Direction Strategy.
Parte de v20b_final (base limpia).

CAMBIO ESTRATEGICO vs v9:
  - Habilita SHORTS (use_selective_entry_short_v1)
  - Balance: $1,200 USDT (paper trading real)
  - Apalancamiento: 10x (max_leverage=10.0)
  - Tamaño de posicion: 10% del equity (max_pos_frac=0.10)

MATH del nuevo setup:
  - Notional por trade = $1,200 × 10% = $120
  - A $95k BTC: ~0.001263 BTC (cerca del minimo de Binance)
  - Fee burden en equity bps = max_pos_frac × fee_rate = 10% × 7bps = 0.7bps (vs 1.4bps antes)
  - Con floor=5bps, stop=5bps, P(win)_random=50% (R/R 1:1 simétrico)
    EV = 60%*(5-0.7) + 40%*(-5-0.7) = +2.58 - 2.28 = +$0.30/trade -> positivo con alpha
  - Duplica las oportunidades (longs + shorts) -> más trades en el mismo dataset

VENTAJA ADICIONAL:
  - Dataset declina -1.4% en train: ahora los shorts explotan esa caída
  - Simetría: modelo no tiene sesgo estructural long-only

LONG entry (veto agresivo - solo entra en momentum bajo):
  - long_veto_imbalance_threshold=-0.20 (flujo vendedor: no long)
  - long_veto_bb_pos_5m_threshold=0.35 (precio ya alto en BB: no long)
  - long_veto_regime_dead_threshold=0.60 (regimen muerto: no long)

SHORT entry (simétrico - solo entra cuando precio está en la cima):
  - short_veto_imbalance_threshold=0.20 (flujo comprador: no short)
  - short_veto_bb_pos_5m_threshold=0.65 (precio bajo en BB: no short, esperar cima)
  - short_veto_regime_dead_threshold=0.60 (regimen muerto: no short)

EXIT curriculum D1 (maker-first):
  - profit_floor_bps=5bps (captura net ~4.3bps despues de fees 0.7bps)
  - stop_loss_bps=5bps (1:1 R/R, breakeven 50% + alpha de regime)
  - giveback=4bps, timeout=20s
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
OUT_DIR    = r"C:\Bot mk3\python\runs_train\maker_exit_v10"
LR = 2e-6
INITIAL_EQUITY = 1200.0
VAL_STEPS = 100000

CFG = dict(
    # === Capital params ($1200, 10x, 10%) ===
    initial_equity=1200.0,
    max_leverage=10.0,
    max_pos_frac=0.10,                            # $120 notional por trade

    # === Exit params (floor=5bps, stop=5bps — simetrico) ===
    profit_floor_bps=5.0,                         # captura ~4.3bps neto (0.7bps fees)
    stop_loss_bps=5.0,                            # 1:1 R/R, breakeven 50%

    # === Reward shaping ===
    reward_fee_cost_weight=0.25,
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0003,
    reward_trailing_mfe_penalty_weight=0.04,
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,

    # === Long entry veto (regime + BB + imbalance) ===
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,          # no long si flujo vendedor
    long_veto_bb_pos_5m_threshold=0.35,           # no long si precio alto en BB
    long_veto_regime_dead_threshold=0.60,         # no long si regimen muerto

    # === Short entry veto (simetrico) ===
    use_selective_entry_short_v1=True,
    short_veto_imbalance_threshold=0.20,          # no short si flujo comprador
    short_veto_bb_pos_5m_threshold=0.65,          # no short si precio bajo en BB
    short_veto_regime_dead_threshold=0.60,        # no short si regimen muerto

    # === Fill model ===
    fill_model=2,

    # === Exit curriculum D1 ===
    use_exit_curriculum_d1=True,
    exit_fallback_loss_bps=5.0,                   # consistent con stop
    exit_fallback_mfe_giveback_bps=4.0,           # trailing
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
    """Audit: equity-based PnL, 100k steps, sin reward shaping."""
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
                                 custom_objects={"learning_rate": LR})

        print(f"[TRAIN] v10: Long+Short | $1200 / 10x / 10% | floor=5bps stop=5bps (1:1 R/R)")
        print(f"  Fee burden: 0.7bps/trade (vs 1.4bps before) | LR={LR}")
        print(f"  Long veto: imb<-0.20 | bb>0.35 | dead>0.60")
        print(f"  Short veto: imb>+0.20 | bb<0.65 | dead>0.60")
        print(f"  Math target: 55%+ win rate -> +$0.17/trade -> +$17 per 100k val steps")
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
            quick_audit(model, venv_path, f"v10_{ckpt//1000}k")

            if ckpt < ckpts[-1]:
                server.terminate(); time.sleep(3); server = start_server()
                train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
                train_venv = VecNormalize.load(venv_path, train_venv)
                model.set_env(train_venv)
    finally:
        server.terminate()
