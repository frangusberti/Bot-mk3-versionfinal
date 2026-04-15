"""
250k PPO training run with Dynamic Floor regime.
Large step count (250k) to ensure trade execution in 6+ hours of market data.
Using pretrained policy with bridging wrappers.
"""
import sys, os, json, time
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'python'))
sys.path.insert(0, os.path.join(os.getcwd(), 'python', 'bot_ml'))

import numpy as np
import gymnasium as gym
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback
from grpc_env import GrpcTradingEnv

TOTAL_STEPS = 250000
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
        mapping = {0: 0, 1: 1, 2: 5, 3: 4, 4: 3, 5: 3, 6: 4}
        return mapping.get(action, 0)

class PerformanceCallback(BaseCallback):
    def __init__(self):
        super().__init__(verbose=0)
        self.trades = 0
        self.best_pnl = -999999.0

    def _on_step(self):
        info = self.locals["infos"][0]
        self.trades += info.get("trades_executed", 0)
        pnl = info.get("realized_pnl", 0.0)
        if pnl > self.best_pnl: self.best_pnl = pnl
        
        if self.num_timesteps % 25000 == 0:
            print(f"  step {self.num_timesteps}/{TOTAL_STEPS} | Total Trades: {self.trades} | Realized PnL: {pnl:.2f}")
        return True

def main():
    print(f"=== 250k Validation Run: Pretrained Model ===")
    
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50,
        use_exit_curriculum_d1=True, # Active D1
        maker_first_exit_timeout_ms=8000,
        exit_maker_pricing_multiplier=0.5,
        profit_floor_bps=0.0, # Floor 0 to allow dynamic floor
        use_selective_entry=True,
        fill_model=1, # Semi-Optimistic (compromise)
        seed=123,
    )
    env = ObservationSlicerWrapper(env, TARGET_OBS_DIM)
    env = ActionMapperWrapper(env)
    env.reset()
    
    model = PPO.load(CHECKPOINT_PATH, env=env)
    cb = PerformanceCallback()
    
    print(f"Starting execution...")
    model.learn(total_timesteps=TOTAL_STEPS, callback=cb)
    print(f"Run complete. Total trades: {cb.trades}")
    env.close()

if __name__ == "__main__":
    main()
