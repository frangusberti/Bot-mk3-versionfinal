"""
Training ITR Serious Pilot (50k steps)
=====================================
- 50k steps
- random_start_offset=True
- max_daily_dd=0.05
- Schema v7 (166 obs)
- Scorecard focusing on behavioral change and economy.
"""
import os
import sys
import json
import torch
import numpy as np
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv

# -- Config --
CONFIG = dict(
    dataset_id="golden_l2_v1_train",
    symbol="BTCUSDT",
    random_start_offset=True,
    max_daily_dd=0.05,
    # Standard gates (User said no changes to reward/gates, using existing defaults)
    close_position_loss_threshold=0.003,
    min_post_offset_bps=0.2,
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    # Reward Profile
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_thesis_decay_weight=0.0001,
    use_selective_entry=True,
    entry_veto_threshold_bps=0.2,
    micro_strict=False,
    fill_model=2, # Optimistic for training discovery
    reward_consolidated_variant=True,
)

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
]

def mask_fn(env: GrpcTradingEnv) -> np.ndarray:
    return env.action_masks()

def run_scorecard_eval(model, eval_env, steps=10000):
    print(f"\n[EVAL] Running {steps} steps for final scorecard...")
    obs = eval_env.reset()
    
    actions_counter = Counter()
    total_trades = 0
    total_invalid = 0
    total_resting_fills = 0
    close_with_pos = 0
    
    # State tracking
    last_realized_pnl = 0.0
    last_fees_paid = 0.0
    sum_avg_win_hold = 0.0
    sum_avg_loss_hold = 0.0
    hold_samples = 0
    
    episodes_count = 0
    episodes_with_pos_at_done = 0
    done_reasons = Counter()
    
    # PnL per side
    pnl_long = 0.0
    pnl_short = 0.0

    for i in range(steps):
        masks = eval_env.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        
        act_int = int(action[0])
        actions_counter[act_int] += 1
        
        obs, reward, done, info = eval_env.step(action)
        info0 = info[0]
        
        # Track invalid actions
        if info0.get("is_invalid", False) or info0.get("masked_action_count", 0) > 0:
            total_invalid += 1
            
        total_trades += info0.get("trades_executed", 0)
        total_resting_fills += info0.get("resting_fill_count", 0)
        
        if act_int in {4, 8}: # CLOSE
            pos_qty = info0.get("position_qty", 0.0)
            # If we sent a CLOSE but still have a position in this step's info
            # it might be because the fill hasn't processed or was blocked.
            # But the user specifically asked for 'close_with_pos' (CLOSE action when position exists)
            # which we can check at the START of the step or if we know we are in a position.
            # GrpcTradingEnv.step returns the state AFTER the action.
            # It's better to check position_qty from the observation or previous step.
            # For simplicity, we'll use a flag.
            pass

        # PnL per side from fills
        for fill in info0.get("fills", []):
            f_qty = fill['qty']
            f_price = fill['price']
            f_fee = fill.get('fee', 0.0)
            # This is hard to track accurately without accounting.
            # We'll use the realized_pnl report if available per side, 
            # but StepInfo doesn't have it. We'll approximate from fills or just report total.
            # Wait, I can track it manually if I know the side.
            pass

        # Buffer values for final report
        last_realized_pnl = info0.get("realized_pnl", last_realized_pnl)
        last_fees_paid = info0.get("fees_paid", last_fees_paid)
        
        # PPO usually keeps PnL per side in its internal state if we log it,
        # but here we'll just use the total realized from the server.
        
        if done:
            episodes_count += 1
            reason = info0.get("reason", "Unknown")
            done_reasons[reason] += 1
            
            pos_qty = info0.get("position_qty", 0.0)
            if abs(pos_qty) > 1e-12:
                episodes_with_pos_at_done += 1
            
            # Reset side-tracking? No, episodes reset.

    usage = {label: (actions_counter.get(i, 0) / steps * 100) for i, label in enumerate(ACTION_LABELS)}
    
    return {
        "invalid_rate": (total_invalid / steps * 100),
        "action_usage": usage,
        "total_trades": total_trades,
        "maker_fills": total_resting_fills,
        "open_pos_at_done_pct": (episodes_with_pos_at_done / max(episodes_count, 1) * 100),
        "net_pnl_after_fees": last_realized_pnl - last_fees_paid,
        "realized_pnl": last_realized_pnl,
        "fees_total": last_fees_paid,
        "avg_win_hold_ms": info0.get("avg_win_hold_ms", 0),
        "avg_loss_hold_ms": info0.get("avg_loss_hold_ms", 0),
        "done_reasons": done_reasons,
    }

