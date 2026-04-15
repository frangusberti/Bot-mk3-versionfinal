"""
PPO Action Masking Pilot
========================
Trains MaskablePPO from scratch for 25k steps to validate that
client-side action masking eliminates invalid actions
(especially CLOSE while flat) without killing maker activity.
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

# -- Config (identical to Variant B viability) --
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
    reward_quote_presence_bonus=0.0,
    reward_thesis_decay_weight=0.0001,
    override_action_dim=10,
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
    """Extract action mask from the environment."""
    return env.action_masks()


def run_masked_eval(model, eval_env, steps=10000):
    """Run evaluation with action masking and collect metrics."""
    obs = eval_env.reset()
    
    actions_counter = Counter()
    total_trades = 0
    total_invalid = 0
    total_steps_done = 0
    total_masked_action = 0
    total_invalid_close_flat = 0
    total_invalid_open_marketable = 0
    total_resting_fills = 0
    total_immediate_fills = 0
    total_accepted_passive = 0
    # Extended metrics
    last_equity = 0.0
    last_realized_pnl = 0.0
    last_fees_paid = 0.0
    last_realized_pnl_total = 0.0
    sum_avg_win_hold = 0.0
    sum_avg_loss_hold = 0.0
    hold_samples = 0
    total_thesis_decay = 0.0
    close_with_pos = 0
    close_flat_count = 0

    CLOSE_ACTIONS = {4, 8}  # CLOSE_LONG=4, CLOSE_SHORT=8

    for _ in range(steps):
        # Get action masks through the vec env
        masks = eval_env.env_method("action_masks")
        action_masks_np = np.array(masks)
        
        action, _ = model.predict(obs, deterministic=True, action_masks=action_masks_np)
        act_int = int(action[0])
        actions_counter[act_int] += 1

        obs, reward, done, info = eval_env.step(action)
        info0 = info[0]
        total_steps_done += 1

        if info0.get("is_invalid", False):
            total_invalid += 1
        total_trades += info0.get("trades_executed", 0)
        total_masked_action += info0.get("masked_action_count", 0)
        total_invalid_close_flat += info0.get("invalid_close_flat", 0)
        total_invalid_open_marketable += info0.get("invalid_open_marketable", 0)
        total_resting_fills += info0.get("resting_fill_count", 0)
        total_immediate_fills += info0.get("immediate_fill_count", 0)
        total_accepted_passive += info0.get("accepted_as_passive_count", 0)

        # Extended: PnL / fees / equity
        last_equity = info0.get("equity", last_equity)
        last_realized_pnl = info0.get("realized_pnl", last_realized_pnl)
        last_fees_paid = info0.get("fees_paid", last_fees_paid)
        last_realized_pnl_total = info0.get("realized_pnl_total", last_realized_pnl_total)

        # Hold times (per-step averages from server)
        w = info0.get("avg_win_hold_ms", 0.0)
        l = info0.get("avg_loss_hold_ms", 0.0)
        if w > 0 or l > 0:
            sum_avg_win_hold += w
            sum_avg_loss_hold += l
            hold_samples += 1

        # Thesis decay
        total_thesis_decay += info0.get("thesis_decay_penalty", 0.0)

        # Close breakdown
        if act_int in CLOSE_ACTIONS:
            pos_qty = info0.get("position_qty", 0.0)
            if abs(pos_qty) > 1e-12:
                close_with_pos += 1
            else:
                close_flat_count += 1

    invalid_rate = (total_invalid / total_steps_done * 100) if total_steps_done > 0 else 0

    # Action distribution
    total_actions = sum(actions_counter.values()) or 1
    usage = {}
    for i, label in enumerate(ACTION_LABELS):
        usage[label] = actions_counter.get(i, 0) / total_actions * 100

    net_pnl = last_realized_pnl - last_fees_paid
    avg_pnl = (net_pnl / total_trades) if total_trades > 0 else 0.0

    return {
        "invalid_rate": invalid_rate,
        "masked_action_chosen_count": total_masked_action,
        "invalid_close_flat_count": total_invalid_close_flat,
        "invalid_open_marketable_count": total_invalid_open_marketable,
        "total_trades": total_trades,
        "maker_fills": total_resting_fills,
        "immediate_fills": total_immediate_fills,
        "accepted_passive": total_accepted_passive,
        "action_usage": usage,
        "total_steps": total_steps_done,
        # Extended
        "net_pnl_after_fees": round(net_pnl, 4),
        "realized_pnl_total": round(last_realized_pnl_total, 4),
        "realized_pnl_cumulative": round(last_realized_pnl, 4),
        "fees_paid_total": round(last_fees_paid, 4),
        "equity_final": round(last_equity, 4),
        "avg_pnl_per_trade": round(avg_pnl, 6),
        "avg_win_hold_ms": round(sum_avg_win_hold / max(hold_samples, 1), 1),
        "avg_loss_hold_ms": round(sum_avg_loss_hold / max(hold_samples, 1), 1),
        "close_with_pos": close_with_pos,
        "close_flat": close_flat_count,
        "thesis_decay_total": round(total_thesis_decay, 6),
    }


def print_scorecard(report):
    usage = report["action_usage"]
    print("\n" + "="*50)
    print("--- MASKING PILOT SCORECARD ---")
    print("="*50)
    print(f"Invalid Rate:             {report['invalid_rate']:.2f}%")
    print(f"Masked Action (server):   {report['masked_action_chosen_count']}")
    print(f"  > Close Flat:           {report['invalid_close_flat_count']}")
    print(f"  > Open Marketable:      {report['invalid_open_marketable_count']}")
    print(f"Total Trades:             {report['total_trades']}")
    print(f"Maker Fills:              {report['maker_fills']}")
    print(f"Accepted Passive:         {report['accepted_passive']}")
    print("-" * 30)
    print(f"HOLD:                     {usage.get('HOLD', 0):.2f}%")
    open_pct = usage.get('OPEN_LONG', 0) + usage.get('OPEN_SHORT', 0)
    print(f"OPEN (L+S):               {open_pct:.2f}%")
    close_pct = usage.get('CLOSE_LONG', 0) + usage.get('CLOSE_SHORT', 0)
    print(f"CLOSE (L+S):              {close_pct:.2f}%")
    print(f"REPRICE:                  {usage.get('REPRICE', 0):.2f}%")
    add_pct = usage.get('ADD_LONG', 0) + usage.get('ADD_SHORT', 0)
    reduce_pct = usage.get('REDUCE_LONG', 0) + usage.get('REDUCE_SHORT', 0)
    print(f"ADD (L+S):                {add_pct:.2f}%")
    print(f"REDUCE (L+S):             {reduce_pct:.2f}%")
    print("="*50)


def main():
    out_dir = "python/runs_train/masking_pilot"
    os.makedirs(out_dir, exist_ok=True)
    
    rss_start = psutil.Process().memory_info().rss / 1024 / 1024
    print(f"[MEMORY] Start RSS: {rss_start:.1f} MB")

    # --- Training env ---
    print("\n[MASKING PILOT] Setting up training env...")
    def make_train_env():
        env = GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            **CONFIG
        )
        return ActionMasker(env, mask_fn)

    train_venv = DummyVecEnv([make_train_env])
    train_venv = VecNormalize(train_venv, norm_obs=True, norm_reward=False, clip_obs=10.0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"[MASKING PILOT] Creating MaskablePPO (device={device})...")
    
    model = MaskablePPO(
        "MlpPolicy",
        train_venv,
        learning_rate=2e-4,
        ent_coef=0.03,
        n_steps=2048,
        batch_size=64,
        n_epochs=10,
        verbose=0,
        device=device,
    )

    train_steps = 25000
    print(f"[MASKING PILOT] Training for {train_steps} steps...")
    model.learn(total_timesteps=train_steps, progress_bar=False)

    model_path = os.path.join(out_dir, "model_masking.zip")
    venv_path = os.path.join(out_dir, "venv_masking.pkl")
    model.save(model_path)
    train_venv.save(venv_path)

    # --- Eval env ---
    print("[MASKING PILOT] Running evaluation (10k steps)...")
    def make_eval_env():
        env = GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="stage2_eval",
            symbol="BTCUSDT",
            **CONFIG
        )
        return ActionMasker(env, mask_fn)

    eval_venv = DummyVecEnv([make_eval_env])
    eval_venv = VecNormalize.load(venv_path, eval_venv)
    eval_venv.training = False
    eval_venv.norm_reward = False

    eval_model = MaskablePPO.load(model_path, env=eval_venv, device=device)

    report = run_masked_eval(eval_model, eval_venv, steps=10000)

    with open(os.path.join(out_dir, "report_masking_pilot.json"), "w") as f:
        json.dump(report, f, indent=2)

    print_scorecard(report)

    rss_end = psutil.Process().memory_info().rss / 1024 / 1024
    print(f"\n[MEMORY] End RSS: {rss_end:.1f} MB (delta: {rss_end - rss_start:+.1f} MB)")


if __name__ == "__main__":
    main()
