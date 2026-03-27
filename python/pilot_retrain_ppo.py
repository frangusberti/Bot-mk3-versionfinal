"""
pilot_retrain_ppo.py — Pilot PPO retrain on real Binance data.
Trains a medium step budget (50,000 steps) and returns detailed metrics.
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
import gymnasium as gym

from grpc_env import GrpcTradingEnv


class MetricsCallback(BaseCallback):
    """Collect per-step action distribution and episode stats during training."""
    def __init__(self, verbose=0):
        super().__init__(verbose)
        self.action_counts = defaultdict(int)
        
        # Cumulative metrics for logging
        self.total_maker_fills = 0
        self.total_toxic_fills = 0
        self.total_taker_fills = 0
        
        self.episode_returns = []
        self.episode_lengths = []
        self.current_return = 0.0
        self.current_length = 0

    def _on_step(self) -> bool:
        action = self.locals["actions"][0]
        self.action_counts[int(action)] += 1
        reward = self.locals["rewards"][0]
        self.current_return += reward
        self.current_length += 1
        
        # Log granular metrics if present in info
        info = self.locals["infos"][0]
        if "maker_fills" in info:
            m_fills = info["maker_fills"]
            self.total_maker_fills += m_fills
            self.logger.record("env/maker_fills_step", m_fills)
        if "toxic_fills" in info:
            t_fills = info["toxic_fills"]
            self.total_toxic_fills += t_fills
            self.logger.record("env/toxic_fills_step", t_fills)
        if "trades_executed" in info:
            self.logger.record("env/trades_executed_step", info["trades_executed"])

        done = self.locals["dones"][0]
        if done:
            self.episode_returns.append(self.current_return)
            self.episode_lengths.append(self.current_length)
            
            # Record cumulative metrics at end of episode
            self.logger.record("env/ep_maker_fills_total", self.total_maker_fills)
            self.logger.record("env/ep_toxic_fills_total", self.total_toxic_fills)
            
            self.current_return = 0.0
            self.current_length = 0
            self.total_maker_fills = 0
            self.total_toxic_fills = 0
            self.total_taker_fills = 0
            
        return True


ACTION_LABELS = [
    "HOLD", "POST_BID", "POST_ASK", "REPRICE_BID", "REPRICE_ASK", "CLEAR_QUOTES", "CLOSE_POSITION"
]


def run_evaluation(model, env, n_steps=2000):
    """Run a deterministic evaluation rollout and collect metrics."""
    obs, info = env.reset()
    
    actions = defaultdict(int)
    trades = 0
    maker_fills = 0
    toxic_fills = 0
    ep_rewards = []
    current_ep = 0.0
    equities = [info.get("equity", 10000.0)]
    ep_trade_counts = []
    ep_trades = 0
    regime_counts = defaultdict(int)

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
        if "maker_fills" in info:
            maker_fills += info["maker_fills"]
        if "toxic_fills" in info:
            toxic_fills += info["toxic_fills"]
            
        if terminated or truncated:
            ep_rewards.append(current_ep)
            ep_trade_counts.append(ep_trades)
            current_ep = 0.0
            ep_trades = 0
            obs, info = env.reset()

    if current_ep != 0.0:
        ep_rewards.append(current_ep)
        ep_trade_counts.append(ep_trades)

    return {
        "action_distribution": dict(actions),
        "total_trades": trades,
        "maker_fills": maker_fills,
        "toxic_fills": toxic_fills,
        "episode_returns": ep_rewards,
        "episode_trade_counts": ep_trade_counts,
        "equities": equities,
    }


class ObsNormWrapper(gym.ObservationWrapper):
    def __init__(self, env, stats_path):
        super().__init__(env)
        data = np.load(stats_path)
        self.mean = data["mean"]
        self.std = data["std"]
        print(f"[WRAPPER] Observation normalization loaded from {stats_path}")

    def observation(self, obs):
        return (obs - self.mean) / self.std


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_id", type=str, default="stage2_train")
    parser.add_argument("--symbol", type=str, default="BTCUSDT")
    parser.add_argument("--train_steps", type=int, default=100000)
    parser.add_argument("--eval_steps", type=int, default=2000)
    parser.add_argument("--server", type=str, default="localhost:50051")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--pretrained_model", type=str, default=None)
    parser.add_argument("--save_freq", type=int, default=50000, help="Steps between checkpoints")
    args = parser.parse_args()

    runs_dir = os.path.join("python", "runs_train", "pilot_stage2_train")
    os.makedirs(runs_dir, exist_ok=True)

    print(f"=== PILOT RETRAIN: {args.dataset_id} ===")
    print(f"Training steps: {args.train_steps}")
    print(f"Eval steps:     {args.eval_steps}")

    # Create environment
    env = GrpcTradingEnv(
        server_addr=args.server,
        dataset_id=args.dataset_id,
        symbol=args.symbol,
        seed=args.seed,
        fill_model=1, # SemiOptimistic
        reward_maker_fill_bonus=0.0006,     # 6 bps per fill
        reward_taker_fill_penalty=0.0005,   # 5 bps penalty
        reward_toxic_fill_penalty=0.0010,   # 10 bps penalty
        reward_idle_posting_penalty=0.00001, # 0.1 bps per step
        reward_mtm_penalty_window_ms=1000,
        reward_mtm_penalty_multiplier=2.0,
        reward_reprice_penalty_bps=0.00005,  # 0.5 bps per reprice
        post_delta_threshold_bps=0.05,       # 0.05 bps movement threshold (~3.5 ticks)
    )
    
    # Wrap with normalization if stats exist for the pretrained model
    stats_path = args.pretrained_model.replace(".zip", "_stats.npz")
    if os.path.exists(stats_path):
        env = ObsNormWrapper(env, stats_path)

    v_env = DummyVecEnv([lambda: env])
    v_env = VecMonitor(v_env)

    # Policy architecture
    policy_kwargs = dict(net_arch=dict(pi=[128, 128], vf=[128, 128]))

    # Create or Load PPO model
    if args.pretrained_model and os.path.exists(args.pretrained_model):
        print(f"Loading pre-trained weights from {args.pretrained_model}...")
        model = PPO.load(
            args.pretrained_model,
            env=v_env,
            verbose=0,
            ent_coef=0.01,
            learning_rate=5e-5,
            seed=args.seed,
            batch_size=256,
            n_steps=2048,
            n_epochs=10,
            clip_range=0.2,
            target_kl=0.02,
        )
    else:
        model = PPO(
            "MlpPolicy",
            v_env,
            verbose=0,
            ent_coef=0.01,
            learning_rate=1e-4,
            seed=args.seed,
            batch_size=256,
            n_steps=2048,
            n_epochs=10,
            clip_range=0.2,
            target_kl=0.02,
            policy_kwargs=policy_kwargs,
        )

    # Implementation of Checkpointed Training
    print("\n--- TRAINING ---")
    callback = MetricsCallback()
    current_steps = 0
    while current_steps < args.train_steps:
        steps_to_run = min(args.save_freq, args.train_steps - current_steps)
        model.learn(total_timesteps=steps_to_run, callback=callback, reset_num_timesteps=False)
        current_steps += steps_to_run
        
        # Save checkpoint
        checkpoint_name = f"pilot_model_{current_steps // 1000}k" if current_steps < args.train_steps else "pilot_model"
        save_path = os.path.join(runs_dir, f"{checkpoint_name}.zip")
        model.save(save_path)
        print(f"[*] Checkpoint saved: {save_path}")

    print("Training complete.")

    # --- TRAINING METRICS ---
    print("\n=== TRAINING METRICS ===")
    total_train_actions = sum(callback.action_counts.values())
    print("Training Action Distribution:")
    for i, label in enumerate(ACTION_LABELS):
        count = callback.action_counts.get(i, 0)
        pct = (count / total_train_actions * 100) if total_train_actions > 0 else 0
        print(f"  {label}: {count} ({pct:.1f}%)")

    hold_rate_train = (callback.action_counts.get(0, 0) / total_train_actions * 100) if total_train_actions > 0 else 0
    print(f"\nHOLD Rate (Training): {hold_rate_train:.1f}%")

    if callback.episode_returns:
        returns = np.array(callback.episode_returns)
        print(f"Episodes completed: {len(returns)}")
        print(f"Mean Episode Return: {returns.mean():.6f}")
        print(f"Std Episode Return:  {returns.std():.6f}")
        print(f"p5 Return:  {np.percentile(returns, 5):.6f}")
        print(f"p50 Return: {np.percentile(returns, 50):.6f}")
        print(f"p95 Return: {np.percentile(returns, 95):.6f}")

    # --- EVALUATION METRICS ---
    print("\n--- EVALUATION (Deterministic) ---")
    # Create a fresh env for evaluation
    eval_env = GrpcTradingEnv(
        server_addr=args.server,
        dataset_id=args.dataset_id,
        symbol=args.symbol,
        seed=args.seed + 1000,
        fill_model=1, # SemiOptimistic
        reward_maker_fill_bonus=0.0006,
        reward_reprice_penalty_bps=0.00005,
        post_delta_threshold_bps=0.05,
    )

    eval_results = run_evaluation(model, eval_env, n_steps=args.eval_steps)

    total_eval_actions = sum(eval_results["action_distribution"].values())
    print("Eval Action Distribution:")
    for i, label in enumerate(ACTION_LABELS):
        count = eval_results["action_distribution"].get(i, 0)
        pct = (count / total_eval_actions * 100) if total_eval_actions > 0 else 0
        print(f"  {label}: {count} ({pct:.1f}%)")

    hold_rate_eval = (eval_results["action_distribution"].get(0, 0) / total_eval_actions * 100) if total_eval_actions > 0 else 0
    print(f"\nHOLD Rate (Eval): {hold_rate_eval:.1f}%")
    print(f"Total Trades (Eval): {eval_results['total_trades']}")
    print(f"Maker Fills (Eval):  {eval_results['maker_fills']}")
    print(f"Toxic Fills (Eval):  {eval_results['toxic_fills']}")
    
    maker_ratio = eval_results["maker_fills"] / eval_results["total_trades"] if eval_results["total_trades"] > 0 else 0
    print(f"Maker Ratio:         {maker_ratio:.2%}")

    if eval_results["episode_returns"]:
        returns = np.array(eval_results["episode_returns"])
        print(f"Mean Episode Return: {returns.mean():.6f}")
        print(f"Std Episode Return:  {returns.std():.6f}")

    if eval_results["episode_trade_counts"]:
        tc = np.array(eval_results["episode_trade_counts"])
        print(f"Mean Trades/Episode: {tc.mean():.1f}")

    equities = eval_results["equities"]
    print(f"\nEquity Curve:")
    print(f"  Initial: {equities[0]:.2f}")
    print(f"  Final:   {equities[-1]:.2f}")
    print(f"  Min:     {min(equities):.2f}")
    print(f"  Max:     {max(equities):.2f}")
    net_pnl = (equities[-1] - equities[0]) / equities[0] * 100
    print(f"  Net PnL: {net_pnl:.2f}%")

    # --- SELECTIVITY ASSESSMENT ---
    print("\n=== SELECTIVITY ASSESSMENT ===")
    if hold_rate_eval > 95.0:
        print("VERDICT: HIGH HOLD --> Likely healthy selectivity (policy is being very choosy)")
        if hold_rate_eval > 99.5:
            print("WARNING: Extreme HOLD rate may indicate policy collapse")
    elif hold_rate_eval > 50.0:
        print("VERDICT: MODERATE HOLD --> Policy is learning to be selective")
    else:
        print("VERDICT: LOW HOLD --> Policy is actively trading (may still be exploring)")

    if eval_results["total_trades"] == 0:
        print("ZERO TRADES in evaluation --> possible policy collapse")
    elif eval_results["total_trades"] < 5:
        print("WARNING: Very few trades -> check if cost penalties are too aggressive")

    # Save summary
    summary = {
        "dataset_id": args.dataset_id,
        "train_steps": args.train_steps,
        "eval_steps": args.eval_steps,
        "hold_rate_train": hold_rate_train,
        "hold_rate_eval": hold_rate_eval,
        "total_trades_eval": eval_results["total_trades"],
        "mean_episode_return_train": float(np.mean(callback.episode_returns)) if callback.episode_returns else None,
        "mean_episode_return_eval": float(np.mean(eval_results["episode_returns"])) if eval_results["episode_returns"] else None,
        "equity_initial": equities[0],
        "equity_final": equities[-1],
        "net_pnl_pct": net_pnl,
    }
    with open(f"{runs_dir}/pilot_summary.json", "w") as f:
        summary["maker_fills_eval"] = eval_results["maker_fills"]
        summary["toxic_fills_eval"] = eval_results["toxic_fills"]
        summary["maker_ratio_eval"] = maker_ratio
        json.dump(summary, f, indent=2)
    print(f"\nSummary saved to {runs_dir}/pilot_summary.json")


if __name__ == "__main__":
    main()
