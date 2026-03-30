"""
PPO Phase 27 Scaling - Controlled Expansion
===========================================
Continues from model_10k.zip to 50k steps.
Monitors behavioral stability and fill discovery.
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

# -- Scaling Config (Same as Calib) --
SCALING_CONFIG = dict(
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
    reward_quote_presence_bonus=0.001,
    override_action_dim=10,
)

class ScalingCallback(BaseCallback):
    CHECKPOINTS = [25000, 50000]
    
    def __init__(self, out_dir, verbose=0):
        super(ScalingCallback, self).__init__(verbose)
        self.out_dir = out_dir
        self._last_checkpoint_idx = 0
        os.makedirs(out_dir, exist_ok=True)

    def _on_step(self) -> bool:
        step = self.num_timesteps
        if self._last_checkpoint_idx < len(self.CHECKPOINTS):
            next_cp = self.CHECKPOINTS[self._last_checkpoint_idx]
            if step >= next_cp:
                self._last_checkpoint_idx += 1
                label = f"{next_cp // 1000}k"
                print(f"\n[SCALING] Step {step}: Saving checkpoint {label}...")
                
                ckpt_path = os.path.join(self.out_dir, f"model_{label}.zip")
                self.model.save(ckpt_path)
                
                venv_path = os.path.join(self.out_dir, f"venv_{label}.pkl")
                self.model.get_env().save(venv_path)
                
                # Run Audit
                self._run_audit(ckpt_path, venv_path, step)
        return True

    def _run_audit(self, model_path, venv_path, steps):
        from ppo_eval_checkpoint import run_ppo_audit
        label = f"{steps // 1000}k"
        print(f"  -- Running Audit at {label}...")
        try:
            report = run_ppo_audit(
                model_path=model_path,
                venv_path=venv_path,
                dataset_id="golden_l2_v1_val",
                steps_per_eval=5000,
                server="localhost:50051",
                **SCALING_CONFIG
            )
            report_path = os.path.join(self.out_dir, f"report_{label}.json")
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2)
            
            # Print Progress Scorecard
            lc = report.get("lifecycle", {})
            usage = lc.get("action_usage_detailed", {})
            semantic = lc.get("semantic_summary", {})
            print(f"\n[PHASE 27 SCALING SCORECARD - {label}]")
            print(f"  Status:           {'ACTIVE' if report.get('total_trades', 0) > 0 else 'EXPLORING'}")
            print(f"  Trades:           {report.get('total_trades', 0)}")
            print(f"  HOLD %:           {usage.get('HOLD', 0):.1f}%")
            print(f"  OPEN Attempt:     {semantic.get('OPEN', 0):.1f}%")
            print(f"  Invalid Action:   {usage.get('ADD_LONG', 0) + usage.get('ADD_SHORT', 0):.2f}% (Expect 0)")
            print(f"  Blocks (Offset):  {report.get('gate_telemetry', {}).get('offset', 0)}")
            print(f"  ------------------------------")
        except Exception as e:
            print(f"  Audit FAILED: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_steps", type=int, default=50000)
    parser.add_argument("--load", type=str, default="python/runs_train/phase27_calib/model_10k.zip")
    parser.add_argument("--venv", type=str, default="python/runs_train/phase27_calib/venv_10k.pkl")
    parser.add_argument("--out", type=str, default="python/runs_train/phase27_scaling")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    def make_env():
        return GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            fill_model=2,
            decision_interval_ms=1000,
            **SCALING_CONFIG
        )

    venv = DummyVecEnv([make_env])
    
    # Load Normalization Stats
    if os.path.exists(args.venv):
        print(f"[SCALING] Loading venv stats from {args.venv}")
        venv = VecNormalize.load(args.venv, venv)
    else:
        print(f"[WARNING] No venv pkl found at {args.venv}. Normalization will restart.")
        venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[SCALING] Loading model from {args.load}")
    model = PPO.load(args.load, env=venv, device=device)
    
    # Keep hyperparams from Calib
    model.ent_coef = 0.05
    model.learning_rate = 2e-5
    
    callback = ScalingCallback(args.out)
    print(f"[SCALING] Resuming training until {args.train_steps} steps...")
    model.learn(total_timesteps=args.train_steps, callback=callback, reset_num_timesteps=False, progress_bar=True)
    print("[SCALING] Complete.")

if __name__ == "__main__":
    main()
