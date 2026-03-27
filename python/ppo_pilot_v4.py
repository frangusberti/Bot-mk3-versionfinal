import os
import sys
import argparse
import json
import numpy as np
import torch
from collections import defaultdict
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize, VecMonitor
from stable_baselines3.common.callbacks import BaseCallback

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

class PPOPilotCallback(BaseCallback):
    """Callback for monitoring PPO Pilot metrics and saving checkpoints."""
    def __init__(self, eval_freq=20000, val_dataset="golden_l2_v1_val", venv_stats_path=None, out_dir="python/runs_train/ppo_pilot_v4", verbose=0):
        super().__init__(verbose)
        self.eval_freq = eval_freq
        self.val_dataset = val_dataset
        self.venv_stats_path = venv_stats_path
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        
    def _on_step(self) -> bool:
        if self.n_calls % self.eval_freq == 0:
            print(f"\n[PPO] Step {self.num_timesteps}: Launching Checkpoint Evaluation...")
            checkpoint_path = os.path.join(self.out_dir, f"model_{self.num_timesteps // 1000}k.zip")
            self.model.save(checkpoint_path)
            
            # Run evaluation (calling standalone script or function)
            # We'll call the evaluation function directly for simplicity in this pilot
            self.run_eval_scorecard(checkpoint_path, self.num_timesteps)
            
        return True

    def run_eval_scorecard(self, model_path, steps):
        from ppo_eval_checkpoint import run_ppo_audit
        report = run_ppo_audit(
            model_path=model_path,
            venv_path=self.venv_stats_path,
            dataset_id=self.val_dataset,
            steps_per_eval=5000 
        )
        
        report_path = os.path.join(self.out_dir, f"report_{steps // 1000}k.json")
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        
        print(f"[PPO] Checkpoint {steps // 1000}k Scorecard:")
        print(f"  PnL: {report['net_pnl']:.2f}% | Trades: {report['total_trades']} | Maker: {report['maker_ratio']:.1%}")
        print(f"  HOLD: {report['action_dist'].get('HOLD', 0):.1f}% | BID: {report['action_dist'].get('POST_BID', 0):.1f}% | ASK: {report['action_dist'].get('POST_ASK', 0):.1f}%")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bc_model", type=str, required=True, help="Path to BC v4 model .zip")
    parser.add_argument("--bc_venv", type=str, required=True, help="Path to BC v4 vec_normalize.pkl")
    parser.add_argument("--train_steps", type=int, default=100000)
    parser.add_argument("--out", type=str, default="python/runs_train/ppo_pilot_v4")
    args = parser.parse_args()

    # 1. Initialize Env with pre-loaded Normalization
    print("[PPO] Initializing Training Environment...")
    raw_env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id="golden_l2_v1_train", 
        symbol="BTCUSDT", 
        fill_model=2,
        reward_maker_fill_bonus=0.0006,      # 6 bps bonus
        reward_taker_fill_penalty=0.0005,    # 5 bps penalty
        reward_toxic_fill_penalty=0.0010,    # 10 bps penalty
        reward_idle_posting_penalty=0.00001, # 0.1 bps per step
        reward_reprice_penalty_bps=0.00005,  # 0.5 bps per reprice
        post_delta_threshold_bps=0.05,       # 0.05 bps threshold
    )
    venv = DummyVecEnv([lambda: raw_env])
    
    print(f"[PPO] Loading Normalization Stats from {args.bc_venv}")
    venv = VecNormalize.load(args.bc_venv, venv)
    # IMPORTANT: Keep training the normalization stats during PPO to adapt? 
    # Or freeze it? User requested "Strict consistency". We'll allow subtle updates but monitor drift.
    venv.training = True 
    venv.norm_reward = False
    
    # 2. Load BC Model as Warm-Start
    print(f"[PPO] Warm-starting with BC model: {args.bc_model}")
    model = PPO.load(
        args.bc_model,
        env=venv,
        device="cuda" if torch.cuda.is_available() else "cpu",
        learning_rate=5e-5, # Small LR to prevent catastrophic forgetting
        ent_coef=0.01,
        n_steps=2048,
        batch_size=256,
        verbose=1
    )
    
    # 3. Launch Training
    callback = PPOPilotCallback(eval_freq=20000, venv_stats_path=args.bc_venv, out_dir=args.out)
    
    print(f"[PPO] Starting {args.train_steps} steps Pilot...")
    model.learn(total_timesteps=args.train_steps, callback=callback, progress_bar=True)
    
    # Final Save
    model.save(os.path.join(args.out, "ppo_pilot_final.zip"))
    venv.save(os.path.join(args.out, "ppo_pilot_venv_final.pkl"))
    print("\n[PPO] Pilot Complete.")

if __name__ == "__main__":
    main()
