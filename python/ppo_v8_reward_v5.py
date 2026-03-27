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

class PPORewardV5Callback(BaseCallback):
    """Callback for monitoring Reward v5 Pilot metrics and saving checkpoints."""
    def __init__(self, start_step_offset=0, val_dataset="golden_l2_v1_val", venv_stats_path=None, out_dir="python/runs_train/maker_v5/ppo_v8_reward_v5", verbose=0):
        super().__init__(verbose)
        self.start_step_offset = start_step_offset
        self.val_dataset = val_dataset
        self.venv_stats_path = venv_stats_path
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        
    def _on_step(self) -> bool:
        absolute_steps = self.start_step_offset + self.n_calls
        if absolute_steps in [10000, 25000, 50000]:
            print(f"\n[Reward v5] Step {absolute_steps}: Saving Checkpoint...")
            checkpoint_path = os.path.join(self.out_dir, f"model_{absolute_steps // 1000}k.zip")
            self.model.save(checkpoint_path)
            
            # Run evaluation scorecard
            self.run_eval_scorecard(checkpoint_path, absolute_steps)
            
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
            
            print(f"[Reward v5] Checkpoint {steps // 1000}k Scorecard:")
            print(f"  PnL: {report['net_pnl']:.2f}% | PF: {report.get('profit_factor', 0):.2f} | Trades: {report['total_trades']}")
            print(f"  HOLD: {report['action_dist'].get('HOLD', 0):.1f}% | BID: {report['action_dist'].get('POST_BID', 0):.1f}% | ASK: {report['action_dist'].get('POST_ASK', 0):.1f}%")
            print(f"  Maker Fills: {report.get('maker_fills', 0)} | Toxic: {report.get('toxic_fills', 0)}")
            print(f"  Max Drawdown: {report.get('max_drawdown', 0)*100:.3f}%")
        except Exception as e:
            print(f"[Reward v5] Eval Failed at {steps} steps: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bc_model", type=str, default="python/runs_train/maker_v3/bc_v4_normalized/bc_pretrained_model.zip")
    parser.add_argument("--bc_venv", type=str, default="python/runs_train/maker_v3/bc_v4_normalized/vec_normalize.pkl")
    parser.add_argument("--train_steps", type=int, default=50000)
    parser.add_argument("--start_step", type=int, default=0)
    parser.add_argument("--out", type=str, default="python/runs_train/maker_v5/ppo_v8_reward_v5")
    args = parser.parse_args()

    # 1. Initialize Env with Reward v5 configuration
    print("[Reward v5] Initializing Training Environment...")
    raw_env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id="golden_l2_v1_train", 
        symbol="BTCUSDT", 
        fill_model=2, # Optimistic for faster reward feedback
        reward_maker_fill_bonus=0.0035, # Increased 1.75x
        reward_taker_fill_penalty=0.0005,
        reward_toxic_fill_penalty=0.0010,
        reward_idle_posting_penalty=0.00001,
        reward_distance_to_mid_penalty=0.00001,
        reward_reprice_penalty_bps=0.00005,
        reward_mtm_penalty_window_ms=5000,
        reward_mtm_penalty_multiplier=0.4, # Reduced from 1.0 to 0.4
        reward_adverse_selection_bonus_multiplier=0.5,
        reward_skew_penalty_weight=0.00005,
        reward_inventory_change_penalty=0.005,
        reward_two_sided_bonus=0.001,
        reward_realized_pnl_multiplier=0.001,
        reward_cancel_all_penalty=4e-7, # Increased 2x
        reward_taker_action_penalty=0.001, # New: Strong Negative Reward
        reward_quote_presence_bonus=0.0001, # New: Small Positive Bonus
        post_delta_threshold_bps=0.05,
    )
    venv = DummyVecEnv([lambda: raw_env])
    
    print(f"[Reward v5] Loading Normalization Stats from {args.bc_venv}")
    venv = VecNormalize.load(args.bc_venv, venv)
    venv.training = True 
    venv.norm_reward = False
    
    # 2. Warm-start from BC
    print(f"[Reward v5] Warm-starting with BC model: {args.bc_model}")
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
    
    # 3. Launch Training Pilot (50k Steps)
    callback = PPORewardV5Callback(start_step_offset=args.start_step, venv_stats_path=args.bc_venv, out_dir=args.out)
    
    print(f"[Reward v5] Starting {args.train_steps} steps Pilot Run...")
    model.learn(total_timesteps=args.train_steps, callback=callback, progress_bar=True)
    
    # Final Save
    model.save(os.path.join(args.out, "ppo_reward_v5_final.zip"))
    venv.save(os.path.join(args.out, "ppo_reward_v5_venv_final.pkl"))
    print("\n[Reward v5] Pilot Complete.")

if __name__ == "__main__":
    main()
