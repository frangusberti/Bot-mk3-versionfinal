import os
import sys
import numpy as np
import pandas as pd
import argparse
from tqdm import tqdm

# Ensure paths are correct for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from teacher_dataset_generator import teacher_policy

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=100000)
    parser.add_argument("--output", type=str, default="data/teacher_vnext_100k_alpha.parquet")
    parser.add_argument("--dataset_id", type=str, default="golden_l2_v1_train") # Phase 3.5 base
    parser.add_argument("--min_offset", type=float, default=0.20)
    parser.add_argument("--server", type=str, default="localhost:50051")
    args = parser.parse_args()

    env = GrpcTradingEnv(
        server_addr=args.server,
        dataset_id=args.dataset_id,
        symbol="BTCUSDT",
        fill_model=0, # Conservative (Stability over density)
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
        random_start_offset=True
    )

    data = []
    
    obs, info = env.reset()
    position = 0
    holding_s = 0
    
    # We need to simulate the Rust-side synthetic price logic to ensure 0.3 bps compliance
    # Let's use a simpler heuristic: if the teacher wants to POST, we check if the 
    # current observation's spread/vol implies an offset >= min_offset.
    
    print(f"[TEACHER_VNEXT] Generating {args.steps} steps of gate-aligned expert data...")
    print(f"Targeting min_offset: {args.min_offset} bps")
    
    blocked_count = 0
    
    for i in tqdm(range(args.steps)):
        # Update tracking before decision for accurate state
        pos_qty = info.get("position_qty", 0.0)
        pos_side = info.get("position_side", "FLAT")
        position = 1 if pos_side == "LONG" else (-1 if pos_side == "SHORT" else 0)

        # Decision Logic (Inherit from V2.0 Expert)
        raw_action = teacher_policy(obs, position, holding_s, info)
        
        # Lifecycle Mapping (vNext 10-action)
        action = 0 # HOLD
        if raw_action == 1: # POST_BID intent
            action = 1 if position == 0 else 2 # OPEN_LONG vs ADD_LONG
        elif raw_action == 2: # POST_ASK intent
            action = 5 if position == 0 else 6 # OPEN_SHORT vs ADD_SHORT
        elif raw_action == 6: # CLOSE intent
            action = 4 if position > 0 else 8 # CLOSE_LONG vs CLOSE_SHORT

        # Gate Enforcement: If action is POST, check if it's statistically likely to pass 0.3 bps
        if action in [1, 2, 5, 6]: # Entry or Add actions
            # Approximate the Rust synthetic offset: (spread*0.5).max(0.2) + (vol*1.5)
            spread_bps = obs[2]
            vol_5s = obs[12] # rv_5s in v6 schema
            
            est_offset = max(0.2, spread_bps * 0.5) + (vol_5s * 1.5)
            
            if est_offset < args.min_offset:
                action = 0 # Force HOLD to keep the expert data clean/executable
                blocked_count += 1
                
        # Store state-action pair
        data.append({
            "obs": obs.tolist(),
            "action": action,
            "ts": info.get("ts", 0)
        })
        
        # Step environment
        obs, reward, terminated, truncated, info = env.step(action)
        
        if terminated or truncated:
            reason = info.get("reason", "UNKNOWN")
            print(f"\n[TEACHER] Episode ended at step {i} (len={holding_s}). Reason: {reason}")
            obs, info = env.reset()
            position = 0
            holding_s = 0
            continue

        # Update holding time
        if position != 0:
            holding_s += 1
        else:
            holding_s = 0

    # Save to Parquet
    df = pd.DataFrame(data)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    df.to_parquet(args.output)
    print(f"[TEACHER_VNEXT] Dataset saved to {args.output} ({len(df)} rows)")
    print(f"[TEACHER_VNEXT] Actions filtered due to min_offset: {blocked_count}")

if __name__ == "__main__":
    main()
