#!/usr/bin/env python3
"""
Standalone audit de todos los modelos candidatos actuales.

Corre cada modelo en el dataset de validacion, guarda resultados en JSON
e imprime tabla comparativa.

Uso:
    cd "C:\\Bot mk3"
    python python/audit_all_candidates.py
"""
import os, sys, gc, json, time, subprocess, datetime
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from bot_ml.grpc_env import GrpcTradingEnv

# ---------------------------------------------------------------------------
# Configs base
# ---------------------------------------------------------------------------
_BASE_V20B = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.02,
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,
    long_veto_bb_pos_5m_threshold=0.40,
    long_veto_regime_dead_threshold=0.50,
    fill_model=2,
    use_exit_curriculum_d1=True,
    exit_fallback_loss_bps=10.0,
    exit_fallback_mfe_giveback_bps=4.0,
    exit_fallback_thesis_decay_threshold=0.40,
    reward_exit_maker_bonus_weight=0.0,
)

_BASE_V21 = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.02,
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.05,
    long_veto_bb_pos_5m_threshold=0.35,
    long_veto_regime_dead_threshold=0.40,
    fill_model=2,
    use_exit_curriculum_d1=True,
    exit_fallback_loss_bps=10.0,
    exit_fallback_mfe_giveback_bps=4.0,
    exit_fallback_thesis_decay_threshold=0.40,
    reward_exit_maker_bonus_weight=0.0,
)

# ---------------------------------------------------------------------------
# Candidatos
# ---------------------------------------------------------------------------
CANDIDATES = [
    # --- Baselines ---
    dict(
        name="v20b_baseline",
        model="python/runs_train/training_v9_selective_v20b/model_v20b_final.zip",
        venv ="python/runs_train/training_v9_selective_v20b/venv_v20b_final.pkl",
        cfg  ={**_BASE_V20B, "maker_first_exit_timeout_ms": 8000, "exit_maker_pricing_multiplier": 1.0},
    ),
    dict(
        name="v21_baseline",
        model="python/runs_train/training_v9_selective_v21/model_v21_final.zip",
        venv ="python/runs_train/training_v9_selective_v21/venv_v21_final.pkl",
        cfg  ={**_BASE_V21, "maker_first_exit_timeout_ms": 8000, "exit_maker_pricing_multiplier": 1.0},
    ),
    # --- Timeout calib (base v20b, pricing=1.0) ---
    dict(
        name="timeout_3s",
        model="python/runs_train/abc_timeout_calib/Rama_A_3s/model.zip",
        venv ="python/runs_train/abc_timeout_calib/Rama_A_3s/venv.pkl",
        cfg  ={**_BASE_V20B, "maker_first_exit_timeout_ms": 3000, "exit_maker_pricing_multiplier": 1.0},
    ),
    dict(
        name="timeout_5s",
        model="python/runs_train/abc_timeout_calib/Rama_B_5s/model.zip",
        venv ="python/runs_train/abc_timeout_calib/Rama_B_5s/venv.pkl",
        cfg  ={**_BASE_V20B, "maker_first_exit_timeout_ms": 5000, "exit_maker_pricing_multiplier": 1.0},
    ),
    dict(
        name="timeout_8s",
        model="python/runs_train/abc_timeout_calib/Rama_C_8s/model.zip",
        venv ="python/runs_train/abc_timeout_calib/Rama_C_8s/venv.pkl",
        cfg  ={**_BASE_V20B, "maker_first_exit_timeout_ms": 8000, "exit_maker_pricing_multiplier": 1.0},
    ),
    # --- Pricing calib (base v20b, timeout=8s) ---
    dict(
        name="pricing_x1.0",
        model="python/runs_train/abc_pricing_calib/Rama_P1_Base/model.zip",
        venv ="python/runs_train/abc_pricing_calib/Rama_P1_Base/venv.pkl",
        cfg  ={**_BASE_V20B, "maker_first_exit_timeout_ms": 8000, "exit_maker_pricing_multiplier": 1.0},
    ),
    dict(
        name="pricing_x0.5",
        model="python/runs_train/abc_pricing_calib/Rama_P2_Aggressive/model.zip",
        venv ="python/runs_train/abc_pricing_calib/Rama_P2_Aggressive/venv.pkl",
        cfg  ={**_BASE_V20B, "maker_first_exit_timeout_ms": 8000, "exit_maker_pricing_multiplier": 0.5},
    ),
    dict(
        name="pricing_x0.1",
        model="python/runs_train/abc_pricing_calib/Rama_P3_Ultra/model.zip",
        venv ="python/runs_train/abc_pricing_calib/Rama_P3_Ultra/venv.pkl",
        cfg  ={**_BASE_V20B, "maker_first_exit_timeout_ms": 8000, "exit_maker_pricing_multiplier": 0.1},
    ),
]

