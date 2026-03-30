"""
PPO Thesis-Driven Validation Run (50k)
=====================================
Validates if microstructure-driven reward improves exit discovery.
Continues from phase27_calib/model_10k.zip.
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
    profit_floor_bps=0.5,    # RELAXED as requested
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_quote_presence_bonus=0.001,
    reward_thesis_decay_weight=0.0001, # NEW FEATURE
    override_action_dim=10,
    use_selective_entry=True,
    entry_veto_threshold_bps=0.2,
)

class ThesisCallback(BaseCallback):
    CHECKPOINTS = [25000, 50000]
    
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
                label = f"{next_cp // 1000}k"
                print(f"\n[THESIS] Step {step}: Saving checkpoint {label}...")
                
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
        print(f"  -- Running Thesis Audit at {label}...")
        try:
            report = run_ppo_audit(
                model_path=model_path,
                venv_path=venv_path,
                dataset_id="golden_l2_v1_val",
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
            print(f"\n[THESIS VALIDATION SCORECARD - {label}]")
            print(f"  Trades:           {report.get('total_trades', 0)}")
            print(f"  Net PnL %:        {report.get('net_pnl', 0):.4f}%")
            print(f"  Thesis Decay Tot: {lc.get('total_thesis_decay', 0):.4f}")
            print(f"  Invalid Actor RL: {lc.get('invalid_action_rate', 0):.2f}%")
            print(f"  CLOSE (W/ POS):   {lc.get('close_with_pos', 0)}")
            print(f"  CLOSE (FLAT):     {lc.get('close_flat', 0)}")
            print(f"  Coincident Exits: {lc.get('exit_coincident_with_decay', 0)}")
            print(f"  Action Usage:     HOLD={usage.get('HOLD',0)}, OPEN={usage.get('OPEN_LONG',0)+usage.get('OPEN_SHORT',0)}")
            print(f"  ------------------------------")
        except Exception as e:
            print(f"  Audit FAILED: {e}")
            import traceback
            traceback.print_exc()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_steps", type=int, default=50000)
    parser.add_argument("--load", type=str, default="python/runs_train/phase27_calib/model_10k.zip")
    parser.add_argument("--venv", type=str, default="python/runs_train/phase27_calib/venv_10k.pkl")
    parser.add_argument("--out", type=str, default="python/runs_train/vnext_thesis_validation")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)

    def make_env():
        return GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            fill_model=1, 
            decision_interval_ms=1000,
            **THESIS_CONFIG
        )

    venv = DummyVecEnv([make_env])
    
    if os.path.exists(args.venv):
        print(f"[THESIS] Loading venv stats from {args.venv}")
        venv = VecNormalize.load(args.venv, venv)
    else:
        venv = VecNormalize(venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[THESIS] Loading base model from {args.load}")
    model = PPO.load(args.load, env=venv, device=device)
    
    model.ent_coef = 0.05
    model.learning_rate = 2e-5
    
    callback = ThesisCallback(args.out)
    print(f"[THESIS] Starting training for {args.train_steps} steps...")
    model.learn(total_timesteps=args.train_steps, callback=callback, reset_num_timesteps=True, progress_bar=True)
    print("[THESIS] Complete.")

if __name__ == "__main__":
    main()
