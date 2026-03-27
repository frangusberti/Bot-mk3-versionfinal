import os
import sys
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

sys.path.insert(0, os.path.join(os.getcwd(), 'python', 'bot_ml'))
from grpc_env import GrpcTradingEnv

def probe():
    env = GrpcTradingEnv(server_addr="localhost:50051", dataset_id="golden_l2_v1_val")
    obs = env.reset()
    obs, reward, terminated, truncated, info = env.step(0)
    print(f"Info Keys: {info.keys()}")
    if "state" in info:
        print(f"State Keys: {info['state'].keys()}")
        print(f"Mid Price from State: {info['state'].get('mid')}")
    if "features" in info:
        print(f"Features: {info['features']}")
    env.close()

if __name__ == "__main__":
    probe()
