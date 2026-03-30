"""
PPO Phase 27 Calibration Pilot - Behavioral Restoration
========================================================
Uses 10-action space, hardened backend (-0.1 penalty), 
and corrected BC weights.
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
from vnext_scorecard import generate_vnext_scorecard
from grpc_env import GrpcTradingEnv
import psutil
import gc

# -- Calibration Config --
CALIB_CONFIG = dict(
    close_position_loss_threshold=0.003,
    min_post_offset_bps=0.2,
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    profit_floor_bps=2.0,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_quote_presence_bonus=0.001, # For exploration incentive
    override_action_dim=10,
)

class CalibrationCallback(BaseCallback):
    CHECKPOINTS = [2000, 5000, 10000]
    
    def __init__(self, out_dir, verbose=0):
        super(CalibrationCallback, self).__init__(verbose)
        self.out_dir = out_dir
        self._last_checkpoint_idx = 0
        os.makedirs(out_dir, exist_ok=True)

    def _on_step(self) -> bool:
        step = self.num_timesteps
        if self._last_checkpoint_idx < len(self.CHECKPOINTS):
            next_cp = self.CHECKPOINTS[self._last_checkpoint_idx]
            if step >= next_cp:
                self._last_checkpoint_idx += 1
                label = f"{next_cp // 1000}k" if next_cp >= 1000 else f"{next_cp}"
                print(f"\n[CALIB] Step {step}: Saving checkpoint {label}...")
                
                ckpt_path = os.path.join(self.out_dir, f"model_{label}.zip")
                self.model.save(ckpt_path)
                
                venv_path = os.path.join(self.out_dir, f"venv_{label}.pkl")
                self.model.get_env().save(venv_path)
                
                # Run Audit
                self._run_audit(ckpt_path, venv_path, step)
        return True

    def _run_audit(self, model_path, venv_path, steps):
        from ppo_eval_checkpoint import run_ppo_audit
        label = f"{steps // 1000}k" if steps >= 1000 else f"{steps}"
        print(f"  -- Running Audit at {label}...")
        try:
            report = run_ppo_audit(
                model_path=model_path,
                venv_path=venv_path,
                dataset_id="golden_l2_v1_val",
                steps_per_eval=2000,
                server="localhost:50051",
                **CALIB_CONFIG
            )
            report_path = os.path.join(self.out_dir, f"report_{label}.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)
            
            # Print Scorecard
            lc = report.get("lifecycle", {})
            action_usage = lc.get("action_usage_detailed", {})
            print(f"\n[PHASE 27 SCORECARD - {label}]")
            print(f"  Trades:           {report.get('total_trades', 0)}")
            print(f"  ADD_LONG (FLAT):  {action_usage.get('ADD_LONG', 0):.1f}%")
            print(f"  HOLD:             {action_usage.get('HOLD', 0):.1f}%")
            print(f"  OPEN_LONG:        {action_usage.get('OPEN_LONG', 0):.1f}%")
            print(f"  ------------------------------")
        except Exception as e:
            print(f"  Audit FAILED: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_steps", type=int, default=10000)
    parser.add_argument("--load", type=str, required=True)
    parser.add_argument("--out", type=str, default="python/runs_train/phase27_calib")
    args = parser.parse_args()

    def make_env():
        return GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            fill_model=2,
            decision_interval_ms=1000,
            **CALIB_CONFIG
        )

    venv = DummyVecEnv([make_env])
    venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[CALIB] Loading BC weights from {args.load}")
    
    # Load BC model as PPO base
    model = PPO.load(args.load, env=venv, device=device)
    
    # Update hyperparams for calibration
    model.ent_coef = 0.05
    model.learning_rate = 2e-5
    model.target_kl = 0.015
    
    callback = CalibrationCallback(args.out)
    print(f"[CALIB] Starting {args.train_steps} steps...")
    model.learn(total_timesteps=args.train_steps, callback=callback, progress_bar=True)
    print("[CALIB] Pilot Complete.")

if __name__ == "__main__":
    main()
