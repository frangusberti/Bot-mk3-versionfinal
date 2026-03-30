import torch
import numpy as np
import pandas as pd
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv
import os
import sys

# Ensure paths are correct for imports
sys.path.insert(0, os.path.join(os.getcwd(), 'python'))
from bot_ml.grpc_env import GrpcTradingEnv

def get_probs(model_path, obs_sample):
    model = PPO.load(model_path)
    obs_tensor = torch.as_tensor(obs_sample).float().unsqueeze(0)
    with torch.no_grad():
        dist = model.policy.get_distribution(obs_tensor)
        probs = dist.distribution.probs.numpy()[0]
    return probs

# Create a dummy FLAT observation (148-dim)
# All zeros, except mid_price=100.0, mask=1.0 for mid_price (74), pos_flag=0.0 (48), mask=1.0 for pos (122)
obs = np.zeros(148)
obs[0] = 100.0   # mid price
obs[74] = 1.0    # mid mask
obs[48] = 0.0    # flat
obs[122] = 1.0   # pos mask

print("--- Logits Audit: Action Distribution (FLAT State) ---")

# BC Model (Wait, I need to know where the BC model is saved. I'll use the one I just trained)
bc_path = "models/vnext_bc_fix.zip"
if os.path.exists(bc_path):
    bc_probs = get_probs(bc_path, obs)
    print(f"\nBC Model ({bc_path}):")
    for i, p in enumerate(bc_probs):
        print(f"Action {i}: {p:.4f}")
else:
    print(f"BC model not found at {bc_path}")

# Degenerate PPO Model
ppo_path = "python/runs_train/vnext_p3_5/model_50k.zip"
if os.path.exists(ppo_path):
    ppo_probs = get_probs(ppo_path, obs)
    print(f"\nPPO Model ({ppo_path}):")
    for i, p in enumerate(ppo_probs):
        print(f"Action {i}: {p:.4f}")
else:
    print(f"PPO model not found at {ppo_path}")

action_names = [
    "HOLD", "OPEN_L", "ADD_L", "RED_L", "CLOSE_L",
    "OPEN_S", "ADD_S", "RED_S", "CLOSE_S", "REPRICE"
]

print("\nTop 3 Actions (FLAT):")
if os.path.exists(bc_path):
    top_bc = np.argsort(bc_probs)[-3:][::-1]
    print(f"BC: {[action_names[i] for i in top_bc]}")
if os.path.exists(ppo_path):
    top_ppo = np.argsort(ppo_probs)[-3:][::-1]
    print(f"PPO: {[action_names[i] for i in top_ppo]}")