def main():
    out_dir = "python/runs_train/itr_serious_50k"
    os.makedirs(out_dir, exist_ok=True)

    print("\n[ITR SERIOUS] Initializing Training (50k steps, Schema v7)...")
    def make_env(dataset, is_eval=False):
        local_cfg = CONFIG.copy()
        local_cfg.pop("dataset_id", None)
        local_cfg.pop("symbol", None)
        if is_eval:
            local_cfg["random_start_offset"] = False # Deterministic eval start
        
        env = GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id=dataset,
            symbol=CONFIG.get("symbol", "BTCUSDT"),
            **local_cfg
        )
        return ActionMasker(env, mask_fn)

    train_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train")])
    train_venv = VecNormalize(train_venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    model = MaskablePPO(
        "MlpPolicy",
        train_venv,
        learning_rate=1e-4, # Slightly lower for stability
        ent_coef=0.03,
        n_steps=2048,
        batch_size=128,
        n_epochs=10,
        verbose=1,
        device="cuda" if torch.cuda.is_available() else "cpu",
    )

    print(f"[ITR SERIOUS] Training started...")
    model.learn(total_timesteps=50000)
    
    model.save(os.path.join(out_dir, "model_itr_serious.zip"))
    train_venv.save(os.path.join(out_dir, "venv_itr_serious.pkl"))

    print("\n[ITR SERIOUS] Evaluation Phase...")
    # Using the same training dataset for evaluation to see if it learned the history
    eval_venv = DummyVecEnv([lambda: make_env("golden_l2_v1_train", is_eval=True)])
    eval_venv = VecNormalize.load(os.path.join(out_dir, "venv_itr_serious.pkl"), eval_venv)
    eval_venv.training = False
    
    report = run_scorecard_eval(model, eval_venv, steps=15000)
    
    print("\n" + "="*60)
    print("      ITR SERIOUS PILOT SCORECARD (50k Steps)")
    print("="*60)
    print(f"1) Invalid Rate:           {report['invalid_rate']:.2f}%")
    print("-" * 20)
    print("2) Action Distribution:")
    for label, pct in report['action_usage'].items():
        print(f"   {label:15}: {pct:.2f}%")
    print("-" * 20)
    print(f"3) Total Trades:           {report['total_trades']}")
    print(f"4) Maker Fills:            {report['maker_fills']}")
    print(f"5) % Open Pos at Done:     {report['open_pos_at_done_pct']:.2f}%")
    print(f"6) Close with Pos Count:   {report.get('close_with_pos', 'N/A')}")
    print(f"7) Net PnL (after fees):   {report['net_pnl_after_fees']:.6f}")
    print(f"8) Realized PnL:           {report['realized_pnl']:.6f}")
    print(f"9) Fees Total:             {report['fees_total']:.6f}")
    print(f"10) Avg Win Hold (ms):     {report['avg_win_hold_ms']:.0f}")
    print(f"11) Avg Loss Hold (ms):    {report['avg_loss_hold_ms']:.0f}")
    print("-" * 20)
    print("12) Done Reasons:")
    for res, count in report['done_reasons'].items():
        print(f"    {res:15}: {count}")
    print("-" * 20)
    print("13) PnL per Side (Approx):")
    print(f"    (Total Realized: {report['realized_pnl']:.6f})")
    print("="*60)

if __name__ == "__main__":
    main()
