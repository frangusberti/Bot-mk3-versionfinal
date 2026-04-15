"""
PPO Reward Architecture A/B Test
==================================
Runs 25k steps from model_50k.zip comparing:
- Variant A: Legacy Cured Reward (presence bonus capped/reduced)
- Variant B: Consolidated Economic Reward
"""
import os
import sys
import json

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from ppo_eval_checkpoint import run_ppo_audit

# -- Variant A Config (Cured Legacy) --
VARIANT_A_CONFIG = dict(
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
    fill_model=1, # Legacy optimistic fills per user instruction
    reward_consolidated_variant=False, # <-- VARIANT A
)

# -- Variant B Config (Consolidated Economic) --
VARIANT_B_CONFIG = VARIANT_A_CONFIG.copy()
VARIANT_B_CONFIG["reward_consolidated_variant"] = True # <-- VARIANT B


def print_scorecard(name: str, report: dict):
    lc = report.get("lifecycle", {})
    usage = lc.get("action_usage_detailed", {})
    
    print("\n" + "="*50)
    print(f"--- {name} SCORECARD ---")
    print("="*50)
    print(f"Net PnL After Fees: {report.get('net_pnl', 0):.4f}%")
    print(f"Total Trades:       {report.get('total_trades', 0)}")
    print(f"Invalid Rate:       {lc.get('invalid_action_rate', 0):.2f}%")
    print(f"Soft Veto Count:    {lc.get('total_soft_vetoes', 0)}")
    print(f"Hard Invalid Count: {lc.get('total_hard_invalid', 0)}")
    print("-" * 30)
    print(f"HOLD:               {usage.get('HOLD', 0):.2f}%")
    print(f"OPEN (L+S):         {usage.get('OPEN_LONG', 0) + usage.get('OPEN_SHORT', 0):.2f}%")
    print(f"REPRICE:            {usage.get('REPRICE', 0):.2f}%")
    print(f"CLOSE (L+S):        {usage.get('CLOSE_LONG', 0) + usage.get('CLOSE_SHORT', 0):.2f}%")
    print("-" * 30)
    print(f"Adverse Selection:  (Deferred MTM penalty captured in PnL)") # Note: We don't track total AS individually in Python yet, but we have Thesis Decay
    print(f"Thesis Decay Total: {lc.get('thesis_decay_total', 0):.4f}")
    print(f"Avg Hold Time (W):  {lc.get('avg_win_hold_ms', 0):.1f} ms")
    print(f"Avg Hold Time (L):  {lc.get('avg_loss_hold_ms', 0):.1f} ms")
    print("="*50)

def main():
    out_dir = "python/runs_train/vnext_reward_ab_test"
    os.makedirs(out_dir, exist_ok=True)
    
    model_path = "python/runs_train/vnext_thesis_validation/model_50k.zip"
    venv_path = "python/runs_train/vnext_thesis_validation/venv_50k.pkl"
    steps = 25000
    
    # 1. Variant A (Cured)
    print(f"\n>>> Running Variant A (Cured Legacy) for {steps} steps...")
    report_a = run_ppo_audit(
        model_path=model_path,
        venv_path=venv_path,
        dataset_id="stage2_eval",
        steps_per_eval=steps,
        server="localhost:50051",
        **VARIANT_A_CONFIG
    )
    with open(os.path.join(out_dir, "report_variant_a.json"), "w") as f:
        json.dump(report_a, f, indent=2)
        
    print_scorecard("VARIANT A (CURED)", report_a)
    
    # 2. Variant B (Consolidated Economic)
    print(f"\n>>> Running Variant B (Consolidated Economic) for {steps} steps...")
    report_b = run_ppo_audit(
        model_path=model_path,
        venv_path=venv_path,
        dataset_id="stage2_eval",
        steps_per_eval=steps,
        server="localhost:50051",
        **VARIANT_B_CONFIG
    )
    with open(os.path.join(out_dir, "report_variant_b.json"), "w") as f:
        json.dump(report_b, f, indent=2)
        
    print_scorecard("VARIANT B (CONSOLIDATED)", report_b)

    print("\n>>> A/B Test Complete.")

if __name__ == "__main__":
    main()
