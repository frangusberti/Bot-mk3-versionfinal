import os
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
from stable_baselines3 import PPO
from stable_baselines3.common import utils
import argparse

def train_bc():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, default="data/teacher_vnext_100k_alpha.parquet")
    parser.add_argument("--output", type=str, default="models/vnext_bc_p3_5.zip")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch_size", type=int, default=512)
    parser.add_argument("--lr", type=float, default=3e-4)
    args = parser.parse_args()

    print(f"[BC] Loading dataset from {args.input}...")
    df = pd.read_parquet(args.input)
    
    # Obs in Parquet is a list. Convert to 2D numpy array.
    obs = np.array(df["obs"].tolist(), dtype=np.float32)
    actions = df["action"].values.astype(np.int64)
    
    # Calculate normalization stats (mean/std)
    mean = np.mean(obs, axis=0)
    std = np.std(obs, axis=0) + 1e-8
    
    # Normalize
    obs_norm = (obs - mean) / std
    
    X = torch.tensor(obs_norm)
    y = torch.tensor(actions)
    
    dataset = TensorDataset(X, y)
    loader = DataLoader(dataset, batch_size=args.batch_size, shuffle=True)
    
    # Calculate class weights for imbalance
    counts = np.bincount(actions, minlength=10)
    weights = np.zeros(10, dtype=np.float32)
    valid_mask = counts > 0
    weights[valid_mask] = 1.0 / (counts[valid_mask] + 1e-8)
    num_valid = np.sum(valid_mask)
    if weights.sum() > 0:
        weights = weights / weights.sum() * num_valid
        
    class_weights = torch.tensor(weights, dtype=torch.float32)
    
    # Create PPO model
    from stable_baselines3.common.env_util import make_vec_env
    import gymnasium as gym
    
    class DummyEnv(gym.Env):
        def __init__(self):
            super().__init__()
            self.observation_space = gym.spaces.Box(low=-np.inf, high=np.inf, shape=(148,), dtype=np.float32)
            self.action_space = gym.spaces.Discrete(10)
        def reset(self, seed=None): return self.observation_space.sample(), {}
        def step(self, action): return self.observation_space.sample(), 0, False, False, {}

    temp_env = DummyEnv()
    model = PPO("MlpPolicy", temp_env, verbose=1, policy_kwargs=dict(net_arch=dict(pi=[128, 128], vf=[128, 128])))
    
    policy = model.policy
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    policy.to(device)
    class_weights = class_weights.to(device)
    
    optimizer = optim.Adam(policy.parameters(), lr=args.lr)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    
    print(f"[BC] Training on {device} for {args.epochs} epochs with weighted loss...")
    print(f"[BC] Class Weights: {weights}")
    
    for epoch in range(args.epochs):
        epoch_loss = 0
        correct = 0
        total = 0
        for batch_X, batch_y in loader:
            batch_X, batch_y = batch_X.to(device), batch_y.to(device)
            
            distribution = policy.get_distribution(batch_X)
            logits = distribution.distribution.logits
            
            loss = criterion(logits, batch_y)
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            epoch_loss += loss.item()
            _, predicted = torch.max(logits, 1)
            total += batch_y.size(0)
            correct += (predicted == batch_y).sum().item()
        
        acc = 100 * correct / total
        print(f"Epoch {epoch+1}/{args.epochs}, Loss: {epoch_loss/len(loader):.4f}, Acc: {acc:.2f}%")
    
    # Save the model
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    model.save(args.output)
    
    # Save normalization stats in a VecNormalize wrapper for RL compatibility
    from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
    final_venv = VecNormalize(DummyVecEnv([lambda: temp_env]), norm_obs=True, norm_reward=False)
    final_venv.obs_rms.mean = mean
    final_venv.obs_rms.var = std**2
    
    venv_path = args.output.replace(".zip", "_venv.pkl")
    final_venv.save(venv_path)
    
    print(f"[BC] Pre-trained model saved to {args.output}")
    print(f"[BC] Normalization wrapper saved to {venv_path}")

if __name__ == "__main__":
    train_bc()
