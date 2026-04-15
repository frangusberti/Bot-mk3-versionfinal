"""
25k PPO training run with Dynamic Floor regime.
D1 + P2 + Suppress V2.1 + Dynamic Trade Floor.
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

import numpy as np
import gymnasium as gym
from collections import defaultdict
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from grpc_env import GrpcTradingEnv

TOTAL_STEPS = 25000

class MetricsCallback(BaseCallback):
    def __init__(self):
        super().__init__(verbose=0)
        self.action_counts = defaultdict(int)
        self.ep_returns = []
        self.ep_lengths = []
        self.ep_trades = []
        self._cur_ret = 0.0
        self._cur_len = 0
        self._cur_trades = 0

    def _on_step(self):
        action = int(self.locals["actions"][0])
        self.action_counts[action] += 1
        self._cur_ret += self.locals["rewards"][0]
        self._cur_len += 1
        info = self.locals["infos"][0]
        self._cur_trades += info.get("trades_executed", 0)
        if self.locals["dones"][0]:
            self.ep_returns.append(self._cur_ret)
            self.ep_lengths.append(self._cur_len)
            self.ep_trades.append(self._cur_trades)
            self._cur_ret = 0.0
            self._cur_len = 0
            self._cur_trades = 0
        if self.num_timesteps % 5000 == 0:
            print(f"  step {self.num_timesteps}/{TOTAL_STEPS}")
        return True

def main():
    print(f"=== 25k Training Run: D1+P2+Suppress+DynFloor ===")

    rl_config_opts = {
        "rl_config": {
            "use_exit_curriculum_d1": True,
            "maker_first_exit_timeout_ms": 8000,
            "exit_maker_pricing_multiplier": 0.5,
            "profit_floor_bps": 0.0,
            "use_selective_entry": True,
        }
    }

    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50,
        seed=42,
    )

    # Auto-detect actual obs dim from first reset
    obs, info = env.reset(options=rl_config_opts)
    actual_dim = len(obs)
    print(f"Actual obs dim: {actual_dim}")
    env.observation_space = gym.spaces.Box(
        low=-np.inf, high=np.inf, shape=(actual_dim,), dtype=np.float32
    )

    model = PPO(
        "MlpPolicy", env, verbose=0, seed=42,
        ent_coef=0.01, learning_rate=1e-4,
        batch_size=256, n_steps=2048, n_epochs=10,
        clip_range=0.2, target_kl=0.02,
    )

    cb = MetricsCallback()
    print(f"Starting training: {TOTAL_STEPS} steps")
    model.learn(total_timesteps=TOTAL_STEPS, callback=cb)
    print("Training complete.")

    # Summary
    total = sum(cb.action_counts.values())
    labels = ["HOLD","OPEN_LONG","ADD_LONG","REDUCE_LONG","CLOSE_LONG",
              "OPEN_SHORT","ADD_SHORT","REDUCE_SHORT","CLOSE_SHORT","REPRICE"]
    print(f"\nAction Distribution ({total} total):")
    for i in range(10):
        c = cb.action_counts.get(i, 0)
        print(f"  {labels[i]}: {c} ({c/total*100:.1f}%)")

    if cb.ep_returns:
        r = np.array(cb.ep_returns)
        print(f"\nEpisodes: {len(r)}")
        print(f"Mean Return: {r.mean():.6f}")
        print(f"Trades/Ep: {np.mean(cb.ep_trades):.1f}")

    env.close()

if __name__ == "__main__":
    main()
