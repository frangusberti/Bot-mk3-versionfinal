import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

def debug():
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_eval",
        symbol="BTCUSDT"
    )
    
    obs, info = env.reset()
    print(f"Observation shape: {obs.shape}")
    
    # Check first 5 steps, print all non-zero values
    for i in range(5):
        print(f"\n--- STEP {i} ---")
        values = obs[:74]
        masks = obs[74:]
        for j in range(74):
            if values[j] != 0 or masks[j] != 0:
                print(f"Index {j:2d}: value={values[j]:.6f}, mask={masks[j]}")
        
        obs, r, term, trunc, info = env.step(0)
        if term or trunc: break

if __name__ == "__main__":
    debug()
