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

class PPORewardV3Callback(BaseCallback):
    """Callback for monitoring Reward v3 Pilot metrics and saving checkpoints."""
    def __init__(self, eval_freq=25000, val_dataset="golden_l2_v1_val", venv_stats_path=None, out_dir="python/runs_train/maker_v3/ppo_v6_reward_v3", verbose=0):
        super().__init__(verbose)
        self.eval_freq = eval_freq
        self.val_dataset = val_dataset
        self.venv_stats_path = venv_stats_path
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        
    def _on_step(self) -> bool:
        if self.n_calls % self.eval_freq == 0:
            print(f"\n[Reward v3] Step {self.num_timesteps}: Saving Checkpoint...")
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
                steps_per_eval=10000 
            )
            
            report_path = os.path.join(self.out_dir, f"report_{steps // 1000}k.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)
            
            print(f"[Reward v3] Checkpoint {steps // 1000}k Scorecard:")
            print(f"  PnL: {report['net_pnl']:.2f}% | PF: {report.get('profit_factor', 0):.2f} | Trades: {report['total_trades']}")
            print(f"  HOLD: {report['action_dist'].get('HOLD', 0):.1f}% | BID: {report['action_dist'].get('POST_BID', 0):.1f}% | ASK: {report['action_dist'].get('POST_ASK', 0):.1f}%")
            print(f"  Maker Fills: {report.get('maker_fills', 0)} | Toxic: {report.get('toxic_fills', 0)}")
            print(f"  Max Drawdown: {report.get('max_drawdown', 0)*100:.3f}%")
        except Exception as e:
            print(f"[Reward v3] Eval Failed at {steps} steps: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bc_model", type=str, default="python/runs_train/maker_v3/bc_v4_normalized/bc_pretrained_model.zip")
    parser.add_argument("--bc_venv", type=str, default="python/runs_train/maker_v3/bc_v4_normalized/vec_normalize.pkl")
    parser.add_argument("--train_steps", type=int, default=100000)
    parser.add_argument("--out", type=str, default="python/runs_train/maker_v3/ppo_v6_reward_v3")
    args = parser.parse_args()

    # 1. Initialize Env with Reward v3 configuration
    print("[Reward v3] Initializing Training Environment...")
    raw_env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id="golden_l2_v1_train", 
        symbol="BTCUSDT", 
        fill_model=2, # Optimistic for faster reward feedback
        reward_maker_fill_bonus=0.0020,
        reward_taker_fill_penalty=0.0005,
        reward_toxic_fill_penalty=0.0010,
        reward_idle_posting_penalty=0.00001,
        reward_distance_to_mid_penalty=0.00001,
        reward_reprice_penalty_bps=0.00005,
        reward_mtm_penalty_window_ms=5000,
        reward_mtm_penalty_multiplier=1.0, # Anti-Adverse Selection
        reward_adverse_selection_bonus_multiplier=0.5, # Positive feedback for edge
        reward_skew_penalty_weight=0.0005, # Quadratic Symmetrization
        reward_realized_pnl_multiplier=0.001, # Explicit realized PnL reward
        reward_cancel_all_penalty=2e-7, # Anti-spam
        post_delta_threshold_bps=0.05,
    )
    venv = DummyVecEnv([lambda: raw_env])
    
    print(f"[Reward v3] Loading Normalization Stats from {args.bc_venv}")
    venv = VecNormalize.load(args.bc_venv, venv)
    venv.training = True 
    venv.norm_reward = False
    
    # 2. Warm-start from BC
    print(f"[Reward v3] Warm-starting with BC model: {args.bc_model}")
    model = PPO.load(
        args.bc_model,
        env=venv,
        device="cuda" if torch.cuda.is_available() else "cpu",
        learning_rate=2e-4,
        ent_coef=0.05,      
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        verbose=1
    )
    
    # 3. Launch Training Pilot (100k Steps)
    callback = PPORewardV3Callback(eval_freq=25000, venv_stats_path=args.bc_venv, out_dir=args.out)
    
    print(f"[Reward v3] Starting {args.train_steps} steps Pilot Run...")
    model.learn(total_timesteps=args.train_steps, callback=callback, progress_bar=True)
    
    # Final Save
    model.save(os.path.join(args.out, "ppo_reward_v3_final.zip"))
    venv.save(os.path.join(args.out, "ppo_reward_v3_venv_final.pkl"))
    print("\n[Reward v3] Pilot Complete.")

if __name__ == "__main__":
    main()
