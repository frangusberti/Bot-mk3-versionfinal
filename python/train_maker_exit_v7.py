"""
Train maker exit v7.
Parte de v6_75k.

DIAGNOSTICO (audit comprehensivo):
  - Todos los modelos anteriores: Net ~ -$85 sobre 35k val steps (fees $85, gross ~$0 neto)
  - El '+1.32' anterior era FALSO: capturaba unrealized PnL de posicion abierta al final del loop
  - Root cause: profit_floor=0.5bps permite salir a 0.5bps ganancia = -6.5bps neto real
  - El modelo no tiene alpha porque el risk/reward esta invertido

CORRECCION: risk/reward 2:1
  - profit_floor_bps: 0.5 → 15.0 (solo salir cuando hay ganancia REAL sobre las fees)
  - stop_loss_bps: 30 → 10.0 (cortar perdedores rapido)
  - exit_fallback_loss_bps: 10 → 10.0 (igual, consistent)
  - exit_fallback_mfe_giveback_bps: 4.0 → 3.0 (trailing mas ajustado)

Con este setup:
  - Ganadora (maker exit): +15bps gross - 7bps fees = +8bps net
  - Perdedora (stop): -10bps - 5bps = -15bps net
  - Breakeven con 65% win rate (muy alcanzable con selective entry)

AUDIT FIX: usa equity directamente en lugar de realized_pnl (que es 0 cuando flat)
"""
import os, sys, gc, time, subprocess
os.chdir(r"C:\Bot mk3")
sys.path.insert(0, 'python')
sys.path.insert(0, 'python/bot_ml')

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from bot_ml.grpc_env import GrpcTradingEnv

BASE_MODEL = r"C:\Bot mk3\python\runs_train\maker_exit_v6\model_75k.zip"
BASE_VENV  = r"C:\Bot mk3\python\runs_train\maker_exit_v6\venv_75k.pkl"
OUT_DIR    = r"C:\Bot mk3\python\runs_train\maker_exit_v7"
LR = 2e-6
INITIAL_EQUITY = 10000.0

CFG = dict(
    profit_floor_bps=15.0,                       # KEY: was 0.5 → 15.0 (net positive with maker exit)
    stop_loss_bps=10.0,                          # KEY: was 30.0 → 10.0 (2:1 risk/reward)
    reward_fee_cost_weight=0.25,                 # was 0.10 → 0.25
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.03,     # slightly stronger trailing
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,
    long_veto_bb_pos_5m_threshold=0.40,
    long_veto_regime_dead_threshold=0.50,
    fill_model=2,
    use_exit_curriculum_d1=True,
    exit_fallback_loss_bps=10.0,                 # consistent with stop_loss
    exit_fallback_mfe_giveback_bps=3.0,          # tighter trailing on profit
    exit_fallback_thesis_decay_threshold=2.0,
    maker_first_exit_timeout_ms=12000,
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
    """
    Audit correcto: usa equity directamente para calcular Net PnL.
    - Acumula por episodio (done=True) + episodio final incompleto
    - Net = equity_final - initial_equity (incluye unrealized del ultimo episodio abierto)
    - Net_realized = Net - unrealized_final (solo trades cerrados)
    """
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
    total_net = 0.0      # acumulado de (equity_final - 10000) por episodio
    last_done = False
    info = {}

    obs = venv.reset()
    for _ in range(35000):
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

        # Acumular Net por episodio usando equity directamente
        if done[0]:
            ep_equity = info.get("equity", INITIAL_EQUITY)
            total_net += ep_equity - INITIAL_EQUITY
            obs = venv.reset(); in_pos = False; cs = 0

    # Ultimo episodio (puede estar abierto — incluye unrealized)
    if not last_done:
        ep_equity = info.get("equity", INITIAL_EQUITY)
        ep_unrealized = info.get("unrealized_pnl", 0.0)
        ep_net_realized = ep_equity - ep_unrealized - INITIAL_EQUITY
        total_net += ep_net_realized  # solo realized del ultimo episodio

    venv.close(); gc.collect()

    tot = em + et
    mk = em / tot * 100 if tot > 0 else 0
    net_per_trade = total_net / trades if trades > 0 else 0.0
    # Estimar fees totales (notional ~$2000, avg 8.5bps round-trip)
    est_fees = trades * 2000.0 * 0.00085
    gross_est = total_net + est_fees
    print(f"  Trades={trades}({trades/35.0:.1f}/1k)  Maker={mk:.0f}%  ({em}M/{et}T)  "
          f"Net={total_net:+.2f}  Net/trade={net_per_trade:+.4f}  "
          f"Gross~={gross_est:+.2f}  D1={fb}")
    return {"trades": trades, "maker_pct": round(mk,1), "net_pnl": round(total_net,4),
            "net_per_trade": round(net_per_trade,6), "d1": fb}

if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    server = start_server()
    try:
        train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
        train_venv = VecNormalize.load(BASE_VENV, train_venv)
        model = MaskablePPO.load(BASE_MODEL, env=train_venv, device="cuda",
                                 custom_objects={"learning_rate": LR})

        print(f"[TRAIN] v7: 100k steps from v6_75k | "
              f"floor=15bps, stop=10bps (2:1 R/R), fee_weight=0.25 | LR={LR}")
        print(f"  Objetivo: Net/trade > 0 (win rate 65% x +8bps neto por ganadora)")
        ckpts = [25000, 50000, 75000, 100000]
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
            quick_audit(model, venv_path, f"v7_{ckpt//1000}k")

            if ckpt < ckpts[-1]:
                server.terminate(); time.sleep(3); server = start_server()
                train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
                train_venv = VecNormalize.load(venv_path, train_venv)
                model.set_env(train_venv)
    finally:
        server.terminate()
