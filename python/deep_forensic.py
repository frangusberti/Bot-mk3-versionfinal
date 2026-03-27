import os
import sys
import numpy as np
import pandas as pd
import torch
import json
from collections import deque
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from paper_account import PaperAccount

ACTION_LABELS = [
    "HOLD", "POST_BID", "POST_ASK", "REPRICE_BID", "REPRICE_ASK", "CLEAR_QUOTES", "CLOSE_POSITION"
]

def cast_floats(obj):
    if isinstance(obj, dict):
        return {k: cast_floats(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [cast_floats(v) for v in obj]
    elif isinstance(obj, (np.float32, np.float64, np.float16)):
        return float(obj)
    elif isinstance(obj, (np.int32, np.int64)):
        return int(obj)
    return obj

def run_deep_forensic_audit(model_path, venv_path, dataset_id, steps_per_eval=10000, server="localhost:50051", output_json="deep_forensic_report.json"):
    """Deep evaluation with microstructure extraction."""
    dummy_env = GrpcTradingEnv(
        server_addr=server, 
        dataset_id=dataset_id, 
        symbol="BTCUSDT", 
        fill_model=2,
        reward_maker_fill_bonus=0.0010,
    )
    venv = DummyVecEnv([lambda: dummy_env])
    
    if venv_path and os.path.exists(venv_path):
        venv = VecNormalize.load(venv_path, venv)
        venv.training = False
        venv.norm_reward = False
    
    model = PPO.load(model_path, env=venv)
    model.policy.eval()
    
    trades_log = []
    monitored_fills = []
    
    paper = PaperAccount(initial_balance=10000.0, fixed_notional=1000.0)
    obs = venv.reset()
    
    last_qty = 0.0
    
    for step in range(steps_per_eval):
        # Extract features from obs
        # obs is a numpy array of shape (1, 148)
        raw_obs = obs[0]
        curr_mid = raw_obs[0] # mid_price is index 0 in schema v6
        microprice = raw_obs[27]
        imb1 = raw_obs[24]
        imb3 = raw_obs[25]
        imb10 = raw_obs[26]
        reg_trend = raw_obs[70]
        reg_range = raw_obs[71]
        reg_shock = raw_obs[72]
        reg_dead = raw_obs[73]
        spread_bps = raw_obs[2]
        
        with torch.no_grad():
            obs_t = torch.tensor(obs, dtype=torch.float32).to(model.device)
            action_t, _ = model.predict(obs_t, deterministic=True)
        
        action = int(action_t[0])
        obs, reward, done, info = venv.step(np.array([action]))
        
        info0 = info[0]
        current_ts = info0.get("ts", 0)
        actual_mid = info0.get("mid_price", 0.0)
        
        # 1. Update future targets
        for fill in monitored_fills:
            for delta_ms in [1000, 3000, 5000]:
                if fill['targets'][str(delta_ms)] is None:
                    if current_ts >= fill['ts_fill'] + delta_ms:
                        fill['targets'][str(delta_ms)] = actual_mid
        
        # 2. Capture fills
        if "fills" in info0 and info0["fills"]:
            for f in info0["fills"]:
                side_val = 1.0 if f["side"] == "Buy" else -1.0
                
                # Determine if Open or Close
                curr_qty = info0.get("position_qty", 0.0)
                is_opening = abs(curr_qty) > abs(last_qty)
                
                fill_record = {
                    "id": len(trades_log),
                    "ts_fill": f["ts_event"],
                    "side": f["side"],
                    "price": f["price"],
                    "qty": f["qty"],
                    "mid_at_fill": actual_mid,
                    "microprice_at_fill": microprice,
                    "spread_bps": spread_bps,
                    "imbalance_top1": imb1,
                    "imbalance_top3": imb3,
                    "imbalance_top10": imb10,
                    "inventory_before": last_qty,
                    "inventory_after": curr_qty,
                    "is_opening": is_opening,
                    "regime": {
                        "trend": reg_trend,
                        "range": reg_range,
                        "shock": reg_shock,
                        "dead": reg_dead
                    },
                    "targets": {"1000": None, "3000": None, "5000": None},
                    "spread_capture_bps": ((f["price"] - actual_mid) / actual_mid * 10000) if f["side"] == "Sell" else ((actual_mid - f["price"]) / actual_mid * 10000)
                }
                trades_log.append(fill_record)
                monitored_fills.append(fill_record)
                last_qty = curr_qty
        
        last_qty = info0.get("position_qty", last_qty)
        if done: break
        
    venv.close()
    
    # Final AS calculation for JSON
    for f in trades_log:
        side_mult = 1.0 if f["side"] == "Buy" else -1.0
        for h in ["1000", "3000", "5000"]:
            target_mid = f["targets"][h]
            if target_mid:
                # Signed Adverse Selection: (mid_h - mid_fill) * side
                f[f"as_{h}_bps"] = (target_mid - f["mid_at_fill"]) / f["mid_at_fill"] * 10000 * side_mult
            else:
                f[f"as_{h}_bps"] = 0.0

    output = cast_floats({
        "model": model_path,
        "total_steps": steps_per_eval,
        "trades": trades_log,
    })
    
    with open(output_json, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"Deep Forensic Report saved to {output_json}. Fills: {len(trades_log)}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--venv", type=str, required=True)
    parser.add_argument("--dataset", type=str, default="golden_l2_v1_val")
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--steps", type=int, default=10000)
    args = parser.parse_args()
    
    run_deep_forensic_audit(args.model, args.venv, args.dataset, steps_per_eval=args.steps, output_json=args.out)
