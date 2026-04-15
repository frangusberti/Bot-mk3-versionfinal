"""
Extended Capture Audit (+100k Checkpoint)
=========================================
Runs a large sample (100k steps) to accumulate 30-50 trades.
Calculates MFE Capture Ratio and lifecycle efficiency.
"""
import os
import sys
import torch
import numpy as np
from collections import Counter
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from sb3_contrib.common.wrappers import ActionMasker

CONFIG = dict(
    dataset_id="golden_l2_v1_train",
    symbol="BTCUSDT",
    random_start_offset=True,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    fill_model=2,
)

def mask_fn(env: GrpcTradingEnv) -> np.ndarray:
    return env.action_masks()

def run_extended_audit(model, env, steps=100000):
    env.training = False
    obs = env.reset()
    
    current_trade = None
    all_trades = []
    
    # Economics
    last_realized_pnl = 0.0
    last_fees_paid = 0.0
    sum_win_hold = 0.0
    sum_loss_hold = 0.0
    hold_count = 0
    pnl_per_side = Counter()
    
    # Actions
    action_counts = Counter()
    reduce_to_flat = 0
    reduce_total = 0
    step_limit_pnl_positive = 0

    for i in range(steps):
        masks = env.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        act_int = int(action[0])
        action_counts[act_int] += 1
        
        pos_qty_before = env.get_attr("unwrapped")[0]._last_info.get("position_qty", 0.0) if hasattr(env.get_attr("unwrapped")[0], '_last_info') else 0.0
        
        obs, reward, done, info = env.step(action)
        info0 = info[0]
        env.get_attr("unwrapped")[0]._last_info = info0
        
        mid = info0['mid_price']
        pos_qty = info0['position_qty']
        entry_price = info0['entry_price']
        
        if act_int in {3, 7}:
            reduce_total += 1
            if abs(pos_qty) < 1e-9:
                reduce_to_flat += 1

        if abs(pos_qty) > 1e-9:
            if current_trade is None:
                current_trade = {
                    "side": "LONG" if pos_qty > 0 else "SHORT",
                    "entry_price": entry_price,
                    "mfe_bps": 0.0,
                    "mae_bps": 0.0,
                    "realized_bps": 0.0,
                    "thresholds": {0.5: False, 2.0: False, 4.0: False, 6.0: False},
                    "max_threshold": 0.0
                }
            
            pnl_bps = (mid - current_trade["entry_price"]) / current_trade["entry_price"] * 10000.0 if current_trade["side"] == "LONG" else (current_trade["entry_price"] - mid) / current_trade["entry_price"] * 10000.0
            current_trade["mfe_bps"] = max(current_trade["mfe_bps"], pnl_bps)
            current_trade["mae_bps"] = min(current_trade["mae_bps"], pnl_bps)
            
            for t in [0.5, 2.0, 4.0, 6.0]:
                if current_trade["mfe_bps"] >= t:
                    current_trade["thresholds"][t] = True
                    current_trade["max_threshold"] = max(current_trade["max_threshold"], t)
        else:
            if current_trade is not None:
                # Close trade
                pnl = (mid - current_trade["entry_price"]) / current_trade["entry_price"] * 10000.0 if current_trade["side"] == "LONG" else (current_trade["entry_price"] - mid) / current_trade["entry_price"] * 10000.0
                current_trade["realized_bps"] = pnl
                all_trades.append(current_trade)
                
                pnl_per_side[current_trade["side"]] += pnl
                current_trade = None

        last_realized_pnl = info0.get("realized_pnl", last_realized_pnl)
        last_fees_paid = info0.get("fees_paid", last_fees_paid)
        
        w = info0.get("avg_win_hold_ms", 0)
        l = info0.get("avg_loss_hold_ms", 0)
        if w > 0 or l > 0:
            sum_win_hold += w
            sum_loss_hold += l
            hold_count += 1

        if done:
            if current_trade:
                # Episode limit reached while in position
                pnl = (mid - current_trade["entry_price"]) / current_trade["entry_price"] * 10000.0 if current_trade["side"] == "LONG" else (current_trade["entry_price"] - mid) / current_trade["entry_price"] * 10000.0
                if pnl > 0:
                    step_limit_pnl_positive += 1
                current_trade["realized_bps"] = pnl
                all_trades.append(current_trade)
                current_trade = None

    return {
        "trades": all_trades,
        "realized_pnl": last_realized_pnl,
        "fees_total": last_fees_paid,
        "pnl_per_side": pnl_per_side,
        "avg_win_hold": sum_win_hold / max(hold_count, 1),
        "avg_loss_hold": sum_loss_hold / max(hold_count, 1),
        "action_counts": action_counts,
        "reduce_to_flat": reduce_to_flat,
        "reduce_total": reduce_total,
        "step_limit_pnl_positive": step_limit_pnl_positive
    }

