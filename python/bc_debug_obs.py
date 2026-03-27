import os
import sys
import argparse
import pandas as pd
import numpy as np
import torch
from stable_baselines3 import PPO

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

def debug_obs(model_path, dataset_id, server="localhost:50051"):
    print(f"\n=== OBS DEBUG ({dataset_id}) ===")
    env = GrpcTradingEnv(server_addr=server, dataset_id=dataset_id, symbol="BTCUSDT")
    model = PPO.load(model_path)
    
    obs, info = env.reset()
    for i in range(10):
        # Print first 10 dims of obs
        print(f"Step {i} Obs[:10]: {obs[:10]}")
        
        with torch.no_grad():
            obs_t = torch.tensor(obs, dtype=torch.float32).unsqueeze(0).to(model.device)
            logits = model.policy.get_distribution(obs_t).distribution.logits
            probs = torch.softmax(logits, dim=1).cpu().numpy()[0]
            action = np.argmax(probs)
            print(f"  Probs: {probs[:3]} -> Action: {action}")
            
        obs, r, term, trunc, info = env.step(action)
        if term or trunc: break
    
    env.close()

if __name__ == "__main__":
    debug_obs(sys.argv[1], "golden_l2_v1_val")
