"""
PPO vNext Phase 2 - Exploration Bridge
=======================================
1. Behavior Cloning (BC) pre-training on expert data.
2. Staged Curriculum for min_post_offset_bps (0.15 -> 0.30).
3. Decaying Micro-Proxy (quote_presence_bonus -> 0).
"""
import os
import sys
import argparse
import json
import torch
import numpy as np
import pandas as pd
from torch.utils.data import DataLoader, TensorDataset
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

# -- Phase 2 Config --
P2_CONFIG = dict(
    # Hard Gates (Layer 3)
    close_position_loss_threshold=0.003,  
    min_post_offset_bps=0.15,             # Start lenient (Curriculum)
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,

    # Simplified Reward + Micro-Proxy
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_quote_presence_bonus=0.0001,   # Micro-proxy (Decaying)

    # Legacy (Zeroed)
    reward_maker_fill_bonus=0.0,
    reward_taker_fill_penalty=0.0,
)

class Phase2Callback(BaseCallback):
    """Callback for Gating Curriculum and Proxy Decay."""
    
    def __init__(self, target_offset=0.30, total_steps=300_000, verbose=0):
        super().__init__(verbose)
        self.start_offset = 0.15
        self.target_offset = target_offset
        self.total_steps = total_steps
        self.start_bonus = 0.0001
        
    def _on_step(self) -> bool:
        step = self.num_timesteps
        
        # 1. Update Offset Curriculum (0.15 -> 0.30 over 200k steps)
        progress = min(1.0, step / 200_000)
        curr_offset = self.start_offset + progress * (self.target_offset - self.start_offset)
        
        # 2. Update Proxy Decay (0.0001 -> 0 over 100k steps)
        bonus_progress = min(1.0, step / 100_000)
        curr_bonus = self.start_bonus * (1.0 - bonus_progress)
        
        # Apply to environment (via rl_config. Attribute access fixed)
        raw_env = self.training_env.envs[0]
        raw_env.rl_config.min_post_offset_bps = curr_offset
        raw_env.rl_config.reward_quote_presence_bonus = curr_bonus
        
        if step % 5000 == 0:
            print(f"[P2] Step {step}: offset={curr_offset:.3f} bps, bonus={curr_bonus:.6f}")
            
        return True

def run_bc(model, dataset_path, epochs=10, batch_size=256):
    print(f"[BC] Starting Behavior Cloning on {dataset_path}...")
    df = pd.read_parquet(dataset_path)
    
    obs = np.array(df['obs'].tolist(), dtype=np.float32)
    actions = np.array(df['action'].tolist(), dtype=np.int64)
    
    dataset = TensorDataset(torch.from_numpy(obs), torch.from_numpy(actions))
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    
    optimizer = torch.optim.Adam(model.policy.parameters(), lr=1e-4)
    criterion = torch.nn.CrossEntropyLoss()
    
    device = model.device
    model.policy.to(device)
    model.policy.train()
    
    for epoch in range(epochs):
        total_loss = 0
        for b_obs, b_actions in loader:
            b_obs, b_actions = b_obs.to(device), b_actions.to(device)
            
            # SB3 policy returns distribution for actions
            dist = model.policy.get_distribution(b_obs)
            logits = dist.distribution.logits
            
            loss = criterion(logits, b_actions)
            
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            
            total_loss += loss.item()
            
        print(f"[BC] Epoch {epoch+1}/{epochs}, Loss: {total_loss/len(loader):.6f}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=300000)
    parser.add_argument("--bc_epochs", type=int, default=15)
    parser.add_argument("--dataset", type=str, default="data/teacher_vnext_100k.parquet")
    parser.add_argument("--out", type=str, default="python/runs_train/vnext/ppo_p2")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # Initialize Environment
    raw_env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="golden_l2_v1_train",
        symbol="BTCUSDT",
        fill_model=2,
        **P2_CONFIG
    )
    venv = DummyVecEnv([lambda: raw_env])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    # Create Model
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = PPO(
        "MlpPolicy", venv,
        learning_rate=3e-4, ent_coef=0.05,
        n_steps=2048, batch_size=64, n_epochs=10,
        verbose=1, device=device,
        policy_kwargs=dict(net_arch=dict(pi=[256, 256], vf=[256, 256])),
    )

    # 1. Behavior Cloning
    if os.path.exists(args.dataset):
        run_bc(model, args.dataset, epochs=args.bc_epochs)
        bc_path = os.path.join(args.out, "model_bc_init.zip")
        model.save(bc_path)
        print(f"[P2] BC Initialized Model saved to {bc_path}")
    else:
        print(f"[WARN] Teacher dataset {args.dataset} NOT FOUND. Skipping BC.")

    # 2. RL Training with Curriculum
    from ppo_vnext import VNextCallback # Reuse scorecard callback
    
    p2_callback = Phase2Callback()
    vnext_callback = VNextCallback(out_dir=args.out) # Scorecard
    
    from stable_baselines3.common.callbacks import CallbackList
    callbacks = CallbackList([p2_callback, vnext_callback])

    print(f"[P2] Starting RL Training for {args.steps} steps...")
    model.learn(total_timesteps=args.steps, callback=callbacks, progress_bar=True)

    # Save Final
    model.save(os.path.join(args.out, "ppo_p2_final.zip"))
    venv.save(os.path.join(args.out, "ppo_p2_venv_final.pkl"))

if __name__ == "__main__":
    main()
