"""
stage2_pilot.py -- Stage 2 Pilot Retrain on ~7 day dataset with temporal split.

Trains PPO on stage2_train (5d), evaluates on stage2_eval (1.5d).
Returns full metrics: action distribution, HOLD rate, episode returns, trades, etc.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

import argparse
import json
import numpy as np
from collections import defaultdict
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecMonitor
from stable_baselines3.common.callbacks import BaseCallback

from grpc_env import GrpcTradingEnv

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL",
    "REDUCE_25", "REDUCE_50", "REDUCE_100"
]


class DetailedMetricsCallback(BaseCallback):
    def __init__(self, verbose=0):
        super().__init__(verbose)
        self.action_counts = defaultdict(int)
        self.episode_returns = []
        self.episode_lengths = []
        self.episode_trade_counts = []
        self.current_return = 0.0
        self.current_length = 0
        self.current_trades = 0
        self.equities = []

    def _on_step(self):
        action = self.locals["actions"][0]
        self.action_counts[int(action)] += 1
        reward = self.locals["rewards"][0]
        self.current_return += reward
        self.current_length += 1

        info = self.locals["infos"][0]
        if "trades_executed" in info:
            self.current_trades += info["trades_executed"]
        if "equity" in info:
            self.equities.append(info["equity"])

        done = self.locals["dones"][0]
        if done:
            self.episode_returns.append(self.current_return)
            self.episode_lengths.append(self.current_length)
            self.episode_trade_counts.append(self.current_trades)
            self.current_return = 0.0
            self.current_length = 0
            self.current_trades = 0
        return True


def run_eval(model, env, n_steps=3000):
    obs, info = env.reset()
    actions = defaultdict(int)
    trades = 0
    ep_rewards = []
    ep_trade_counts = []
    equities = [info.get("equity", 10000.0)]
    current_ep = 0.0
    ep_trades = 0

    for _ in range(n_steps):
        action, _ = model.predict(obs, deterministic=True)
        action_val = int(action)
        actions[action_val] += 1
        obs, reward, terminated, truncated, info = env.step(action_val)
        current_ep += reward
        if "equity" in info:
            equities.append(info["equity"])
        if "trades_executed" in info:
            t = info["trades_executed"]
            trades += t
            ep_trades += t
        if terminated or truncated:
            ep_rewards.append(current_ep)
            ep_trade_counts.append(ep_trades)
            current_ep = 0.0
            ep_trades = 0
            obs, info = env.reset()
            # Throttling to prevent system collapse
            import time
            time.sleep(1.0) 
            print(f"  [Throttling] Episode Reset Stability Pause...", end='\r')

    if current_ep != 0.0:
        ep_rewards.append(current_ep)
        ep_trade_counts.append(ep_trades)

    return {
        "action_distribution": dict(actions),
        "total_trades": trades,
        "episode_returns": ep_rewards,
        "episode_trade_counts": ep_trade_counts,
        "equities": equities,
    }


def print_dist(dist, label, total):
    print(f"\n{label} Action Distribution:")
    for i, name in enumerate(ACTION_LABELS):
        c = dist.get(i, 0)
        pct = c / total * 100 if total > 0 else 0
        print(f"  {name}: {c:,} ({pct:.1f}%)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train_dataset", default="stage2_train")
    parser.add_argument("--eval_dataset", default="stage2_eval")
    parser.add_argument("--train_steps", type=int, default=100000)
    parser.add_argument("--eval_steps", type=int, default=3000)
    parser.add_argument("--server", default="localhost:50051")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    log_dir = f"python/runs_train/stage2_pilot"
    os.makedirs(log_dir, exist_ok=True)

    print("=" * 60)
    print("STAGE 2 PILOT RETRAIN")
    print("=" * 60)
    print(f"Train dataset: {args.train_dataset}")
    print(f"Eval dataset:  {args.eval_dataset}")
    print(f"Train steps:   {args.train_steps:,}")
    print(f"Eval steps:    {args.eval_steps:,}")

    # --- TRAINING ---
    print("\n--- PHASE 1: TRAINING ---")
    train_env = GrpcTradingEnv(
        server_addr=args.server,
        dataset_id=args.train_dataset,
        symbol="BTCUSDT",
        seed=args.seed,
    )
    vec_env = DummyVecEnv([lambda: train_env])
    vec_env = VecMonitor(vec_env, log_dir)

    model = PPO(
        "MlpPolicy",
        vec_env,
        verbose=0,
        ent_coef=0.01,
        learning_rate=1e-4,
        seed=args.seed,
        batch_size=256,
        n_steps=2048,
        n_epochs=10,
        clip_range=0.2,
        target_kl=0.02,
    )

    cb = DetailedMetricsCallback()
    model.learn(total_timesteps=args.train_steps, callback=cb)
    model.save(f"{log_dir}/stage2_model")
    print("Training complete.")

    # Training metrics
    total_train = sum(cb.action_counts.values())
    hold_train = cb.action_counts.get(0, 0) / total_train * 100 if total_train > 0 else 0
    print_dist(cb.action_counts, "Training", total_train)
    print(f"\nHOLD Rate (Train): {hold_train:.1f}%")
    if cb.episode_returns:
        r = np.array(cb.episode_returns)
        print(f"Episodes: {len(r)}")
        print(f"Mean Return: {r.mean():.6f}  Std: {r.std():.6f}")
        print(f"p5={np.percentile(r,5):.6f}  p50={np.percentile(r,50):.6f}  p95={np.percentile(r,95):.6f}")
    if cb.episode_trade_counts:
        tc = np.array(cb.episode_trade_counts)
        print(f"Mean Trades/Episode: {tc.mean():.1f}  Std: {tc.std():.1f}")

    # --- EVALUATION (on holdout temporal split) ---
    print("\n--- PHASE 2: EVALUATION (temporal holdout) ---")
    vec_env.close()

    eval_env = GrpcTradingEnv(
        server_addr=args.server,
        dataset_id=args.eval_dataset,
        symbol="BTCUSDT",
        seed=args.seed + 1000,
    )

    eval_results = run_eval(model, eval_env, n_steps=args.eval_steps)

    total_eval = sum(eval_results["action_distribution"].values())
    hold_eval = eval_results["action_distribution"].get(0, 0) / total_eval * 100 if total_eval > 0 else 0
    print_dist(eval_results["action_distribution"], "Eval (holdout)", total_eval)
    print(f"\nHOLD Rate (Eval): {hold_eval:.1f}%")
    print(f"Total Trades (Eval): {eval_results['total_trades']}")

    if eval_results["episode_returns"]:
        r = np.array(eval_results["episode_returns"])
        print(f"Eval Episodes: {len(r)}")
        print(f"Mean Return: {r.mean():.6f}  Std: {r.std():.6f}")
    if eval_results["episode_trade_counts"]:
        tc = np.array(eval_results["episode_trade_counts"])
        print(f"Mean Trades/Episode: {tc.mean():.1f}")

    eq = eval_results["equities"]
    print(f"\nEquity Curve (Eval):")
    print(f"  Initial: {eq[0]:.2f}")
    print(f"  Final:   {eq[-1]:.2f}")
    print(f"  Min:     {min(eq):.2f}")
    print(f"  Max:     {max(eq):.2f}")
    net_pnl = (eq[-1] - eq[0]) / eq[0] * 100
    print(f"  Net PnL: {net_pnl:.2f}%")

    # --- SELECTIVITY ASSESSMENT ---
    print("\n" + "=" * 60)
    print("SELECTIVITY ASSESSMENT")
    print("=" * 60)
    if hold_eval > 99.5:
        print("HOLD >99.5%: Very high selectivity or potential collapse")
        if eval_results["total_trades"] == 0:
            print("  -> ZERO trades: likely cost-aversion dominance at this step budget")
        else:
            print(f"  -> {eval_results['total_trades']} trades: ultra-selective sniper mode")
    elif hold_eval > 95:
        print(f"HOLD = {hold_eval:.1f}%: High selectivity, healthy for cost-paying model")
    elif hold_eval > 50:
        print(f"HOLD = {hold_eval:.1f}%: Moderate selectivity, actively learning")
    else:
        print(f"HOLD = {hold_eval:.1f}%: Low selectivity, may still be exploring/churning")

    # Long/Short bias
    long_count = eval_results["action_distribution"].get(1, 0)
    short_count = eval_results["action_distribution"].get(2, 0)
    if long_count + short_count > 0:
        long_pct = long_count / (long_count + short_count) * 100
        print(f"\nDirectional Bias: LONG={long_pct:.0f}% / SHORT={100-long_pct:.0f}%")
    else:
        print("\nNo directional trades executed in eval.")

    # Save summary
    summary = {
        "train_dataset": args.train_dataset,
        "eval_dataset": args.eval_dataset,
        "train_steps": args.train_steps,
        "eval_steps": args.eval_steps,
        "hold_rate_train": hold_train,
        "hold_rate_eval": hold_eval,
        "total_trades_eval": eval_results["total_trades"],
        "mean_return_train": float(np.mean(cb.episode_returns)) if cb.episode_returns else None,
        "mean_return_eval": float(np.mean(eval_results["episode_returns"])) if eval_results["episode_returns"] else None,
        "episodes_train": len(cb.episode_returns),
        "episodes_eval": len(eval_results["episode_returns"]),
        "equity_initial": eq[0],
        "equity_final": eq[-1],
        "net_pnl_pct": net_pnl,
    }
    with open(f"{log_dir}/stage2_summary.json", "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to {log_dir}/stage2_summary.json")


if __name__ == "__main__":
    main()
