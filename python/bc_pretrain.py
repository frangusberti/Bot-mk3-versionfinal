import os
import argparse
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

def pretrain_bc(model, dataset_path, epochs=20, batch_size=256, lr=1e-3):
    print(f"[BC] Loading dataset: {dataset_path}")
    df = pd.read_parquet(dataset_path)
    
    # Filter valid actions
    df = df[df.action < 7]
    
    obs_batch = np.stack(df['obs_vec'].values).astype(np.float32)
    actions_batch = df['action'].values.astype(np.int64)
    
    print(f"[BC] Extracted {len(obs_batch)} valid rows. Action dist (Target):")
    print((df.action_label.value_counts(normalize=True) * 100).round(2))
    
    device = model.device
    
    obs_t = torch.tensor(obs_batch, dtype=torch.float32).to(device)
    act_t = torch.tensor(actions_batch, dtype=torch.long).to(device)
    
    dataset = TensorDataset(obs_t, act_t)
    dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    
    # Optimize policy parameters. PPO uses policy.parameters()
    optimizer = torch.optim.Adam(model.policy.parameters(), lr=lr)
    
    model.policy.train()
    
    print("\n[BC] Starting Behavior Cloning pretraining...")
    for epoch in range(epochs):
        epoch_losses = []
        for obs_b, act_b in dataloader:
            # Normalize observations using the VecNormalize wrapper inside the model
            obs_b_np = obs_b.cpu().numpy()
            obs_b_norm = model.get_vec_normalize_env().normalize_obs(obs_b_np)
            obs_b_norm_t = torch.tensor(obs_b_norm, dtype=torch.float32).to(device)
            
            # evaluate_actions returns values, log_prob, entropy
            # We maximize the log_prob of the generated Teacher actions
            _, log_prob, entropy = model.policy.evaluate_actions(obs_b_norm_t, act_b)
            
            # Loss is negative log probability + a slight entropy bonus to stay explorative
            loss = -log_prob.mean() - 0.01 * entropy.mean()
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            epoch_losses.append(loss.item())
            
        print(f"[BC] Epoch {epoch+1}/{epochs} - Loss: {np.mean(epoch_losses):.4f}")
        
    print("[BC] Pretraining complete.")
    return model

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=str, required=True, help="Path to BC parquet dataset")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch_size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=5e-4) # Moderate LR for initialization
    parser.add_argument("--server", type=str, default="localhost:50051")
    parser.add_argument("--out", type=str, default="python/runs_train/pilot_bc")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    
    print("\n=== BEHAVIOR CLONING PRETRAINING ===")
    
    # Needs a dummy env to initialize PPO dimensions correctly
    print("[BC] Connecting to grpc server to initialize observation space...")
    env = GrpcTradingEnv(
        server_addr=args.server,
        dataset_id="golden_l2_v1_train",
        symbol="BTCUSDT",
    )
    vec_env = DummyVecEnv([lambda: env])
    
    # Wrap in VecNormalize
    print("[BC] Applying VecNormalize...")
    vec_env = VecNormalize(vec_env, norm_obs=True, norm_reward=False, clip_obs=10.)
    
    # Initialize PPO with same architecture
    print("[BC] Initializing PPO policy network...")
    model = PPO(
        "MlpPolicy",
        vec_env,
        verbose=0,
        ent_coef=0.01,
        learning_rate=args.lr, 
        batch_size=args.batch_size,
    )
    
    # Pre-warm VecNormalize with the dataset to get sane stats
    df = pd.read_parquet(args.dataset)
    obs_batch = np.stack(df['obs_vec'].values).astype(np.float32)
    print(f"[BC] Warm-starting normalization with {len(obs_batch)} samples...")
    # NOTE: We can't easily push manual data into VecNormalize's running stats 
    # but we can wrap the training loop to normalize inputs using a simple scaler or manual stats if needed.
    # Actually, VecNormalize will update during manual training if we use it correctly.

    # Run BC loop
    model = pretrain_bc(
        model, 
        dataset_path=args.dataset,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr
    )
    
    save_path = os.path.join(args.out, "bc_pretrained_model")
    model.save(save_path)
    vec_env.save(os.path.join(args.out, "vec_normalize.pkl"))
    print(f"\n[BC] Saved pretrained model to {save_path}.zip")
    print(f"[BC] Saved normalization stats to {args.out}/vec_normalize.pkl")

if __name__ == "__main__":
    main()
