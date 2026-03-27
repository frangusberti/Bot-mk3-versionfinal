"""
offline_eval.py — Evaluate trained PPO agent on offline historical data windows.

Usage:
    python python/bot_ml/offline_eval.py --model_path python/runs_train/offline_123/final_model --symbol BTCUSDT --index data/index/datasets_index.json
"""
import argparse
import os
import time
import numpy as np
from stable_baselines3 import PPO

import window_env
from episode_builder import EpisodeBuilder

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_path", type=str, required=True, help="Path to trained model zip")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol(s), comma separated")
    parser.add_argument("--index", type=str, default="data/index/datasets_index.json", help="Path to index")
    parser.add_argument("--window", type=int, default=1800, help="Window size in seconds")
    parser.add_argument("--stride", type=int, default=300, help="Window stride in seconds")
    parser.add_argument("--server", type=str, default="localhost:50051", help="gRPC server address")
    parser.add_argument("--max_episodes", type=int, default=100, help="Max episodes to evaluate")
    parser.add_argument("--seed", type=int, default=100, help="Evaluation seed")
    
    args = parser.parse_args()
    
    symbols = args.symbol.split(",")
    
    # 1. Build Episode List
    builder = EpisodeBuilder(args.index)
    episodes = builder.build_windows(symbols, window_len_secs=args.window, stride_secs=args.stride)
    
    if not episodes:
        print(f"No episodes found for {symbols} in {args.index}. Exiting.")
        return

    # Cap episodes
    if args.max_episodes > 0:
        episodes = episodes[:args.max_episodes]

    print(f"Starting evaluation: {len(episodes)} episodes")
    print(f"Model: {args.model_path}")

    # 2. Create Window Environment
    env = window_env.WindowTradingEnv(
        episodes=episodes,
        server_addr=args.server,
        seed=args.seed
    )

    # 3. Load Model
    model = PPO.load(args.model_path)

    # 4. Evaluate
    results = []
    
    for i in range(len(episodes)):
        obs, info = env.reset()
        done = False
        truncated = False
        total_reward = 0.0
        steps = 0
        
        # Episode info
        ep_id = info.get('episode_id', 'unknown')
        start_ts = info.get('window_start', 0)
        
        while not (done or truncated):
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, done, truncated, step_info = env.step(action)
            total_reward += reward
            steps += 1
            
        final_equity = step_info.get('equity', 0.0)
        reason = step_info.get('reason', 'UNKNOWN')
        
        res = {
            "episode_idx": i,
            "episode_id": ep_id,
            "symbol": episodes[i]['symbol'],
            "start_ts": start_ts,
            "steps": steps,
            "reward": total_reward,
            "final_equity": final_equity,
            "reason": reason
        }
        results.append(res)
        
        print(f"Ep {i+1}/{len(episodes)} | Rw: {total_reward:.2f} | Eq: {final_equity:.2f} | {reason}")

    env.close()
    
    # Summary
    if results:
        avg_reward = np.mean([r['reward'] for r in results])
        avg_equity = np.mean([r['final_equity'] for r in results])
        print("--------------------------------------------------")
        print(f"Evaluation Complete.")
        print(f"Avg Reward: {avg_reward:.4f}")
        print(f"Avg Final Equity: {avg_equity:.2f} (Start=10000.0)")
        profit_eps = [r for r in results if r['final_equity'] > 10000.0]
        print(f"Win Rate: {len(profit_eps)}/{len(results)} ({len(profit_eps)/len(results)*100:.1f}%)")

if __name__ == "__main__":
    main()
