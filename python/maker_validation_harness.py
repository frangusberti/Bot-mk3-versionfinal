import os
import sys
import numpy as np
import pandas as pd
from tqdm import tqdm
from stable_baselines3 import PPO
from collections import defaultdict

# Ensure paths are correct for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

def run_maker_scorecard(
    model_path, 
    dataset_id="stage2_eval", 
    steps=5000, 
    fill_model_val=0,
    maker_bonus=0.0006,
    mtm_penalty_window=1000,
    mtm_penalty_multiplier=2.0,
    reprice_penalty=0.00005,
    post_threshold=0.05
):
    """
    Run a detailed evaluation to assess Maker-Alpha Readiness.
    """
    print(f"[*] Loading model from {model_path}...")
    if not os.path.exists(model_path):
        # Check if it's in python/runs_train/pilot_real_pilot/pilot_model.zip
        alt_path = os.path.join("python", "runs_train", "pilot_real_pilot", "pilot_model.zip")
        if os.path.exists(alt_path):
            model_path = alt_path
        else:
            print(f"Error: Model not found at {model_path}")
            return

    model = PPO.load(model_path)
    
    # Check for normalization stats
    stats_path = model_path.replace(".zip", "_stats.npz")
    norm_mean, norm_std = None, None
    if os.path.exists(stats_path):
        print(f"[*] Loading normalization stats from {stats_path}...")
        data = np.load(stats_path)
        norm_mean = data["mean"]
        norm_std = data["std"]

    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id=dataset_id,
        symbol="BTCUSDT",
        seed=123, # Use a different seed for validation
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
        fill_model=fill_model_val, 
        reward_maker_fill_bonus=maker_bonus,
        reward_mtm_penalty_window_ms=mtm_penalty_window,
        reward_mtm_penalty_multiplier=mtm_penalty_multiplier,
        reward_reprice_penalty_bps=reprice_penalty,
        post_delta_threshold_bps=post_threshold,
        random_start_offset=False
    )
    
    obs, info = env.reset()
    
    stats = {
        "trades": 0,
        "maker_fills": 0,
        "taker_fills": 0,
        "toxic_fills": 0,
        "total_reward": 0.0,
        "pnl_net": 0.0,
        "equities": [],
        "actions": defaultdict(int),
        "reprice_count": 0,
    }
    
    prev_realized_pnl = 0.0
    initial_equity = info.get("equity", 10000.0)
    
    print(f"[*] Running Maker Scorecard Evaluation on {dataset_id} ({steps} steps) with fill_model={fill_model_val}...")
    for _ in tqdm(range(steps)):
        # Normalize obs if stats loaded
        model_obs = obs
        if norm_mean is not None:
            model_obs = (obs - norm_mean) / norm_std
            
        action, _ = model.predict(model_obs, deterministic=True)
        action_val = int(action)
        stats["actions"][action_val] += 1
        
        obs, reward, terminated, truncated, info = env.step(action_val)
        stats["total_reward"] += reward
        
        if "equity" in info:
            stats["equities"].append(info["equity"])
        
        if "maker_fills" in info: stats["maker_fills"] += info["maker_fills"]
        if "toxic_fills" in info: stats["toxic_fills"] += info["toxic_fills"]
        if "reprice_count" in info: stats["reprice_count"] += info["reprice_count"]
        
        # Track realized pnl change for trade count
        curr_realized = info.get("realized_pnl", 0.0)
        if curr_realized != prev_realized_pnl:
            stats["trades"] += 1
            prev_realized_pnl = curr_realized
            
        if terminated or truncated:
            obs, info = env.reset()
            prev_realized_pnl = 0.0

    # Final tally
    equities = stats["equities"]
    final_equity = equities[-1] if equities else initial_equity
    net_pnl = final_equity - initial_equity
    stats["pnl_net"] = net_pnl
    
    # Calculate Profit Factor and Win Rate
    # Note: We'd need more granular trade-by-trade PnL for perfect Win Rate,
    # but we can estimate or stick to simple aggregates for now.
    # For a stricter audit, let's just use what we have.
    
    total_trades = stats["trades"]
    maker_ratio = stats["maker_fills"] / total_trades if total_trades > 0 else 0
    toxic_rate = stats["toxic_fills"] / stats["maker_fills"] if stats["maker_fills"] > 0 else 0
    
    print("\n" + "="*50)
    print("MAKER ALPHA SCORECARD")
    print("="*50)
    print(f"Dataset:          {dataset_id}")
    print(f"Total Steps:      {steps}")
    print(f"Total Trades:     {total_trades}")
    print(f"Net PnL (USDT):   {net_pnl:.2f}")
    print(f"Return Pct:       {(net_pnl/initial_equity*100):.2f}%")
    print(f"Avg PnL/Trade:    {(net_pnl/total_trades if total_trades > 0 else 0):.4f}")
    print("-" * 50)
    print(f"Maker Fills:      {stats['maker_fills']}")
    print(f"Taker Fills:      {stats['taker_fills']}")
    print(f"Toxic Fills:      {stats['toxic_fills']}")
    print(f"Maker Ratio:      {maker_ratio:.2%}")
    print(f"Toxic Rate:       {toxic_rate:.2%}")
    print(f"Reprice Count:    {stats['reprice_count']}")
    print("-" * 50)
    print("Action Distribution:")
    action_labels = ["HOLD", "POST_BID", "POST_ASK", "REPRICE_BID", "REPRICE_ASK", "CLEAR_QUOTES", "CLOSE_POSITION"]
    for i, label in enumerate(action_labels):
        count = stats["actions"].get(i, 0)
        pct = count / steps * 100
        print(f"  {label:<15}: {count:>5} ({pct:>5.1f}%)")
    print("="*50)
    
    # Save to JSON for report generation
    report = {
        "dataset": dataset_id,
        "steps": steps,
        "total_trades": total_trades,
        "net_pnl": net_pnl,
        "maker_fills": stats["maker_fills"],
        "taker_fills": stats["taker_fills"],
        "toxic_fills": stats["toxic_fills"],
        "maker_ratio": maker_ratio,
        "toxic_rate": toxic_rate,
        "actions": {label: stats["actions"].get(i, 0) for i, label in enumerate(action_labels)}
    }
    with open("python/audits/refined_audit_results.json", "w") as f:
        import json
        json.dump(report, f, indent=4)
    print(f"[*] Report saved to python/audits/refined_audit_results.json")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, default="python/runs_train/pilot_real_pilot/pilot_model.zip")
    parser.add_argument("--dataset", type=str, default="stage2_eval")
    parser.add_argument("--steps", type=int, default=5000)
    parser.add_argument("--fill_model", type=int, default=0)
    parser.add_argument("--maker_bonus", type=float, default=0.0006)
    parser.add_argument("--mtm_window", type=int, default=1000)
    parser.add_argument("--mtm_multiplier", type=float, default=2.0)
    parser.add_argument("--reprice_penalty", type=float, default=0.00005)
    parser.add_argument("--post_threshold", type=float, default=0.05)
    args = parser.parse_args()
    
    run_maker_scorecard(
        args.model, 
        args.dataset, 
        args.steps, 
        args.fill_model,
        args.maker_bonus,
        args.mtm_window,
        args.mtm_multiplier,
        args.reprice_penalty,
        args.post_threshold
    )
