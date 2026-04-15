"""Minimal bridge verification: 500 real PPO steps to confirm non-Hold actions reach the server."""
import os, sys
sys.path.append(os.path.join(os.getcwd(), 'python'))
from bot_ml.grpc_env import GrpcTradingEnv

env = GrpcTradingEnv(
    server_addr="localhost:50051",
    dataset_id="stage2_train",
    symbol="BTCUSDT",
    initial_equity=50000.0,
    max_pos_frac=0.50,
)

obs, info = env.reset()
actual_obs_dim = len(obs)
print(f"Obs dim from env: {env.observation_space.shape}, actual obs: {actual_obs_dim}")

# Override obs space to match actual
import gymnasium as gym
import numpy as np
env.observation_space = gym.spaces.Box(low=-np.inf, high=np.inf, shape=(actual_obs_dim,), dtype=np.float32)

from stable_baselines3 import PPO
model = PPO("MlpPolicy", env, verbose=0, seed=42)

action_counts = {}
trades_seen = 0
for step in range(500):
    action, _ = model.predict(obs, deterministic=False)
    action_val = int(action)
    action_counts[action_val] = action_counts.get(action_val, 0) + 1
    obs, reward, done, truncated, info = env.step(action_val)
    trades_seen += info.get("trades_executed", 0)
    if done or truncated:
        obs, info = env.reset()

env.close()

print(f"\nTotal steps: 500")
print(f"Trades seen: {trades_seen}")
labels = ["HOLD","OPEN_LONG","ADD_LONG","REDUCE_LONG","CLOSE_LONG",
          "OPEN_SHORT","ADD_SHORT","REDUCE_SHORT","CLOSE_SHORT","REPRICE"]
non_hold = 0
for i in range(10):
    c = action_counts.get(i, 0)
    if i > 0: non_hold += c
    print(f"  {i} ({labels[i]}): {c}  ({c/500*100:.1f}%)")
print(f"\nNon-Hold actions: {non_hold} ({non_hold/500*100:.1f}%)")
