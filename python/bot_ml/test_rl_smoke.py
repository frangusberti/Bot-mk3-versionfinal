"""
test_rl_smoke.py — Basic sanity check for RL training pipeline.

Trains for a small number of steps to ensure no crashes and observation shape is correct.
"""
import unittest
import numpy as np
import os
import shutil
from stable_baselines3 import PPO
from stable_baselines3.common.env_checker import check_env
import grpc_env

class TestRLPipeline(unittest.TestCase):
    def setUp(self):
        self.env = grpc_env.GrpcTradingEnv(
            dataset_id="synthetic_test",
            symbol="BTCUSDT",
            seed=42
        )
        self.log_dir = "python/runs_train/smoke_test"
        if os.path.exists(self.log_dir):
            shutil.rmtree(self.log_dir)
        os.makedirs(self.log_dir, exist_ok=True)

    def test_env_conforms_to_gym(self):
        """Test that environment follows Gym API."""
        print("\nChecking Gym environment conformance...")
        check_env(self.env)
        print("Protocol check passed.")

    def test_training_loop(self):
        """Test that we can train for 200 steps without crashing."""
        print("\nTesting PPO training loop (200 steps)...")
        model = PPO("MlpPolicy", self.env, verbose=1)
        model.learn(total_timesteps=200)
        model.save(f"{self.log_dir}/smoke_model")
        print("Training loop passed.")
        
        # Test loading
        loaded = PPO.load(f"{self.log_dir}/smoke_model")
        self.assertIsNotNone(loaded)

    def tearDown(self):
        self.env.close()
        # cleanup
        if os.path.exists(self.log_dir):
            shutil.rmtree(self.log_dir)

if __name__ == "__main__":
    unittest.main()
