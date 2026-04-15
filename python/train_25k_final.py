"""
25k PPO training run with Dynamic Floor regime - FINAL VALIDATION.
Using Optimistic Fill (2) to ensure trade execution.
"""
import sys, os
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'python'))
sys.path.insert(0, os.path.join(os.getcwd(), 'python', 'bot_ml'))

import numpy as np
import gymnasium as gym
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
        self.observation_space = gym.spaces.Box(low=-np.inf, high=np.inf, shape=(target_dim,), dtype=np.float32)
    def observation(self, observation):
        return observation[:self.target_dim]

class ActionMapperWrapper(gym.ActionWrapper):
    def __init__(self, env):
        super().__init__(env)
        self.action_space = gym.spaces.Discrete(7)
    def action(self, action):
        mapping = {0: 0, 1: 1, 2: 5, 3: 4, 4: 3, 5: 3, 6: 4}
        return mapping.get(action, 0)

def main():
    print("=== 25k Final Validation Run ===")
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50, # Now correctly honored
        use_exit_curriculum_d1=True,
        maker_first_exit_timeout_ms=8000,
        exit_maker_pricing_multiplier=0.5,
        profit_floor_bps=0.0,
        use_selective_entry=True,
        fill_model=2, # Optimistic
        seed=555,
    )
    env = ObservationSlicerWrapper(env, TARGET_OBS_DIM)
    env = ActionMapperWrapper(env)
    env.reset()
    
    model = PPO.load(CHECKPOINT_PATH, env=env)
    print("Executing steps...")
    model.learn(total_timesteps=TOTAL_STEPS)
    print("Run complete.")
    env.close()

if __name__ == "__main__":
    main()
