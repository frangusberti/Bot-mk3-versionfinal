"""
rl_train.py — Train PPO agent using Stable-Baselines3 against GrpcTradingEnv.

Usage:
    python python/bot_ml/rl_train.py --steps 100000 --symbol BTCUSDT --dataset synthetic_test
"""
import argparse
import os
import time
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecMonitor
from stable_baselines3.common.callbacks import CheckpointCallback
import json
import json
import datetime

import grpc_env

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--steps", type=int, default=100_000, help="Total timesteps to train")
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Trading symbol")
    parser.add_argument("--dataset", type=str, default="synthetic_test", help="Dataset ID")
    
    default_run_name = datetime.datetime.now().strftime("%Y%m%d_%H%M") + "_RL_TRAIN"
    parser.add_argument("--run_name", type=str, default=default_run_name, help="Name for this training run")
    parser.add_argument("--server", type=str, default="localhost:50051", help="gRPC server address")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--ent_coef", type=float, default=0.01, help="Entropy coefficient")
    parser.add_argument("--learning_rate", type=float, default=3e-4, help="Learning rate")
    
    args = parser.parse_args()

    # Create logs directory
    log_dir = f"python/runs_train/{args.run_name}"
    os.makedirs(log_dir, exist_ok=True)
    
    print(f"Starting training run: {args.run_name}")
    print(f"Env: {args.symbol} on {args.dataset}")
    print(f"Logs: {log_dir}")

    # Create environment
    def make_env():
        return grpc_env.GrpcTradingEnv(
            server_addr=args.server,
            dataset_id=args.dataset,
            symbol=args.symbol,
            seed=args.seed
        )

    # Vectorized environment (wrapper for SB3)
    env = DummyVecEnv([make_env])
    env = VecMonitor(env, log_dir)

    # Define model
    model = PPO(
        "MlpPolicy",
        env,
        verbose=1,
        tensorboard_log=log_dir,
        ent_coef=args.ent_coef,
        learning_rate=args.learning_rate,
        seed=args.seed,
        batch_size=2048,
        n_steps=2048,
    )

    # Save checkpoint every 20k steps
    checkpoint_callback = CheckpointCallback(
        save_freq=20000,
        save_path=f"{log_dir}/checkpoints",
        name_prefix="ppo_model"
    )

    # Train
    try:
        model.learn(total_timesteps=args.steps, callback=checkpoint_callback)
        model.learn(total_timesteps=args.steps, callback=checkpoint_callback)
        model.save(f"{log_dir}/final_model")
        
        # Save Brain Metadata
        try:
            # Extract info from first env in VecEnv
            feature_signature = env.get_attr("feature_signature")[0]
            feature_profile = env.get_attr("feature_profile")[0]
            
            brain_metadata = {
                "brain_id": args.run_name,
                "name": args.run_name,
                "created_at": datetime.datetime.now().isoformat(),
                "algo_type": "ppo",
                "symbol": args.symbol,
                "dataset_id": args.dataset,
                "feature_profile": feature_profile,
                "feature_signature_hash": feature_signature,
                "train_steps": args.steps,
                "hyperparams": {
                    "ent_coef": args.ent_coef,
                    "learning_rate": args.learning_rate,
                    "batch_size": 2048,
                    "n_steps": 2048,
                },
                "risk_config": {
                    # We could fetch this from env.rl_config but arguments are visible here
                },
                "framework": "stable-baselines3",
                "model_file": "final_model.zip"
            }
            
            with open(f"{log_dir}/brain.json", "w") as f:
                json.dump(brain_metadata, f, indent=4)
                
            print(f"Brain metadata saved to {log_dir}/brain.json")
        except Exception as e:
            print(f"Failed to save brain metadata: {e}")

        print("Training completed successfully.")
    except KeyboardInterrupt:
        print("Training interrupted. Saving current model...")
        model.save(f"{log_dir}/interrupted_model")
    except Exception as e:
        print(f"Training failed: {e}")
    finally:
        env.close()

if __name__ == "__main__":
    main()
