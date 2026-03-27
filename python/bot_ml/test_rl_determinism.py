"""
test_rl_determinism.py — Verify environment determinism.

Runs the same episode twice with identical seed and fixed action sequence.
Hashes the stream of (observations, rewards, done, info) and asserts the hash is identical.
"""
import unittest
import numpy as np
import hashlib
import json
import grpc_env

class TestRLDeterminism(unittest.TestCase):
    def run_episode(self, seed):
        env = grpc_env.GrpcTradingEnv(
            dataset_id="synthetic_test",
            symbol="BTCUSDT",
            seed=seed
        )
        
        obs, info = env.reset()
        hasher = hashlib.sha256()
        
        # Initial state hash
        hasher.update(obs.tobytes())
        
        # Fixed action sequence: HOLD, BUY, HOLD...
        actions = [0, 1, 0, 0, 0, 3, 2, 0, 0, 6] * 10
        
        steps = 0
        total_reward = 0.0
        
        for action in actions:
            obs, reward, done, truncated, info = env.step(action)
            
            # Update hash with transition data
            hasher.update(obs.tobytes())
            hasher.update(np.array([reward], dtype=np.float32).tobytes())
            hasher.update(str(done).encode('utf-8'))
            
            # Hash critical info fields to ensure engine state determinism
            state_str = f"{info.get('equity',0):.2f}_{info.get('position_qty',0):.4f}"
            hasher.update(state_str.encode('utf-8'))
            
            total_reward += reward
            steps += 1
            if done or truncated:
                break
                
        env.close()
        return hasher.hexdigest(), steps, total_reward

    def test_determinism(self):
        print("\nRunning Episode 1...")
        hash1, steps1, rew1 = self.run_episode(seed=12345)
        print(f"Run 1: Hash={hash1[:16]} Steps={steps1} Reward={rew1:.4f}")
        
        print("Running Episode 2 (Same Seed)...")
        hash2, steps2, rew2 = self.run_episode(seed=12345)
        print(f"Run 2: Hash={hash2[:16]} Steps={steps2} Reward={rew2:.4f}")
        
        self.assertEqual(hash1, hash2, "Environment is NOT deterministic! Hashes differ.")
        self.assertEqual(steps1, steps2)
        self.assertEqual(rew1, rew2)
        print("SUCCESS: Environment is perfectly deterministic.")

if __name__ == "__main__":
    unittest.main()
