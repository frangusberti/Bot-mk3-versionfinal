"""
rl_eval.py — Evaluate trained PPO agent.

Usage:
    python python/bot_ml/rl_eval.py --model_path python/runs_train/run_123/final_model --episodes 5
"""
import argparse
import numpy as np
import time
from stable_baselines3 import PPO
from grpc_env import GrpcTradingEnv

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model_path", type=str, required=True, help="Path to trained model zip")
    parser.add_argument("--episodes", type=int, default=5, help="Number of episodes to evaluate")
    parser.add_argument("--dataset", type=str, default="synthetic_test", help="Dataset ID")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--server", type=str, default="localhost:50051", help="gRPC server address")
    args = parser.parse_args()

    # Create environment
    env = GrpcTradingEnv(
        server_addr=args.server,
        dataset_id=args.dataset,
        symbol=args.symbol,
        seed=100  # Evaluation seed
    )

    # Load model
    print(f"Loading model from {args.model_path}")
    model = PPO.load(args.model_path)

    print(f"Starting evaluation: {args.episodes} episodes on {args.dataset}")

    for i in range(args.episodes):
        obs, info = env.reset(seed=100+i)
        done = False
        truncated = False
        total_reward = 0.0
        steps = 0
        
        while not (done or truncated):
            action, _ = model.predict(obs, deterministic=True)
            obs, reward, done, truncated, step_info = env.step(action)
            total_reward += reward
            steps += 1
            
            # Print periodic status
            if steps % 1000 == 0:
                print(f"Ep {i+1} Step {steps}: Reward={total_reward:.4f} Eq={step_info.get('equity', 0):.2f}")

        final_equity = step_info.get('equity', 0.0)
        reason = step_info.get('reason', 'UNKNOWN')
        print(f"Episode {i+1} Finished: Steps={steps} TotalReward={total_reward:.4f} FinalEquity={final_equity:.2f} Reason={reason}")

    env.close()

if __name__ == "__main__":
    main()
