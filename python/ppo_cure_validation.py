"""
PPO Presence Bonus Cure Validation
==================================
Runs 25k steps from model_50k.zip with capped and reduced presence bonus.
"""
import os
import sys
import json
import torch
import numpy as np

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv
from ppo_eval_checkpoint import run_ppo_audit

# -- Cure Config --
CURE_CONFIG = dict(
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
    reward_quote_presence_bonus=0.0001, # REDUCED from 0.001
    reward_thesis_decay_weight=0.0001,
    override_action_dim=10,
    use_selective_entry=True,
    entry_veto_threshold_bps=0.2,
    micro_strict=False,
    fill_model=2, # Realistic fills for economic verification
)

def main():
    out_dir = "python/runs_train/vnext_cure_validation"
    os.makedirs(out_dir, exist_ok=True)
    
    model_path = "python/runs_train/vnext_thesis_validation/model_50k.zip"
    venv_path = "python/runs_train/vnext_thesis_validation/venv_50k.pkl"
    
    # 1. Run Audit directly using the model
    print(f"[CURE] Running 25k step validation audit from {model_path}...")
    
    report = run_ppo_audit(
        model_path=model_path,
        venv_path=venv_path,
        dataset_id="stage2_eval",
        steps_per_eval=25000,
        server="localhost:50051",
        **CURE_CONFIG
    )
    
    report_path = os.path.join(out_dir, "report_cure_25k.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    
    # 2. Extract and Print Metrics
    lc = report.get("lifecycle", {})
    usage = lc.get("action_usage_detailed", {})
    
    print("\n" + "="*40)
    print("CURE VALIDATION SCORECARD (25k STEPS)")
    print("="*40)
    print(f"Total Trades:      {report.get('total_trades', 0)}")
    print(f"Net PnL %:         {report.get('net_pnl', 0):.4f}%")
    print(f"Invalid Rate:      {lc.get('invalid_action_rate', 0):.2f}%")
    print("-" * 20)
    print(f"HOLD:              {usage.get('HOLD', 0):.2f}%")
    print(f"OPEN_LONG:         {usage.get('OPEN_LONG', 0):.2f}%")
    print(f"REPRICE:           {usage.get('REPRICE', 0):.2f}%")
    print(f"CLOSE_LONG:        {usage.get('CLOSE_LONG', 0):.2f}%")
    print("-" * 20)
    print(f"Thesis Decay Tot:  {lc.get('thesis_decay_total', 0):.4f}")
    print(f"Close w/ Pos:      {lc.get('close_with_pos', 0)}")
    print(f"Avg Active Orders: {lc.get('side_distribution', {}).get('avg_qty', 0):.4f} (Proxy)")
    print("="*40)

if __name__ == "__main__":
    main()