def main():
    model_path = "python/runs_train/itr_causal_chk/model_chk_100000.zip"
    venv_path = "python/runs_train/itr_causal_chk/venv_chk_100000.pkl"
    
    if not os.path.exists(model_path):
        print("Checkpoint not found.")
        return

    model = MaskablePPO.load(model_path)
    def make_env():
        return ActionMasker(GrpcTradingEnv(server_addr="localhost:50051", **CONFIG), mask_fn)
    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(venv_path, venv)
    
    print("[EXTENDED AUDIT] Running 100,000 steps...")
    res = run_extended_audit(model, venv, steps=100000)
    
    trades = res['trades']
    num_t = len(trades)
    if num_t == 0:
        print("No trades executed.")
        return

    avg_mfe = np.mean([t['mfe_bps'] for t in trades])
    avg_mae = np.mean([t['mae_bps'] for t in trades])
    avg_realized = np.mean([t['realized_bps'] for t in trades])
    
    # Capture ratio (only for trades that reach at least 0.5 bps)
    profitable_trades = [t for t in trades if t['mfe_bps'] >= 0.5]
    capture_ratios = [t['realized_bps'] / t['mfe_bps'] for t in profitable_trades if t['mfe_bps'] > 0]
    avg_capture = np.mean(capture_ratios) if capture_ratios else 0.0

    print("\n" + "="*60)
    print("A) EVALUACION EXTENDIDA (+100k CHECKPOINT)")
    print("="*60)
    print(f"Total Trades:      {num_t}")
    print(f"Realized PnL:      {res['realized_pnl']:.4f}")
    print(f"Net PnL:           {res['realized_pnl'] - res['fees_total']:.4f}")
    print(f"Fees Total:        {res['fees_total']:.4f}")
    print(f"PnL by Side:       LONG={res['pnl_per_side']['LONG']:.2f}, SHORT={res['pnl_per_side']['SHORT']:.2f} bps")
    print(f"Avg Hold Win/Loss: {res['avg_win_hold']:.0f} / {res['avg_loss_hold']:.0f} ms")

    print("\n" + "="*60)
    print("B) AUDITORIA DE EFICIENCIA DE SALIDA")
    print("="*60)
    print(f"Avg MFE:            {avg_mfe:.2f} bps")
    print(f"Avg MAE:            {avg_mae:.2f} bps")
    print(f"Avg Realized (bps): {avg_realized:.2f} bps")
    print(f"MFE Capture Ratio:  {avg_capture*100:.1f}%")
    
    for thr in [0.5, 2.0, 4.0, 6.0]:
        reached = sum(1 for t in trades if t['thresholds'][thr])
        closed_below = sum(1 for t in trades if t['thresholds'][thr] and t['realized_bps'] < thr)
        pct_reached = reached/num_t*100
        pct_below = (closed_below/reached*100) if reached > 0 else 0
        print(f"Reach +{thr} bps:     {reached} ({pct_reached:.1f}%) | Closed Below: {pct_below:.1f}%")

    print("\n" + "="*60)
    print("C) AUDITORIA DE LIFECYCLE")
    print("="*60)
    counts = res['action_counts']
    reduce_total = counts[3] + counts[7]
    close_total = counts[4] + counts[8]
    print(f"REDUCE Usage:      {reduce_total} clicks")
    print(f"CLOSE Usage:       {close_total} clicks")
    print(f"REDUCE to FLAT:    {res['reduce_to_flat']} ({res['reduce_to_flat']/(reduce_total or 1)*100:.1f}%)")
    print(f"Step Limit +PnL:   {res['step_limit_pnl_positive']} (Trades that timed out profitable)")
    
    # Verdict on Capture
    if avg_capture < 0.3:
        print("\nVEREDICTO: Captura POBRE. Estamos devolviendo >70% del MFE.")
    elif avg_capture < 0.6:
        print("\nVEREDICTO: Captura MODERADA. Se puede optimizar la salida.")
    else:
        print("\nVEREDICTO: Captura SOLIDA.")

if __name__ == "__main__":
    main()
