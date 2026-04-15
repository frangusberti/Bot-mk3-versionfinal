
import os
import sys
import numpy as np
from typing import Dict, List
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

# Add paths
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(os.path.join(os.path.dirname(__file__), 'bot_ml'))
from bot_ml.grpc_env import GrpcTradingEnv

def mask_fn(env: GrpcTradingEnv) -> np.ndarray:
    return env.action_masks()

def run_deterministic_audit(name: str, config_patch: Dict, steps: int = 50000):
    print(f"\n{'='*60}")
    print(f" AUDIT: {name}")
    print(f"{'='*60}")
    
    # Single 50k window for continuity
    manifest_start = 1773862066776
    warmup_ms = 6 * 3600 * 1000 
    start_ts = manifest_start + warmup_ms
    
    model_path = "python/runs_train/itr_causal_chk/model_chk_100000.zip"
    venv_path = "python/runs_train/itr_causal_chk/venv_chk_100000.pkl"
    
    base_config = {
        "dataset_id": "golden_l2_v1_train",
        "symbol": "BTCUSDT",
        "decision_interval_ms": 100,
        "max_pos_frac": 0.05,
        "initial_equity": 10000.0,
        "fill_model": 2,
        "use_selective_entry": False,
        "reward_thesis_decay_weight": 0.0001,
        "random_start_offset": False,
        "micro_strict": False,
        "start_ts": start_ts
    }
    base_config.update(config_patch)
    
    model = MaskablePPO.load(model_path)
    
    def make_env():
        return ActionMasker(GrpcTradingEnv(server_addr="localhost:50051", **{k:v for k,v in base_config.items() if k not in ["dataset_id", "symbol"]}), mask_fn)
        
    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False
    venv.norm_reward = False
    
    obs = venv.reset()
    
    all_trades = []
    current_trade = None
    
    action_counts = {"HOLD":0, "OPEN":0, "ADD":0, "REDUCE":0, "CLOSE":0}
    
    last_realized = 0.0
    
    for i in range(steps):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, done, info_list = venv.step(action)
        info = info_list[0]
        
        if i == 0:
            print(f"DEBUG Info Keys: {list(info.keys())}")
        if i % 1000 == 0 and info.get("position_qty", 0.0) != 0:
            print(f"DEBUG Step {i}: pos={info.get('position_qty')}, upnl={info.get('unrealized_pnl')}, equity={info.get('equity')}")
        
        # Action Tracking
        act_idx = int(action[0])
        act_name = ["HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG", "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE"][act_idx]
        if "OPEN" in act_name: action_counts["OPEN"] += 1
        elif "ADD" in act_name: action_counts["ADD"] += 1
        elif "REDUCE" in act_name: action_counts["REDUCE"] += 1
        elif "CLOSE" in act_name: action_counts["CLOSE"] += 1
        else: action_counts["HOLD"] += 1
        
        pos_qty = info.get("position_qty", 0.0)
        equity = info.get("equity", 10000.0)
        upnl = info.get("unrealized_pnl", 0.0)
        upnl_bps = upnl / equity * 10000.0
        realized = info.get("realized_pnl", 0.0)
        
        if abs(pos_qty) > 1e-8:
            if current_trade is None:
                current_trade = {
                    "entry_ts": info.get("ts"),
                    "max_upnl_bps": upnl_bps,
                    "unlocked": False,
                    "entry_equity": equity
                }
            else:
                current_trade["max_upnl_bps"] = max(current_trade["max_upnl_bps"], upnl_bps)
                if current_trade["max_upnl_bps"] >= 5.0:
                    current_trade["unlocked"] = True
        else:
            if current_trade is not None:
                # Trade closed
                pnl_delta = realized - last_realized
                current_trade["realized_bps"] = pnl_delta / current_trade["entry_equity"] * 10000.0
                all_trades.append(current_trade)
                current_trade = None
        
        last_realized = realized
        
        if done: break
        
    num_t = len(all_trades)
    if num_t > 0:
        avg_mfe = np.mean([t["max_upnl_bps"] for t in all_trades])
        # Capture ratio = realized / mfe (if mfe > 0)
        captures = []
        for t in all_trades:
            if t["max_upnl_bps"] > 0.1:
                captures.append(t["realized_bps"] / t["max_upnl_bps"])
        avg_capture = np.mean(captures) if captures else 0.0
        reach_2 = len([t for t in all_trades if t["max_upnl_bps"] >= 2.0]) / num_t
        reach_4 = len([t for t in all_trades if t["max_upnl_bps"] >= 4.0]) / num_t
        reach_6 = len([t for t in all_trades if t["max_upnl_bps"] >= 6.0]) / num_t
        unlocked_pct = len([t for t in all_trades if t["unlocked"]]) / num_t
    else:
        avg_mfe = avg_capture = reach_2 = reach_4 = reach_6 = unlocked_pct = 0.0

    print(f"\n--- {name} SCORECARD ---")
    print(f"Total Trades:      {num_t}")
    print(f"Net PnL:           {info.get('realized_pnl', 0.0) - info.get('fees_paid', 0.0):.4f}")
    print(f"Realized PnL:      {info.get('realized_pnl', 0.0):.4f}")
    print(f"Fees Paid:         {info.get('fees_paid', 0.0):.4f}")
    print(f"MFE Capture Ratio: {avg_capture:.2%}")
    print(f"Avg MFE:           {avg_mfe:.2f} bps")
    print(f"Reach +2/+4/+6:    {reach_2*100:.1f}% / {reach_4*100:.1f}% / {reach_6*100:.1f}%")
    
    # Rust counters
    rust_acts = info.get('action_counts', {})
    print(f"CLOSE Usage:       {rust_acts.get('CLOSE_LONG', 0) + rust_acts.get('CLOSE_SHORT', 0)}")
    print(f"REDUCE Usage:      {rust_acts.get('REDUCE_LONG', 0) + rust_acts.get('REDUCE_SHORT', 0)}")
    print(f"REDUCE_to_FLAT:    {rust_acts.get('REDUCE_TO_FLAT', 0)}")
    print(f"Blocked Partial:   {rust_acts.get('BLOCKED_PARTIAL_REDUCE', 0)}")
    print(f"Blocked Full:      {rust_acts.get('BLOCKED_FULL_CLOSE', 0)}")
    
    print(f"Flat at Done:      {abs(pos_qty) < 1e-8}")
    print(f"Dangling Size:     {pos_qty:.6f}")
    print(f"Reason:            {info.get('reason')}")
    if current_trade:
        print(f"Dangling Trade:    Open with max_upnl={current_trade['max_upnl_bps']:.2f}")

    return {"name": name, "net": info.get('realized_pnl', 0.0) - info.get('fees_paid', 0.0), "capture": avg_capture}

if __name__ == "__main__":
    r_a = run_deterministic_audit("A) Baseline (Rigid Gate)", {"use_winner_unlock": False, "profit_floor_bps": 0.5})
    r_b = run_deterministic_audit("B) Winner-Unlock (Flexible Gate)", {"use_winner_unlock": True, "profit_floor_bps": 0.5})
    r_c = run_deterministic_audit("C) Zero Floor (Gate Off)", {"use_winner_unlock": False, "profit_floor_bps": 0.0})
    
    print("\nFINAL SUMMARY")
    print(f"A: Net {r_a['net']:.4f}, Capture {r_a['capture']:.2%}")
    print(f"B: Net {r_b['net']:.4f}, Capture {r_b['capture']:.2%}")
    print(f"C: Net {r_c['net']:.4f}, Capture {r_c['capture']:.2%}")