AUDIT_STEPS = 35000
DATASET_VAL  = "golden_l2_v1_val"

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
def start_server():
    print("[SERVER] Reiniciando bot-server...")
    os.system("taskkill /F /IM bot-server.exe 2>NUL")
    time.sleep(2)
    proc = subprocess.Popen(
        [r"target\release\bot-server.exe"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    print("[SERVER] Listo.")
    return proc

# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def audit_candidate(cand: dict) -> dict:
    name = cand["name"]
    print(f"\n{'='*65}")
    print(f"  AUDITANDO: {name}")
    print(f"{'='*65}")

    cfg = cand["cfg"]

    def make_env():
        return ActionMasker(
            GrpcTradingEnv(server_addr="localhost:50051", dataset_id=DATASET_VAL,
                           symbol="BTCUSDT", **cfg),
            lambda e: e.action_masks()
        )

    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(cand["venv"], venv)
    venv.training  = False
    venv.norm_reward = False

    model = MaskablePPO.load(cand["model"], device="cpu")

    # --- contadores ---
    trades = 0
    fees_total = 0.0
    exit_maker = 0
    exit_taker = 0
    pnl_long = 0.0
    pnl_short = 0.0
    mfe_ratio_sum = 0.0
    mfe_count = 0
    upnl_peak_sum = 0.0
    d1_fallbacks = 0
    never_reach_05 = 0   # trades que nunca vieron upnl > 0.5 bps
    done_reasons: dict = {}
    steps_done = 0

    in_pos = False
    current_side = 0
    trade_max_upnl = 0.0
    trade_start_rpnl = 0.0
    last_info: dict = {}

    obs = venv.reset()

    for step in range(AUDIT_STEPS):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, _reward, done_arr, info_list = venv.step(action)
        info = info_list[0]
        last_info = info
        steps_done += 1
        done = bool(done_arr[0])

        pos_qty  = info.get("position_qty", 0.0)
        equity   = info.get("equity", 10000.0) or 10000.0
        upnl_bps = info.get("unrealized_pnl", 0.0) / equity * 10000.0

        if info.get("exit_fallback_triggered", 0):
            d1_fallbacks += 1

        # fees y conteo de trades desde fills
        for fill in info.get("fills", []):
            side = fill.get("side", "")
            is_exit = (current_side > 0 and "Sell" in side) or \
                      (current_side < 0 and "Buy" in side)
            fee = abs(fill.get("fee", 0.0))
            fees_total += fee
            if not is_exit and not in_pos:
                trades += 1
            if is_exit:
                if "Maker" in fill.get("liquidity", ""):
                    exit_maker += 1
                else:
                    exit_taker += 1

        # tracking de posicion
        if abs(pos_qty) > 1e-9:
            if not in_pos:
                in_pos = True
                current_side = 1 if pos_qty > 0 else -1
                trade_max_upnl = upnl_bps
                trade_start_rpnl = info.get("realized_pnl", 0.0)
            trade_max_upnl = max(trade_max_upnl, upnl_bps)
        else:
            if in_pos:
                trade_rpnl = info.get("realized_pnl", 0.0) - trade_start_rpnl
                if current_side > 0:
                    pnl_long  += trade_rpnl
                else:
                    pnl_short += trade_rpnl

                if trade_max_upnl < 0.5:
                    never_reach_05 += 1

                if trade_max_upnl > 0.1:
                    mfe_ratio_sum += upnl_bps / trade_max_upnl
                    mfe_count     += 1
                    upnl_peak_sum += trade_max_upnl

                in_pos = False
                current_side = 0
                trade_max_upnl = 0.0

        if done:
            reason = info.get("reason", "UNKNOWN")
            done_reasons[reason] = done_reasons.get(reason, 0) + 1
            if step > int(AUDIT_STEPS * 0.85):
                break
            obs = venv.reset()
            in_pos = False
            current_side = 0
            trade_max_upnl = 0.0

    realized_pnl  = last_info.get("realized_pnl", 0.0)
    net_pnl       = realized_pnl - fees_total
    total_exits   = exit_maker + exit_taker
    maker_pct     = exit_maker / total_exits * 100 if total_exits > 0 else 0.0
    pnl_per_trade = net_pnl / trades if trades > 0 else 0.0
    t_per_1k      = trades / steps_done * 1000 if steps_done > 0 else 0.0
    mfe_capture   = mfe_ratio_sum / mfe_count * 100 if mfe_count > 0 else 0.0
    avg_peak      = upnl_peak_sum / mfe_count if mfe_count > 0 else 0.0
    dead_pct      = never_reach_05 / trades * 100 if trades > 0 else 0.0

    venv.close()
    gc.collect()

    # --- print ---
    print(f"  Trades:         {trades:>6}  ({t_per_1k:.1f}/1k steps)")
    print(f"  Net PnL:        {net_pnl:>+8.2f}  (rPnL={realized_pnl:+.2f}, fees={fees_total:.2f})")
    print(f"  PnL/trade:      {pnl_per_trade:>+8.3f}")
    print(f"  Long/Short PnL: {pnl_long:>+8.2f} / {pnl_short:>+8.2f}")
    print(f"  Exit maker%:    {maker_pct:>5.1f}%  ({exit_maker}M / {exit_taker}T)")
    print(f"  MFE capture:    {mfe_capture:>5.1f}%  (avg peak={avg_peak:.2f} bps)")
    print(f"  Dead trades:    {dead_pct:>5.1f}%  (nunca >0.5 bps upnl)")
    print(f"  D1 fallbacks:   {d1_fallbacks:>6}")
    print(f"  Done reasons:   {done_reasons}")

    return dict(
        trades=trades, fees_total=round(fees_total, 4),
        realized_pnl=round(realized_pnl, 4), net_pnl=round(net_pnl, 4),
        pnl_per_trade=round(pnl_per_trade, 5),
        pnl_long=round(pnl_long, 4), pnl_short=round(pnl_short, 4),
        exit_maker=exit_maker, exit_taker=exit_taker,
        maker_pct=round(maker_pct, 2),
        mfe_capture_pct=round(mfe_capture, 2),
        avg_peak_upnl_bps=round(avg_peak, 3),
        dead_trade_pct=round(dead_pct, 2),
        d1_fallbacks=d1_fallbacks,
        trades_per_1k_steps=round(t_per_1k, 2),
        steps_done=steps_done,
        done_reasons=done_reasons,
    )


# ---------------------------------------------------------------------------
# Tabla comparativa
# ---------------------------------------------------------------------------
def print_table(results: dict):
    ok = {k: v for k, v in results.items() if "error" not in v}
    if not ok:
        print("\n[Sin resultados validos para comparar]")
        return

    print("\n\n" + "="*95)
    print(f"{'TABLA COMPARATIVA':^95}")
    print("="*95)
    hdr = (f"{'Modelo':<20} {'Trades':>7} {'T/1k':>5} {'NetPnL':>8} "
           f"{'PnL/tr':>7} {'Maker%':>7} {'MFE%':>6} {'Dead%':>6} {'D1fall':>6}")
    print(hdr)
    print("-"*95)
    for name, s in ok.items():
        print(
            f"{name:<20} {s['trades']:>7} {s['trades_per_1k_steps']:>5.1f}"
            f" {s['net_pnl']:>+8.2f} {s['pnl_per_trade']:>+7.3f}"
            f" {s['maker_pct']:>6.1f}%"
            f" {s['mfe_capture_pct']:>5.1f}%"
            f" {s['dead_trade_pct']:>5.1f}%"
            f" {s['d1_fallbacks']:>6}"
        )
    print("="*95)

    best_pnl = max(ok.items(), key=lambda x: x[1]["net_pnl"])
    best_ppt = max(ok.items(), key=lambda x: x[1]["pnl_per_trade"])
    best_maker = max(ok.items(), key=lambda x: x[1]["maker_pct"])
    lowest_dead = min(ok.items(), key=lambda x: x[1]["dead_trade_pct"])

    print(f"\n  Mejor Net PnL:    {best_pnl[0]:<20} {best_pnl[1]['net_pnl']:>+.2f}")
    print(f"  Mejor PnL/trade:  {best_ppt[0]:<20} {best_ppt[1]['pnl_per_trade']:>+.3f}")
    print(f"  Mejor Maker%:     {best_maker[0]:<20} {best_maker[1]['maker_pct']:.1f}%")
    print(f"  Menos dead trades:{lowest_dead[0]:<20} {lowest_dead[1]['dead_trade_pct']:.1f}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    out_dir = os.path.join(os.path.dirname(__file__), "audit_results")
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(out_dir, f"audit_{ts}.json")

    print(f"[AUDIT] Iniciando auditoria de {len(CANDIDATES)} modelos")
    print(f"[AUDIT] Resultados -> {out_path}\n")

    server_proc = start_server()
    results: dict = {}

    try:
        for cand in CANDIDATES:
            try:
                stats = audit_candidate(cand)
                results[cand["name"]] = stats
            except Exception as exc:
                print(f"\n[ERROR] {cand['name']}: {exc}")
                import traceback; traceback.print_exc()
                results[cand["name"]] = {"error": str(exc)}

            # Guardar incrementalmente por si se interrumpe
            with open(out_path, "w") as f:
                json.dump(results, f, indent=2)

            # Reiniciar servidor entre candidatos para liberar RAM
            server_proc.terminate()
            time.sleep(3)
            server_proc = start_server()
    finally:
        server_proc.terminate()

    print_table(results)
    print(f"\n[GUARDADO] {out_path}")
