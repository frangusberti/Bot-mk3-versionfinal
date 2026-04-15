"""
Exit Gate A/B Audit
===================
Rama A: profit_floor_bps = 0.5
Rama B: profit_floor_bps = 0.0

Compares behavior and economy to determine if the profit floor is the primary cause of 'Inventory Juggling'.
"""
import os
import sys
import torch
import numpy as np
from collections import Counter
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from sb3_contrib.common.wrappers import ActionMasker

# -- Config --
BASE_CONFIG = dict(
    dataset_id="golden_l2_v1_train",
    symbol="BTCUSDT",
    random_start_offset=False, # Stabilized start for A/B comparison
    max_daily_dd=0.05,
    close_position_loss_threshold=0.003,
    min_post_offset_bps=0.2,
    imbalance_block_threshold=0.6,
    post_delta_threshold_bps=0.5,
    stop_loss_bps=30.0,
    fill_model=2, # Same as pilot
)

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG",
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
]

def mask_fn(env: GrpcTradingEnv) -> np.ndarray:
    return env.action_masks()

def run_eval(model, venv, steps=5000):
    obs = venv.reset()
    actions_counter = Counter()
    total_trades = 0
    total_invalid = 0
    close_with_pos = 0
    episodes_count = 0
    episodes_flat_at_done = 0
    
    # Telemetry
    last_realized_pnl = 0.0
    last_fees_paid = 0.0
    sum_win_hold = 0.0
    sum_loss_hold = 0.0
    hold_samples = 0
    
    exit_blocks = Counter()

    for i in range(steps):
        masks = venv.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        act_int = int(action[0])
        actions_counter[act_int] += 1
        
        obs, reward, done, info = venv.step(action)
        info0 = info[0]
        
        total_trades += info0.get("trades_executed", 0)
        
        if act_int in {4, 8}: # CLOSE
            # Use prev state logic for close_with_pos if needed, but 
            # simplest is to check if it was blocked or actually traded.
            pass

        # Collect exit block buckets from action_counts
        counts = info0.get("action_counts", {})
        for k in ["EXIT_BLOCKED_NEG", "EXIT_BLOCKED_0_TO_05", "EXIT_BLOCKED_05_TO_2", "EXIT_BLOCKED_GT_2"]:
            if k in counts:
                exit_blocks[k] = counts[k] # Last value seen is the total cumulative for the episode

        last_realized_pnl = info0.get("realized_pnl", last_realized_pnl)
        last_fees_paid = info0.get("fees_paid", last_fees_paid)
        
        w = info0.get("avg_win_hold_ms", 0)
        l = info0.get("avg_loss_hold_ms", 0)
        if w > 0 or l > 0:
            sum_win_hold += w
            sum_loss_hold += l
            hold_samples += 1

        if done:
            episodes_count += 1
            pos_qty = info0.get("position_qty", 0.0)
            if abs(pos_qty) < 1e-9:
                episodes_flat_at_done += 1

    usage = {label: (actions_counter.get(i, 0) / steps * 100) for i, label in enumerate(ACTION_LABELS)}
    
    return {
        "actions": usage,
        "total_trades": total_trades,
        "flat_at_done_pct": (episodes_flat_at_done / max(episodes_count, 1) * 100),
        "net_pnl": last_realized_pnl - last_fees_paid,
        "realized_pnl": last_realized_pnl,
        "avg_win_hold": sum_win_hold / max(hold_samples, 1),
        "avg_loss_hold": sum_loss_hold / max(hold_samples, 1),
        "exit_blocks": exit_blocks,
        "episodes": episodes_count
    }

def print_result(label, res):
    print(f"\n--- {label} ---")
    print(f"Action Usage:")
    for a in ["HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG"]:
        print(f"  {a:12}: {res['actions'][a]:.2f}%")
    print(f"Total Trades:      {res['total_trades']}")
    print(f"Episodes:          {res['episodes']}")
    print(f"% Flat at Done:    {res['flat_at_done_pct']:.2f}%")
    print(f"Net PnL:           {res['net_pnl']:.4f}")
    print(f"Realized PnL:      {res['realized_pnl']:.4f}")
    print(f"Avg Hold Win/Loss: {res['avg_win_hold']:.0f} / {res['avg_loss_hold']:.0f} ms")
    print(f"Exit Blocked (profit_floor):")
    for k, v in res['exit_blocks'].items():
        print(f"  {k:20}: {v}")

def main():
    model_path = "python/runs_train/itr_serious_50k/model_itr_serious.zip"
    venv_path = "python/runs_train/itr_serious_50k/venv_itr_serious.pkl"
    
    if not os.path.exists(model_path):
        print(f"MODEL NOT FOUND: {model_path}")
        return

    print(f"Loading model: {model_path}")
    model = MaskablePPO.load(model_path)
    
    def make_env(floor):
        cfg = BASE_CONFIG.copy()
        cfg["profit_floor_bps"] = floor
        env = GrpcTradingEnv(server_addr="localhost:50051", **cfg)
        return ActionMasker(env, mask_fn)

    # --- RAMA A (0.5 bps) ---
    print("\n[A/B AUDIT] Running Rama A (Floor = 0.5 bps)...")
    venv_a = DummyVecEnv([lambda: make_env(0.5)])
    venv_a = VecNormalize.load(venv_path, venv_a)
    venv_a.training = False
    res_a = run_eval(model, venv_a, steps=5000)
    print_result("RAMA A (Floor = 0.5)", res_a)

    # --- RAMA B (0.0 bps) ---
    print("\n[A/B AUDIT] Running Rama B (Floor = 0.0 bps)...")
    venv_b = DummyVecEnv([lambda: make_env(0.0)])
    venv_b = VecNormalize.load(venv_path, venv_b)
    venv_b.training = False
    res_b = run_eval(model, venv_b, steps=5000)
    print_result("RAMA B (Floor = 0.0)", res_b)

if __name__ == "__main__":
    main()
