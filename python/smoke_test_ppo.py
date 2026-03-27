import sys
import os
import json
import numpy as np
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env

# Add bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

def run_smoke_test():
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="synthetic_test",
        symbol="BTCUSDT",
        seed=1337
    )
    
    print("Initializing PPO Model...")
    model = PPO("MlpPolicy", env, verbose=0, n_steps=1024, batch_size=256, n_epochs=3)
    
    print("Training for 3000 steps...")
    model.learn(total_timesteps=3000)
    
    print("Evaluating trained policy over a rollout of 1000 steps...")
    obs, info = env.reset()
    
    actions_taken = {
        "HOLD": 0,
        "OPEN_LONG": 0,
        "OPEN_SHORT": 0,
        "CLOSE_ALL": 0,
        "REDUCE_50": 0,
        "FLIP_LONG": 0,
        "FLIP_SHORT": 0
    }
    
    mapping = ["HOLD", "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL", "REDUCE_50", "FLIP_LONG", "FLIP_SHORT"]
    
    trades = 0
    ep_rewards = []
    current_ep_reward = 0.0
    equities = [info.get("equity", 10000.0)]
    
    for _ in range(1000):
        action, _states = model.predict(obs, deterministic=False)
        action_val = int(action)
        actions_taken[mapping[action_val]] += 1
        
        obs, reward, terminated, truncated, info = env.step(action_val)
        
        current_ep_reward += reward
        if "equity" in info:
            equities.append(info["equity"])
            
        if "trades_executed" in info and info["trades_executed"] > 0:
            trades += info["trades_executed"]
            
        if terminated or truncated:
            ep_rewards.append(current_ep_reward)
            current_ep_reward = 0.0
            obs, info = env.reset()
            
    if current_ep_reward != 0.0:
        ep_rewards.append(current_ep_reward)
        
    print("\n--- SMOKE TEST RESULTS ---")
    print("Action Distribution (Eval):")
    for act, count in actions_taken.items():
        print(f"  {act}: {count} ({(count/1000)*100:.1f}%)")
        
    print(f"\nTotal Trades Executed: {trades}")
    
    if ep_rewards:
        print(f"Mean Episode Return: {np.mean(ep_rewards):.4f}")
        print(f"Std Episode Return: {np.std(ep_rewards):.4f}")
    else:
        print("Mean Episode Return: N/A (no termination)")
        
    print(f"Initial Equity: {equities[0]:.2f}")
    print(f"Final Equity: {equities[-1]:.2f}")
    print(f"Min Equity: {min(equities):.2f}")
    print(f"Max Equity: {max(equities):.2f}")

if __name__ == "__main__":
    run_smoke_test()
