"""
PPO Execution Viability Diagnostic
==================================
Trains and evaluates Variant B (Consolidated Economic Reward) 
from model_50k.zip using fill_model=2 to diagnose execution bottlenecks.
"""
import os
import sys
import json
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from grpc_env import GrpcTradingEnv
from ppo_eval_checkpoint import run_ppo_audit

# -- Variant B Config --
CONFIG = dict(
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
    reward_quote_presence_bonus=0.0, # Disabled in Variant B
    reward_thesis_decay_weight=0.0001,
    override_action_dim=10,
    use_selective_entry=True,
    entry_veto_threshold_bps=0.2,
    micro_strict=False,
    fill_model=2, # <-- Option 2: Realistic fill model for viability
    reward_consolidated_variant=True, # <-- VARIANT B
)

def print_scorecard(report: dict):
    lc = report.get("lifecycle", {})
    usage = lc.get("action_usage_detailed", {})
    
    print("\n" + "="*50)
    print("--- VIABILITY DIAGNOSTIC SCORECARD ---")
    print("="*50)
    print(f"Net PnL After Fees: {report.get('net_pnl', 0):.4f}%")
    print(f"Total Trades:       {report.get('total_trades', 0)}")
    marketability = report.get("marketability", {})
    print(f"Resting Fills:      {marketability.get('resting_fills', 0)}")
    print(f"Immediate Fills:    {marketability.get('immediate_fills', 0)}")
    print(f"Unknown Fills:      {marketability.get('unknown_fills', 0)}")
    print(f"Accepted Marketable:{marketability.get('accepted_as_marketable', 0)}")
    print(f"Accepted Passive:   {marketability.get('accepted_as_passive', 0)}")
    print(f"Invalid Rate:       {lc.get('invalid_action_rate', 0):.2f}%")
    bd = marketability.get("breakdown", {})
    print(f"  > Masked Chosen:  {bd.get('masked_chosen', 0)}")
    print(f"  > Open Marketable:{bd.get('open_marketable', 0)}")
    print(f"  > Close Flat:     {bd.get('close_flat', 0)}")
    print(f"  > Side Mismatch:  {bd.get('side_mismatch', 0)}")
    print(f"  > Reprice Empty:  {bd.get('reprice_empty', 0)}")
    print(f"Soft Veto Count:    {lc.get('total_soft_vetoes', 0)}")
    print(f"Hard Invalid Count: {lc.get('total_hard_invalid', 0)}")
    print("-" * 30)
    print(f"HOLD:               {usage.get('HOLD', 0):.2f}%")
    print(f"OPEN (L+S):         {usage.get('OPEN_LONG', 0) + usage.get('OPEN_SHORT', 0):.2f}%")
    print(f"REPRICE:            {usage.get('REPRICE', 0):.2f}%")
    print(f"CLOSE (L+S):        {usage.get('CLOSE_LONG', 0) + usage.get('CLOSE_SHORT', 0):.2f}%")
    print("-" * 30)
    print(f"Avg Hold Time (W):  {lc.get('avg_win_hold_ms', 0):.1f} ms")
    print(f"Avg Hold Time (L):  {lc.get('avg_loss_hold_ms', 0):.1f} ms")
    print("="*50)

def main():
    out_dir = "python/runs_train/vnext_viability"
    os.makedirs(out_dir, exist_ok=True)
    
    print("\n[VIABILITY] Setting up environment with fill_model=2...")
    raw_env = GrpcTradingEnv(
        server_addr="localhost:50051", 
        dataset_id="golden_l2_v1_train", 
        symbol="BTCUSDT", 
        **CONFIG
    )
    venv = DummyVecEnv([lambda: raw_env])
    
    base_model_path = "python/runs_train/vnext_thesis_validation/model_50k.zip"
    base_venv_path = "python/runs_train/vnext_thesis_validation/venv_50k.pkl"
    
    venv = VecNormalize.load(base_venv_path, venv)
    venv.training = True 
    venv.norm_reward = True
    
    print(f"[VIABILITY] Loading model {base_model_path}...")
    model = PPO.load(
        base_model_path,
        env=venv,
        device="cuda" if torch.cuda.is_available() else "cpu",
        learning_rate=2e-4, 
        ent_coef=0.03,      
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        verbose=0
    )
    
    steps = 25000
    print(f"[VIABILITY] Training Variant B for {steps} steps...")
    model.learn(total_timesteps=steps, progress_bar=False)
    
    out_model = os.path.join(out_dir, "model_viability.zip")
    out_venv = os.path.join(out_dir, "venv_viability.pkl")
    model.save(out_model)
    venv.save(out_venv)
    
    print(f"[VIABILITY] Running Evaluation Audit (10k steps)...")
    report = run_ppo_audit(
        model_path=out_model,
        venv_path=out_venv,
        dataset_id="stage2_eval",
        steps_per_eval=10000,
        server="localhost:50051",
        **CONFIG
    )
    
    with open(os.path.join(out_dir, "report_viability.json"), "w") as f:
        json.dump(report, f, indent=2)
        
    print_scorecard(report)

if __name__ == "__main__":
    main()
