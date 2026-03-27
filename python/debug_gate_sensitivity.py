import os
import sys
import numpy as np
import pandas as pd
from tqdm import tqdm

# Ensure paths are correct for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from teacher_dataset_generator import teacher_policy

def test_gate_sensitivity(steps=5000, thresholds=[0.1, 0.15, 0.2, 0.25, 0.3]):
    print(f"[DIAGNOSTIC] Testing Gate Sensitivity over {steps} steps...")
    
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
        random_start_offset=True
    )

    obs, info_raw = env.reset()
    position = 0
    holding_s = 0
    
    results = {t: {"blocked": 0, "allowed": 0} for t in thresholds}
    total_post_attempts = 0

    for i in tqdm(range(steps)):
        info = info_raw.get("step_info", {}) if info_raw else {}
        action = teacher_policy(obs, position, holding_s, info)
        
        if action in [1, 2]: # POST_BID, POST_ASK
            total_post_attempts += 1
            # Approximate the Rust synthetic offset: (spread*0.5).max(0.2) + (vol*1.5)
            spread_bps = obs[2]
            vol_5s = obs[12]
            est_offset = max(0.2, spread_bps * 0.5) + (vol_5s * 1.5)
            
            for t in thresholds:
                if est_offset < t:
                    results[t]["blocked"] += 1
                else:
                    results[t]["allowed"] += 1
        
        # Step environment (always use teacher action to maintain sequence, 
        # even if it would be blocked in real run, we just want stats)
        obs, reward, terminated, truncated, info_raw = env.step(action)
        
        if terminated or truncated:
            obs, info_raw = env.reset()
            position = 0
            holding_s = 0

    print("\n[DIAGNOSTIC RESULTS] - Teacher POST Attempts:", total_post_attempts)
    for t in thresholds:
        allowed = results[t]["allowed"]
        blocked = results[t]["blocked"]
        pass_rate = (allowed / total_post_attempts * 100) if total_post_attempts > 0 else 0
        print(f"  Threshold {t:.2f} bps: Allowed={allowed} | Blocked={blocked} | Pass Rate={pass_rate:.1f}%")

if __name__ == "__main__":
    test_gate_sensitivity()
