"""
Debug Execution - Trace a single trade attempt (Model 50k)
"""
import sys
import os
import json
import torch
from stable_baselines3 import PPO

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

# Config (Matches Scaling/Calib)
DEBUG_CONFIG = dict(
    close_position_loss_threshold=0.003,
    min_post_offset_bps=0.1,  # Permissive
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    profit_floor_bps=2.0,
    stop_loss_bps=30.0,
    use_selective_entry=False,
    override_action_dim=10,
)

def debug_run():
    model_path = "python/runs_train/phase27_scaling/model_50k.zip"
    if not os.path.exists(model_path):
        print(f"ERROR: Model not found at {model_path}")
        return
        
    model = PPO.load(model_path)
    
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="golden_l2_v1_val",
        symbol="BTCUSDT",
        fill_model=2, # Optimistic
        decision_interval_ms=1000,
        **DEBUG_CONFIG
    )
    
    print(f"\n--- Starting Debug Trace (Model 50k) ---")
    obs, info = env.reset()
    
    for i in range(100):
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(action)
        done = terminated or truncated
        
        print(f"Step {i:02d}: Action={action}")
        print(f"  Pos:      {info.get('position_side', 'FLAT')} {info.get('position_qty', 0.0):.4f}")
        print(f"  Mid:      {info.get('mid_price', 0.0):.2f}")
        
        if info.get('fills'):
            print(f"  SUCCESS: {len(info['fills'])} fills!")
            for f in info['fills']:
                print(f"    - {f['side']} {f['qty']} @ {f['price']} ({f['liquidity']})")
            break
            
        if done:
            print(f"  DONE: Episode ended (reason={info.get('reason')})")
            break

    env.close()

if __name__ == "__main__":
    debug_run()
