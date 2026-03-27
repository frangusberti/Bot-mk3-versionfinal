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

def run_forensic_audit(model_path, venv_path, dataset_id, steps_per_eval=10000, server="localhost:50051", output_json="forensic_report.json"):
    """Evaluates a model and records every trade detail including post-fill mid prices."""
    dummy_env = GrpcTradingEnv(
        server_addr=server, 
        dataset_id=dataset_id, 
        symbol="BTCUSDT", 
        fill_model=2,
        # Penalties/Bonuses not used for inference but needed for Env init
        reward_maker_fill_bonus=0.0010,
        reward_taker_fill_penalty=0.0005,
        reward_toxic_fill_penalty=0.0010,
        reward_idle_posting_penalty=0.00001,
        reward_distance_to_mid_penalty=0.00001,
        reward_reprice_penalty_bps=0.00005,
        post_delta_threshold_bps=0.05,
    )
    venv = DummyVecEnv([lambda: dummy_env])
    
    if venv_path and os.path.exists(venv_path):
        venv = VecNormalize.load(venv_path, venv)
        venv.training = False
        venv.norm_reward = False
    
    model = PPO.load(model_path, env=venv)
    model.policy.eval()
    
    trades_log = []
    # Fill details being monitored for future prices:
    # { 'id': 0, 'ts_fill': ms, 'side': 'Buy'/'Sell', 'fill_price': float, 'mid_at_fill': float, 'targets': {1000: None, 3000: None, 5000: None} }
    monitored_fills = []
    
    paper = PaperAccount(initial_balance=10000.0, fixed_notional=1000.0)
    obs = venv.reset()
    
    for step in range(steps_per_eval):
        with torch.no_grad():
            obs_t = torch.tensor(obs, dtype=torch.float32).to(model.device)
            action_t, _ = model.predict(obs_t, deterministic=True)
        
        action = int(action_t[0])
        obs, reward, done, info = venv.step(np.array([action]))
        
        info0 = info[0]
        current_mid = info0.get("mid_price", 0.0)
        current_ts = info0.get("ts", 0)
        
        # 1. Update monitored fills with future mid prices
        for fill in monitored_fills:
            for delta_ms in [1000, 3000, 5000]:
                if fill['targets'][delta_ms] is None:
                    if current_ts >= fill['ts_fill'] + delta_ms:
                        fill['targets'][delta_ms] = current_mid
        
        # 2. Record new fills
        if "fills" in info0 and info0["fills"]:
            for f in info0["fills"]:
                fill_record = {
                    "id": len(trades_log),
                    "ts_fill": f["ts_event"],
                    "side": f["side"],
                    "price": f["price"],
                    "qty": f["qty"],
                    "mid_at_fill": current_mid,
                    "is_maker": f.get("is_maker", True),
                    "targets": {1000: None, 3000: None, 5000: None},
                    "spread_capture_bps": ((f["price"] - current_mid) / current_mid * 10000) if f["side"] == "Sell" else ((current_mid - f["price"]) / current_mid * 10000)
                }
                trades_log.append(fill_record)
                monitored_fills.append(fill_record)
        
        paper.step(current_mid)
        if done: break
        
    venv.close()
    
    # Save output
    output = {
        "model": model_path,
        "total_steps": steps_per_eval,
        "trades": trades_log,
        "summary": paper.get_report()
    }
    
    with open(output_json, "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"Forensic Report saved to {output_json}. Fills captured: {len(trades_log)}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--venv", type=str, required=True)
    parser.add_argument("--dataset", type=str, default="golden_l2_v1_val")
    parser.add_argument("--out", type=str, required=True)
    parser.add_argument("--steps", type=int, default=10000)
    args = parser.parse_args()
    
    run_forensic_audit(args.model, args.venv, args.dataset, steps_per_eval=args.steps, output_json=args.out)
