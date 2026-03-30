"""
PPO Thesis-Driven Scaling (50k -> 300k)
=====================================
Continues from model_50k.zip with the micro_strict=False audit fix.
"""
import os
import sys
import argparse
import json
import torch
import numpy as np

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback
from grpc_env import GrpcTradingEnv

# -- Thesis Driven Config --
THESIS_CONFIG = dict(
    close_position_loss_threshold=0.003,
    min_post_offset_bps=0.2,
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_quote_presence_bonus=0.001,
    reward_thesis_decay_weight=0.0001,
    override_action_dim=10,
    use_selective_entry=True,
    entry_veto_threshold_bps=0.2,
    micro_strict=False, # NEW: Enable L1 micro features in replay
)

class ThesisCallback(BaseCallback):
    CHECKPOINTS = [50000, 100000, 150000, 200000, 250000]
    
    def __init__(self, out_dir, verbose=0):
        super(ThesisCallback, self).__init__(verbose)
        self.out_dir = out_dir
        self._last_checkpoint_idx = 0
        os.makedirs(out_dir, exist_ok=True)

    def _on_step(self) -> bool:
        step = self.num_timesteps
        if self._last_checkpoint_idx < len(self.CHECKPOINTS):
            next_cp = self.CHECKPOINTS[self._last_checkpoint_idx]
            if step >= next_cp:
                self._last_checkpoint_idx += 1
                total_steps = 50000 + next_cp
                label = f"{total_steps // 1000}k"
                print(f"\n[SCALING] Step {total_steps}: Saving checkpoint {label}...")
                
                ckpt_path = os.path.join(self.out_dir, f"model_{label}.zip")
                self.model.save(ckpt_path)
                
                venv_path = os.path.join(self.out_dir, f"venv_{label}.pkl")
                self.model.get_env().save(venv_path)
                
                # Run Audit
                self._run_audit(ckpt_path, venv_path, total_steps)
        return True

    def _run_audit(self, model_path, venv_path, total_steps):
        from ppo_eval_checkpoint import run_ppo_audit
        label = f"{total_steps // 1000}k"
        print(f"  -- Running Thesis Audit at {label}...")
        try:
            report = run_ppo_audit(
                model_path=model_path,
                venv_path=venv_path,
                dataset_id="stage2_eval", # Corrected dataset ID
                steps_per_eval=5000,
                server="localhost:50051",
                **THESIS_CONFIG
            )
            report_path = os.path.join(self.out_dir, f"report_{label}.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)
            
            # Print Enhanced Scorecard
            lc = report.get("lifecycle", {})
            usage = lc.get("action_counts", {})
            print(f"\n[THESIS SCALING SCORECARD - {label}]")
            print(f"  Trades:           {report.get('total_trades', 0)}")
            print(f"  Net PnL %:        {report.get('net_pnl', 0):.4f}%")
            print(f"  Thesis Decay Tot: {lc.get('thesis_decay_total', 0):.4f}")
            print(f"  Invalid Actor RL: {lc.get('invalid_action_rate', 0):.2f}%")
            print(f"  CLOSE (W/ POS):   {lc.get('close_with_pos', 0)}")
            print(f"  CLOSE (FLAT):     {lc.get('close_flat', 0)}")
            print(f"  Coincident Exits: {lc.get('exit_coincident_with_decay', 0)}")
            print(f"  Action Usage:     HOLD={usage.get('HOLD',0)}, OPEN={usage.get('OPEN_LONG',0)+usage.get('OPEN_SHORT',0)}")
            print(f"  ------------------------------")
        except Exception as e:
            print(f"  Audit FAILED: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_steps", type=int, default=250000)
    parser.add_argument("--load", type=str, default="python/runs_train/vnext_thesis_validation/model_50k.zip")
    parser.add_argument("--venv", type=str, default="python/runs_train/vnext_thesis_validation/venv_50k.pkl")
    parser.add_argument("--out", type=str, default="python/runs_train/vnext_thesis_scaling")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    def make_env():
        return GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="stage2_train",
            symbol="BTCUSDT",
            fill_model=1, 
            decision_interval_ms=1000,
            **THESIS_CONFIG
        )

    venv = DummyVecEnv([make_env])
    
    if os.path.exists(args.venv):
        print(f"[SCALING] Loading venv stats from {args.venv}")
        venv = VecNormalize.load(args.venv, venv)
    else:
        venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[SCALING] Loading base model from {args.load}")
    model = PPO.load(args.load, env=venv, device=device)
    
    model.ent_coef = 0.05
    model.learning_rate = 2e-5
    
    callback = ThesisCallback(args.out)
    print(f"[SCALING] Starting training for {args.train_steps} steps...")
    model.learn(total_timesteps=args.train_steps, callback=callback, reset_num_timesteps=True, progress_bar=True)
    print("[SCALING] Complete.")

if __name__ == "__main__":
    main()
