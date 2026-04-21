"""
Audit comprehensivo de todos los checkpoints con PnL acumulado correcto.

FIX vs audits anteriores:
  - rpnl/fees se acumulan en CADA reset de episodio, no solo el ultimo
  - Reporta: Net total, Net/trade, fees totales, gross PnL

Uso: python python/audit_comprehensive.py
"""
import os, sys, gc, time, subprocess
os.chdir(r"C:\Bot mk3")
sys.path.insert(0, 'python')
sys.path.insert(0, 'python/bot_ml')

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from bot_ml.grpc_env import GrpcTradingEnv

def mask_fn(env): return env.action_masks()

def start_server():
    os.system("taskkill //F //IM bot-server.exe 2>NUL")
    time.sleep(2)
    proc = subprocess.Popen([r"C:\Bot mk3\target\release\bot-server.exe"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    return proc

VAL_STEPS = 35000

def audit_model(model_path, venv_path, label, cfg):
    """
    Audit con PnL acumulado correcto a traves de todos los episodios.
    """
    cfg_a = {**cfg, "reward_exit_maker_bonus_weight": 0.0, "reward_exit_taker_penalty_weight": 0.0}
    def make_val():
        return ActionMasker(GrpcTradingEnv("localhost:50051",
            dataset_id="golden_l2_v1_val", symbol="BTCUSDT", **cfg_a), mask_fn)

    venv = DummyVecEnv([make_val])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False; venv.norm_reward = False

    model = MaskablePPO.load(model_path, env=venv, device="cuda")

    trades = em = et = fb = 0
    in_pos = False; cs = 0
    total_rpnl = 0.0
    total_fees = 0.0
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

        # Acumular PnL/fees en cada fin de episodio
        if done[0]:
            total_rpnl += info.get("realized_pnl", 0.0)
            total_fees += info.get("fees_paid", 0.0)
            obs = venv.reset(); in_pos = False; cs = 0

    # Ultimo episodio (puede no haber terminado con done=True)
    if not last_done:
        total_rpnl += info.get("realized_pnl", 0.0)
        total_fees += info.get("fees_paid", 0.0)

    venv.close(); gc.collect()

    tot = em + et
    mk = em / tot * 100 if tot > 0 else 0
    net = total_rpnl - total_fees
    net_per_trade = net / trades if trades > 0 else 0.0
    print(f"  {label:<20} Trades={trades:3d}({trades/35.0:.1f}/1k)  "
          f"Maker={mk:.0f}%({em}M/{et}T)  "
          f"Gross={total_rpnl:+.2f}  Fees={total_fees:.2f}  "
          f"Net={net:+.2f}  Net/trade={net_per_trade:+.4f}  D1={fb}")
    return {"label": label, "trades": trades, "maker_pct": round(mk,1),
            "gross": round(total_rpnl,4), "fees": round(total_fees,4),
            "net": round(net,4), "net_per_trade": round(net_per_trade,6), "d1": fb}


# Todos los checkpoints relevantes con sus CFG
CANDIDATES = [
    {
        "label": "v5_50k",
        "model": r"C:\Bot mk3\python\runs_train\maker_exit_v5\model_50k.zip",
        "venv":  r"C:\Bot mk3\python\runs_train\maker_exit_v5\venv_50k.pkl",
        "cfg": dict(
            profit_floor_bps=0.5, stop_loss_bps=30.0,
            reward_fee_cost_weight=0.1, reward_as_penalty_weight=0.5,
            reward_inventory_risk_weight=0.0005, reward_trailing_mfe_penalty_weight=0.02,
            use_winner_unlock=True, reward_thesis_decay_weight=0.0001,
            use_selective_entry_long_v2=True, long_veto_imbalance_threshold=-0.20,
            long_veto_bb_pos_5m_threshold=0.40, long_veto_regime_dead_threshold=0.50,
            fill_model=2, use_exit_curriculum_d1=True,
            exit_fallback_loss_bps=10.0, exit_fallback_mfe_giveback_bps=4.0,
            exit_fallback_thesis_decay_threshold=2.0,
            maker_first_exit_timeout_ms=8000, exit_maker_pricing_multiplier=1.0,
            reward_exit_maker_bonus_weight=0.05, reward_exit_taker_penalty_weight=0.03,
        ),
    },
    {
        "label": "v6_25k",
        "model": r"C:\Bot mk3\python\runs_train\maker_exit_v6\model_25k.zip",
        "venv":  r"C:\Bot mk3\python\runs_train\maker_exit_v6\venv_25k.pkl",
        "cfg": dict(
            profit_floor_bps=0.5, stop_loss_bps=30.0,
            reward_fee_cost_weight=0.1, reward_as_penalty_weight=0.5,
            reward_inventory_risk_weight=0.0005, reward_trailing_mfe_penalty_weight=0.02,
            use_winner_unlock=True, reward_thesis_decay_weight=0.0001,
            use_selective_entry_long_v2=True, long_veto_imbalance_threshold=-0.20,
            long_veto_bb_pos_5m_threshold=0.40, long_veto_regime_dead_threshold=0.50,
            fill_model=2, use_exit_curriculum_d1=True,
            exit_fallback_loss_bps=10.0, exit_fallback_mfe_giveback_bps=4.0,
            exit_fallback_thesis_decay_threshold=2.0,
            maker_first_exit_timeout_ms=12000, exit_maker_pricing_multiplier=1.0,
            reward_exit_maker_bonus_weight=0.10, reward_exit_taker_penalty_weight=0.06,
        ),
    },
    {
        "label": "v6_75k",
        "model": r"C:\Bot mk3\python\runs_train\maker_exit_v6\model_75k.zip",
        "venv":  r"C:\Bot mk3\python\runs_train\maker_exit_v6\venv_75k.pkl",
        "cfg": dict(
            profit_floor_bps=0.5, stop_loss_bps=30.0,
            reward_fee_cost_weight=0.1, reward_as_penalty_weight=0.5,
            reward_inventory_risk_weight=0.0005, reward_trailing_mfe_penalty_weight=0.02,
            use_winner_unlock=True, reward_thesis_decay_weight=0.0001,
            use_selective_entry_long_v2=True, long_veto_imbalance_threshold=-0.20,
            long_veto_bb_pos_5m_threshold=0.40, long_veto_regime_dead_threshold=0.50,
            fill_model=2, use_exit_curriculum_d1=True,
            exit_fallback_loss_bps=10.0, exit_fallback_mfe_giveback_bps=4.0,
            exit_fallback_thesis_decay_threshold=2.0,
            maker_first_exit_timeout_ms=12000, exit_maker_pricing_multiplier=1.0,
            reward_exit_maker_bonus_weight=0.10, reward_exit_taker_penalty_weight=0.06,
        ),
    },
    {
        "label": "v6_100k",
        "model": r"C:\Bot mk3\python\runs_train\maker_exit_v6\model_100k.zip",
        "venv":  r"C:\Bot mk3\python\runs_train\maker_exit_v6\venv_100k.pkl",
        "cfg": dict(
            profit_floor_bps=0.5, stop_loss_bps=30.0,
            reward_fee_cost_weight=0.1, reward_as_penalty_weight=0.5,
            reward_inventory_risk_weight=0.0005, reward_trailing_mfe_penalty_weight=0.02,
            use_winner_unlock=True, reward_thesis_decay_weight=0.0001,
            use_selective_entry_long_v2=True, long_veto_imbalance_threshold=-0.20,
            long_veto_bb_pos_5m_threshold=0.40, long_veto_regime_dead_threshold=0.50,
            fill_model=2, use_exit_curriculum_d1=True,
            exit_fallback_loss_bps=10.0, exit_fallback_mfe_giveback_bps=4.0,
            exit_fallback_thesis_decay_threshold=2.0,
            maker_first_exit_timeout_ms=12000, exit_maker_pricing_multiplier=1.0,
            reward_exit_maker_bonus_weight=0.10, reward_exit_taker_penalty_weight=0.06,
        ),
    },
]

if __name__ == "__main__":
    server = start_server()
    print(f"\n{'='*90}")
    print(f"AUDIT COMPREHENSIVO — {VAL_STEPS} val steps — PnL acumulado (FIXED)")
    print(f"Capital: $10,000 USDT | Notional/trade: ~$2,000 | Leverage: 0.2x")
    print(f"Fees simuladas: Maker=2bps, Taker=5bps (Binance VIP0 Futuros)")
    print(f"{'='*90}")
    results = []
    try:
        for c in CANDIDATES:
            server.terminate(); time.sleep(3); server = start_server()
            r = audit_model(c["model"], c["venv"], c["label"], c["cfg"])
            results.append(r)
    finally:
        server.terminate()

    print(f"\n{'='*90}")
    print("RESUMEN (ordenado por Net PnL):")
    for r in sorted(results, key=lambda x: x["net"], reverse=True):
        print(f"  {r['label']:<20} Net={r['net']:+.4f}  Net/trade={r['net_per_trade']:+.6f}  "
              f"Maker={r['maker_pct']:.0f}%  Trades={r['trades']}  D1={r['d1']}")
