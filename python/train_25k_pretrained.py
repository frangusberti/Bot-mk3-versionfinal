"""
25k PPO training run with Dynamic Floor regime using a PRETRAINED base model.
Handles 148-dim observation and 7-dim action space of the old model.
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.getcwd(), 'python'))
sys.path.insert(0, os.path.join(os.getcwd(), 'python', 'bot_ml'))

import numpy as np
import gymnasium as gym
from collections import defaultdict
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from grpc_env import GrpcTradingEnv

TOTAL_STEPS = 25000
CHECKPOINT_PATH = r"C:\Bot mk3\python\runs_train\pilot_stage2_train\pilot_model.zip"
TARGET_OBS_DIM = 148

class ObservationSlicerWrapper(gym.ObservationWrapper):
    def __init__(self, env, target_dim):
        super().__init__(env)
        self.target_dim = target_dim
        self.observation_space = gym.spaces.Box(
            low=-np.inf, high=np.inf, shape=(target_dim,), dtype=np.float32
        )
    def observation(self, observation):
        return observation[:self.target_dim]

class ActionMapperWrapper(gym.ActionWrapper):
    def __init__(self, env):
        super().__init__(env)
        self.action_space = gym.spaces.Discrete(7)
    
    def action(self, action):
        # 0: HOLD, 1: OPEN_LONG, 2: OPEN_SHORT, 3: CLOSE_ALL, 4: REDUCE_25, 5: REDUCE_50, 6: REDUCE_100
        # Map to: 0: HOLD, 1: OPEN_LONG, 2: ADD_LONG, 3: REDUCE_LONG, 4: CLOSE_LONG, 
        #         5: OPEN_SHORT, 6: ADD_SHORT, 7: REDUCE_SHORT, 8: CLOSE_SHORT, 9: REPRICE
        mapping = {
            0: 0, # HOLD
            1: 1, # OPEN_LONG
            2: 5, # OPEN_SHORT
            3: 4, # CLOSE_LONG (approximates CLOSE_ALL if Long, server handles invalid if wrong)
            4: 3, # REDUCE_25 -> REDUCE_LONG
            5: 3, # REDUCE_50 -> REDUCE_LONG
            6: 4, # REDUCE_100 -> CLOSE_LONG
        }
        return mapping.get(action, 0)

class MetricsCallback(BaseCallback):
    def __init__(self):
        super().__init__(verbose=0)
        self.action_counts = defaultdict(int)
        self.ep_returns = []
        self.ep_lengths = []
        self._cur_ret = 0.0
        self._cur_len = 0

    def _on_step(self):
        action = int(self.locals["actions"][0])
        self.action_counts[action] += 1
        self._cur_ret += self.locals["rewards"][0]
        self._cur_len += 1
        
        if self.locals["dones"][0]:
            self.ep_returns.append(self._cur_ret)
            self.ep_lengths.append(self._cur_len)
            self._cur_ret = 0.0
            self._cur_len = 0
        
        if self.num_timesteps % 5000 == 0:
            print(f"  step {self.num_timesteps}/{TOTAL_STEPS}")
        return True

def main():
    print(f"=== 25k Training Run: Pretrained Baseline + Dynamic Floor ===")
    print(f"Loading checkpoint: {CHECKPOINT_PATH}")

    rl_config_opts = {
        "rl_config": {
            "use_exit_curriculum_d1": True,
            "maker_first_exit_timeout_ms": 8000,
            "exit_maker_pricing_multiplier": 0.5,
            "profit_floor_bps": 0.0,
            "use_selective_entry": True,
        }
    }

    raw_env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50,
        seed=42,
    )

    # Wrap to match pretrained model (Obs: 148, Act: 7)
    env = ObservationSlicerWrapper(raw_env, TARGET_OBS_DIM)
    env = ActionMapperWrapper(env)
    
    # Initialize env before loading to ensure observation/action spaces are correct
    env.reset(options=rl_config_opts)

    # Load model
    model = PPO.load(CHECKPOINT_PATH, env=env)
    print("Model loaded successfully.")

    cb = MetricsCallback()
    print(f"Starting training: {TOTAL_STEPS} steps")
    model.learn(total_timesteps=TOTAL_STEPS, callback=cb)
    print("Training complete.")

    # Action counts
    total = sum(cb.action_counts.values())
    labels = ["HOLD", "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL", "REDUCE_25", "REDUCE_50", "REDUCE_100"]
    print(f"\nPolicy Action Distribution ({total} steps):")
    for i in range(7):
        c = cb.action_counts.get(i, 0)
        print(f"  {labels[i]:12}: {c:5} ({c/total*100:4.1f}%)")

    env.close()

if __name__ == "__main__":
    main()
