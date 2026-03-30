import os
import sys
import numpy as np
import pandas as pd
import argparse
from tqdm import tqdm

# Ensure paths are correct for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

def teacher_policy(obs, position, holding_s, info=None):
    # Signals from observation (Sprint 2 Schema v6)
    mid = obs[0]
    spread_bps = obs[2]
    ret_1s = obs[4]
    ret_5s = obs[6]
    # Taker flow (14..23) is often missing in golden_l2; use Book Imbalance (D)
    imb_1s = obs[24] # obi_top1
    imb_5s = obs[24] # fallback to top1 for rapid calibration
    microprice = obs[27]
    pnl_pct = obs[49] # latent_pnl_pct
    
    # Feature Health (from info)
    health = info.get("feature_health", {}) if info else {}
    obs_quality = health.get("obs_quality", 1.0)
    book_age = health.get("book_age_ms", 0)
    
    eps = 1e-9
    action = 0
    
    # Missing Microprice Fallback (if masked to 0.0, treat as mid)
    eff_micro = microprice if microprice > 0 else mid
    
    # Global Filters
    if obs_quality < 0.99 or book_age > 100 or spread_bps >= 10.0:
        if position != 0:
            return 6 # CLOSE_POSITION
        return 0
    
    if position != 0:
        pnl_bps = pnl_pct * 10000.0
        
        # EXIT RULES
        if pnl_bps >= 4.0: return 6 
        if pnl_bps <= -2.5: return 6
        if holding_s >= 20: return 6 
        
        if position > 0: # Long
            if imb_1s < -0.05 or eff_micro < mid:
                return 6 
        else: # Short
            if imb_1s > 0.05 or eff_micro > mid:
                return 6 
    else:
        # ENTRY RULES (Book Imbalance only for BTCUSDT)
        
        # LONG ENTRY
        if (imb_5s > 0.08 and 
            eff_micro >= mid):
            return 1 # POST_BID
            
        # SHORT ENTRY
        if (imb_5s < -0.08 and 
            eff_micro <= mid):
            return 2 # POST_ASK
            
    return action

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=100000)
    parser.add_argument("--output", type=str, default="data/teacher_dataset.parquet")
    parser.add_argument("--dataset_id", type=str, default="stage2_train")
    args = parser.parse_args()

    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id=args.dataset_id,
        symbol="BTCUSDT",
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
        random_start_offset=True
    )

    data = []
    
    obs, info = env.reset()
    position = 0
    holding_s = 0
    
    print(f"[TEACHER] Generating {args.steps} steps of expert data...")
    for i in tqdm(range(args.steps)):
        
        # Decision Logic (Teacher V2.0 Design)
        action = teacher_policy(obs, position, holding_s, info)
                
        # Store state-action pair
        data.append({
            "obs": obs.tolist(),
            "action": action,
            "ts": info.get("ts", 0)
        })
        
        # Step environment
        obs, reward, terminated, truncated, info = env.step(action)
        
        # Update tracking
        pos_side = info.get("position_side", "FLAT")
        pos_qty = info.get("position_qty", 0.0)
        
        new_pos = 0
        if abs(pos_qty) > 1e-9:
            new_pos = 1 if pos_side == "LONG" else -1
            
        if new_pos != 0 and position == 0:
            holding_s = 0
        elif new_pos != 0:
            holding_s += 1
        else:
            holding_s = 0
        position = new_pos
        
        if terminated or truncated:
            obs, info = env.reset()
            position = 0
            holding_s = 0

    # Save to Parquet
    df = pd.DataFrame(data)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    df.to_parquet(args.output)
    print(f"[TEACHER] Dataset saved to {args.output} ({len(df)} rows)")

if __name__ == "__main__":
    main()
