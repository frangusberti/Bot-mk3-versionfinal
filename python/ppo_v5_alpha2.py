import os
import sys
import argparse
import json
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

class PPOPilotCallback(BaseCallback):
    """Callback for monitoring PPO Pilot metrics and saving checkpoints."""
    def __init__(self, eval_freq=20000, val_dataset="golden_l2_v1_val", venv_stats_path=None, out_dir="python/runs_train/maker_v3/ppo_v5_alpha2", verbose=0):
        super().__init__(verbose)
        self.eval_freq = eval_freq
        self.val_dataset = val_dataset
        self.venv_stats_path = venv_stats_path
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        
    def _on_step(self) -> bool:
        if self.n_calls % self.eval_freq == 0:
            print(f"\n[PPO Alpha2] Step {self.num_timesteps}: Saving Checkpoint...")
            checkpoint_path = os.path.join(self.out_dir, f"model_{self.num_timesteps // 1000}k.zip")
            self.model.save(checkpoint_path)
            
            # Run evaluation scorecard
            self.run_eval_scorecard(checkpoint_path, self.num_timesteps)
            
        return True

    def run_eval_scorecard(self, model_path, steps):
        from ppo_eval_checkpoint import run_ppo_audit
        try:
            report = run_ppo_audit(
                model_path=model_path,
                venv_path=self.venv_stats_path,
                dataset_id=self.val_dataset,
                steps_per_eval=5000 
            )
            
            report_path = os.path.join(self.out_dir, f"report_{steps // 1000}k.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)
            
            print(f"[PPO Alpha2] Checkpoint {steps // 1000}k Scorecard:")
            print(f"  PnL: {report['net_pnl']:.2f}% | Trades: {report['total_trades']} | Maker: {report['maker_ratio']:.1%}")
            print(f"  HOLD: {report['action_dist'].get('HOLD', 0):.1f}% | BID: {report['action_dist'].get('POST_BID', 0):.1f}% | ASK: {report['action_dist'].get('POST_ASK', 0):.1f}%")
        except Exception as e:
            print(f"[PPO Alpha2] Eval Failed at {steps} steps: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bc_model", type=str, default="python/runs_train/maker_v3/bc_v4_normalized/bc_pretrained_model.zip")
    parser.add_argument("--bc_venv", type=str, default="python/runs_train/maker_v3/bc_v4_normalized/vec_normalize.pkl")
    parser.add_argument("--train_steps", type=int, default=100000)
    parser.add_argument("--out", type=str, default="python/runs_train/maker_v3/ppo_v5_alpha2")
    args = parser.parse_args()

    # 1. Initialize Env with Maker Alpha v2 configuration
    print("[PPO Alpha2] Initializing Training Environment...")
    raw_env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id="golden_l2_v1_train", 
        symbol="BTCUSDT", 
        fill_model=2, # Optimistic for exploration
        reward_maker_fill_bonus=0.0010,     # 10 bps bonus (Up from 6)
        reward_taker_fill_penalty=0.0005,   # 5 bps penalty
        reward_toxic_fill_penalty=0.0010,   # 10 bps penalty
        reward_idle_posting_penalty=0.00001, # 0.1 bps per step
        reward_distance_to_mid_penalty=0.00001, # 0.1 bps per bps of distance (NEW)
        reward_reprice_penalty_bps=0.00005, # 0.5 bps per reprice
        post_delta_threshold_bps=0.05,      # 0.05 bps threshold
    )
    venv = DummyVecEnv([lambda: raw_env])
    
    print(f"[PPO Alpha2] Loading Normalization Stats from {args.bc_venv}")
    venv = VecNormalize.load(args.bc_venv, venv)
    venv.training = True 
    venv.norm_reward = False
    
    # 2. Warm-start with BC model and aggressive exploration params
    print(f"[PPO Alpha2] Warm-starting with BC model: {args.bc_model}")
    model = PPO.load(
        args.bc_model,
        env=venv,
        device="cuda" if torch.cuda.is_available() else "cpu",
        learning_rate=2e-4, # Increased for faster exploration
        ent_coef=0.05,      # Increased for high entropy
        n_steps=2048,
        batch_size=64,      # Smaller batches for stochastic gradients
        n_epochs=10,
        verbose=1
    )
    
    # 3. Launch Training
    callback = PPOPilotCallback(eval_freq=20000, venv_stats_path=args.bc_venv, out_dir=args.out)
    
    print(f"[PPO Alpha2] Starting {args.train_steps} steps Pilot...")
    model.learn(total_timesteps=args.train_steps, callback=callback, progress_bar=True)
    
    # Final Save
    model.save(os.path.join(args.out, "ppo_alpha2_final.zip"))
    venv.save(os.path.join(args.out, "ppo_alpha2_venv_final.pkl"))
    print("\n[PPO Alpha2] Pilot Complete.")

if __name__ == "__main__":
    main()
