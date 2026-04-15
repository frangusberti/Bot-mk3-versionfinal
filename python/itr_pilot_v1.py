"""
ITR Smoke Pilot v1
==================
Trains MaskablePPO for 25k steps using Schema v7 (166 dimensions).
Validates behavior shift and feature warmup before reward/exit adjustments.
"""
import os
import sys
import json
import torch
import numpy as np
import psutil
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from grpc_env import GrpcTradingEnv

# -- Pilot Config --
CONFIG = dict(
    dataset_id="golden_l2_v1_train",
    symbol="BTCUSDT",
    random_start_offset=True,
    max_daily_dd=0.05,
    # Standard gates
    close_position_loss_threshold=0.003,
    min_post_offset_bps=0.2,
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    # Reward (no changes as requested)
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_thesis_decay_weight=0.0001,
    use_selective_entry=True,
    entry_veto_threshold_bps=0.2,
    micro_strict=False,
    fill_model=2,
    reward_consolidated_variant=True,
)

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
]

def mask_fn(env: GrpcTradingEnv) -> np.ndarray:
    return env.action_masks()

def run_itr_eval(model, eval_env, steps=10000):
    obs = eval_env.reset()
    
    actions_counter = Counter()
    total_trades = 0
    total_invalid = 0
    total_steps_done = 0
    total_resting_fills = 0
    close_with_pos = 0
    
    # Feature validity stats
    validity_at_steps = {0: [], 60: [], 300: [], 900: []}
    
    # PnL / Fees
    last_realized_pnl = 0.0
    last_fees_paid = 0.0
    sum_avg_win_hold = 0.0
    sum_avg_loss_hold = 0.0
    hold_samples = 0
    
    episodes_count = 0
    episodes_with_pos_at_done = 0

    for i in range(steps):
        masks = eval_env.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        
        act_int = int(action[0])
        actions_counter[act_int] += 1
        
        # Capture feature validity (Step 0 is special as it happens after reset)
        # We'll use the observation we just got
        step_in_ep = total_steps_done % 2048 # Approx, better to get from info if possible
        # Actually total_steps_done since reset for this specific episode. 
        # But we only have one env in DummyVecEnv.
        
        # Let's track internal step count
        curr_step = total_steps_done
        
        obs_vec = obs[0] if isinstance(obs, np.ndarray) else obs
        mask_vec = obs_vec[83:166]
        valid_pct = (np.sum(mask_vec > 0.5) / 83.0) * 100.0
        
        # We'll track validity based on 'ts' or 'step_count' from server if available
        # For simplicity, we'll just check if it's one of the target steps
        # This only works if we don't reset in the middle of eval steps or we handle it.
        
        obs, reward, done, info = eval_env.step(action)
        info0 = info[0]
        total_steps_done += 1
        
        # Accurate step counting per episode
        # info0['ts'] is available, but let's use a simple counter for now
        # because the user asked for 1m, 5m, 15m (60, 300, 900 steps)
        
        # Collect validity at intervals
        # Note: In DummyVecEnv, 'done' resets automagically.
        # We need to detect reset to restart our local counter.
        
        if info0.get("is_invalid", False):
            total_invalid += 1
        total_trades += info0.get("trades_executed", 0)
        total_resting_fills += info0.get("resting_fill_count", 0)
        
        if act_int in {4, 8}: # CLOSE
            pos_qty = info0.get("position_qty", 0.0)
            if abs(pos_qty) > 1e-12:
                close_with_pos += 1

        last_realized_pnl = info0.get("realized_pnl", last_realized_pnl)
        last_fees_paid = info0.get("fees_paid", last_fees_paid)
        
        w = info0.get("avg_win_hold_ms", 0.0)
        l = info0.get("avg_loss_hold_ms", 0.0)
        if w > 0 or l > 0:
            sum_avg_win_hold += w
            sum_avg_loss_hold += l
            hold_samples += 1

        if done:
            episodes_count += 1
            pos_qty = info0.get("position_qty", 0.0)
            if abs(pos_qty) > 1e-12:
                episodes_with_pos_at_done += 1

    # Simple scorecard
    usage = {label: (actions_counter.get(i, 0) / steps * 100) for i, label in enumerate(ACTION_LABELS)}
    
    return {
        "invalid_rate": (total_invalid / steps * 100),
        "action_usage": usage,
        "total_trades": total_trades,
        "maker_fills": total_resting_fills,
        "open_pos_at_done_pct": (episodes_with_pos_at_done / max(episodes_count, 1) * 100),
        "close_with_pos": close_with_pos,
        "net_pnl_after_fees": last_realized_pnl - last_fees_paid,
        "realized_pnl": last_realized_pnl,
        "fees_total": last_fees_paid,
        "avg_win_hold_ms": sum_avg_win_hold / max(hold_samples, 1),
        "avg_loss_hold_ms": sum_avg_loss_hold / max(hold_samples, 1),
    }

def main():
    out_dir = "python/runs_train/itr_pilot_v1"
    os.makedirs(out_dir, exist_ok=True)

    print("\n[ITR PILOT] Setting up training env (25k steps, Schema v7)...")
    def make_env(dataset):
        # Avoid duplicating dataset_id/symbol if they are in CONFIG
        local_cfg = CONFIG.copy()
        local_cfg.pop("dataset_id", None)
        local_cfg.pop("symbol", None)
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
        learning_rate=2e-4,
        ent_coef=0.03,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        verbose=1,
        device="cuda" if torch.cuda.is_available() else "cpu",
    )

    print(f"[ITR PILOT] Starting training...")
    model.learn(total_timesteps=25000)
    
    model.save(os.path.join(out_dir, "model_itr.zip"))
    train_venv.save(os.path.join(out_dir, "venv_itr.pkl"))

    print("\n[ITR PILOT] Running evaluation (10k steps)...")
    eval_venv = DummyVecEnv([lambda: make_env("stage2_eval")])
    eval_venv = VecNormalize.load(os.path.join(out_dir, "venv_itr.pkl"), eval_venv)
    eval_venv.training = False
    
    report = run_itr_eval(model, eval_venv, steps=10000)
    
    print("\n" + "="*50)
    print("      ITR SMOKE PILOT SCORECARD")
    print("="*50)
    print(f"Invalid Rate:             {report['invalid_rate']:.2f}%")
    print(f"Total Trades:             {report['total_trades']}")
    print(f"Maker Fills:              {report['maker_fills']}")
    print(f"Net PnL (after fees):     {report['net_pnl_after_fees']:.4f}")
    print(f"Realized PnL:             {report['realized_pnl']:.4f}")
    print(f"Fees Total:               {report['fees_total']:.4f}")
    print(f"Avg Win Hold (ms):        {report['avg_win_hold_ms']:.0f}")
    print(f"Avg Loss Hold (ms):       {report['avg_loss_hold_ms']:.0f}")
    print(f"Episodes w/ Position at Done: {report['open_pos_at_done_pct']:.2f}%")
    print(f"Close with Position:      {report['close_with_pos']}")
    print("-" * 30)
    for label, pct in report['action_usage'].items():
        print(f"{label:15}: {pct:.2f}%")
    print("="*50)

    # Manual Validity Check (Last obs)
    obs = eval_venv.reset()
    for t in [0, 60, 300, 900]:
        # Fast forward
        for _ in range(t):
           obs, _, _, _ = eval_venv.step([0]) # HOLD
        masks = obs[0][83:166]
        valid_pct = (np.sum(masks > 0.5) / 83.0) * 100.0
        print(f"Feature Validity at {t//60}m: {valid_pct:.1f}%")

if __name__ == "__main__":
    main()
