"""
stage3_final_retrain.py -- Stage 3 Final Long Retrain (2M Steps).

Requirements:
1. Low-consumption mode (affinities/priority).
2. Leave 2 CPU cores free (using 14 of 16).
3. Checkpoints every 500k steps.
4. Metric export at each checkpoint.
5. Early stopping if HOLD=100% at 1M steps.
"""
import sys
import os
import time
import json
import argparse
import numpy as np
import psutil
from collections import defaultdict

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecMonitor
from stable_baselines3.common.callbacks import BaseCallback
from grpc_env import GrpcTradingEnv

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL",
    "REDUCE_25", "REDUCE_50", "REDUCE_100"
]

def set_low_priority():
    p = psutil.Process(os.getpid())
    # Set priority to BELOW_NORMAL
    if sys.platform == 'win32':
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    else:
        p.nice(10)
    
    # Set affinity to leave 2 cores free (assume 16 cores)
    cores = list(range(max(1, psutil.cpu_count() - 2)))
    try:
        p.cpu_affinity(cores)
        print(f"Low priority set. Affinity restricted to {len(cores)} cores.")
    except Exception as e:
        print(f"Could not set affinity: {e}")

class Stage3Callback(BaseCallback):
    def __init__(self, eval_env, eval_steps=3000, checkpoint_freq=500000, log_dir="python/runs_train/stage3"):
        super().__init__()
        self.eval_env = eval_env
        self.eval_steps = eval_steps
        self.checkpoint_freq = checkpoint_freq
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.checkpoint_metrics = []

    def _on_step(self) -> bool:
        if self.num_timesteps % self.checkpoint_freq == 0:
            print(f"\n--- CHECKPOINT AT {self.num_timesteps} STEPS ---")
            
            # Save model
            model_path = os.path.join(self.log_dir, f"model_{self.num_timesteps}")
            self.model.save(model_path)
            
            # Run Eval
            metrics = self.run_eval()
            metrics["steps"] = self.num_timesteps
            self.checkpoint_metrics.append(metrics)
            
            # Export Current Metrics
            with open(os.path.join(self.log_dir, "stage3_progress.json"), "w") as f:
                json.dump(self.checkpoint_metrics, f, indent=2)
            
            # Check for Early Stopping (at 1M steps)
            if self.num_timesteps >= 1000000:
                # If deterministic eval is still 100% HOLD
                hold_rate = metrics["hold_rate"]
                if hold_rate > 99.9:
                    print("\n[EARLY STOPPING] 1M steps reached with 100% HOLD. Stopping training to save time.")
                    return False # Stop training
            
        return True

    def run_eval(self):
        obs, info = self.eval_env.reset()
        actions = defaultdict(int)
        trades = 0
        ep_rewards = []
        equities = [info.get("equity", 10000.0)]
        current_ep = 0.0

        for _ in range(self.eval_steps):
            action, _ = self.model.predict(obs, deterministic=True)
            action_val = int(action)
            actions[action_val] += 1
            obs, reward, terminated, truncated, info = self.eval_env.step(action_val)
            current_ep += reward
            if "equity" in info:
                equities.append(info["equity"])
            if "trades_executed" in info:
                trades += info["trades_executed"]
            if terminated or truncated:
                ep_rewards.append(current_ep)
                current_ep = 0.0
                obs, info = self.eval_env.reset()
                time.sleep(0.5) # Stability pause

        total_actions = sum(actions.values())
        hold_rate = actions.get(0, 0) / total_actions * 100 if total_actions > 0 else 100
        
        print(f"  HOLD Rate: {hold_rate:.1f}% | Trades: {trades}")
        print(f"  Net PnL: {(equities[-1] - equities[0]) / equities[0] * 100:.2f}%")
        
        return {
            "hold_rate": float(hold_rate),
            "trades": int(trades),
            "action_dist": dict(actions),
            "mean_return": float(np.mean(ep_rewards)) if ep_rewards else 0.0,
            "equity_final": float(equities[-1])
        }

def main():
    set_low_priority()
    
    log_dir = "python/runs_train/stage3"
    
    train_env = GrpcTradingEnv(server_addr="localhost:50051", dataset_id="stage2_train", symbol="BTCUSDT")
    eval_env = GrpcTradingEnv(server_addr="localhost:50051", dataset_id="stage2_eval", symbol="BTCUSDT")
    
    vec_env = DummyVecEnv([lambda: train_env])
    vec_env = VecMonitor(vec_env, log_dir)
    
    model = PPO(
        "MlpPolicy",
        vec_env,
        verbose=1,
        ent_coef=0.015, # Slightly higher entropy for longer run
        learning_rate=1e-4,
        batch_size=256,
        n_steps=2048,
        n_epochs=10,
        clip_range=0.2,
        target_kl=0.02,
    )
    
    cb = Stage3Callback(eval_env=eval_env)
    
    print("\nStarting Stage 3 Final Long Retrain (2M steps)...")
    try:
        model.learn(total_timesteps=2000000, callback=cb)
        model.save(os.path.join(log_dir, "final_model"))
    except KeyboardInterrupt:
        print("\nTraining interrupted by user.")
    
    print("\nStage 3 training loop exited.")

if __name__ == "__main__":
    main()
