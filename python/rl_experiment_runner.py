import os
import sys
import json
import argparse
import subprocess
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from bc_eval import run_eval
from rl_scorecard import generate_scorecard
import bot_pb2

CHECKPOINTS = [50_000, 100_000, 250_000]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bc_model", type=str, required=True, help="Path to BC pretrained model zip")
    parser.add_argument("--out_dir", type=str, default="python/runs_rl")
    parser.add_argument("--server", type=str, default="localhost:50051")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    baseline_path = os.path.join(args.out_dir, "bc_baseline_metrics.json")

    # Step 1: Establish BC Baseline if it doesn't exist
    if not os.path.exists(baseline_path):
        print(f"\n[RUNNER] Establishing BC Baseline from {args.bc_model}...")
        bc_model = PPO.load(args.bc_model)
        
        env_kwargs = {
            "server_addr": args.server,
            "dataset_id": "stage2_eval",
            "symbol": "BTCUSDT",
            "maker_fee": 2.0,
            "taker_fee": 5.0,
            "slip_bps": 1.0,
            "fill_model": bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
        }
        
        det_metrics = run_eval(bc_model, env_kwargs, deterministic=True, steps=5000)
        sto_metrics = run_eval(bc_model, env_kwargs, deterministic=False, steps=5000)
        
        baseline_metrics = {
            "deterministic": det_metrics,
            "stochastic": sto_metrics,
        }
        
        with open(baseline_path, "w") as f:
            json.dump(baseline_metrics, f, indent=2)
        print(f"[RUNNER] Baseline saved to {baseline_path}")
    else:
        with open(baseline_path, "r") as f:
            baseline_metrics = json.load(f)
            
    # Step 2: Initialize RL Environment
    env = GrpcTradingEnv(
        server_addr=args.server,
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
        fill_model=bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
    )
    vec_env = DummyVecEnv([lambda: env])

    # Load BC weights into a new PPO configured for safe adaptation
    print(f"\n[RUNNER] Loading BC prior for RL Fine-Tuning...")
    
    # We update the environment and conservative hyperparameters
    custom_objects = {
        "learning_rate": 5e-5,  # Conservative LR
        "ent_coef": 0.015,      # Enough entropy to break deadlock but not go wild
        "clip_range": 0.15,     # Tighter trust region
        "target_kl": 0.015      # Early stopping KL
    }
    model = PPO.load(args.bc_model, env=vec_env, custom_objects=custom_objects)
    
    # Step 3: Train across checkpoints
    total_timesteps_run = 0
    
    for ckpt in CHECKPOINTS:
        steps_to_run = ckpt - total_timesteps_run
        print(f"\n=======================================================")
        print(f"[RUNNER] Training Phase: {total_timesteps_run} -> {ckpt} steps")
        print(f"=======================================================")
        
        model.learn(total_timesteps=steps_to_run, reset_num_timesteps=False)
        total_timesteps_run = ckpt
        
        checkpoint_path = os.path.join(args.out_dir, f"checkpoint_{ckpt}")
        model.save(checkpoint_path)
        print(f"[RUNNER] Checkpoint saved: {checkpoint_path}")
        
        # Step 4: Evaluate Checkpoint
        print(f"\n[RUNNER] Evaluating Checkpoint {ckpt}...")
        env_kwargs = {
            "server_addr": args.server,
            "dataset_id": "stage2_eval",
            "symbol": "BTCUSDT",
            "maker_fee": 2.0,
            "taker_fee": 5.0,
            "slip_bps": 1.0,
            "fill_model": bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
        }
        
        det_metrics = run_eval(model, env_kwargs, deterministic=True, steps=5000)
        sto_metrics = run_eval(model, env_kwargs, deterministic=False, steps=5000)
        
        metrics = {
            "deterministic": det_metrics,
            "stochastic": sto_metrics,
            "training_steps": ckpt
        }
        
        metric_file = os.path.join(args.out_dir, f"checkpoint_{ckpt}_metrics.json")
        with open(metric_file, "w") as f:
            json.dump(metrics, f, indent=2)
            
        scorecard = generate_scorecard(metrics, baseline_metrics)
        scorecard_file = os.path.join(args.out_dir, f"checkpoint_{ckpt}_scorecard.json")
        with open(scorecard_file, "w") as f:
            json.dump(scorecard, f, indent=2)
            
        print(f"\n[RUNNER] SCORECARD RESULTS ({ckpt} steps):")
        print(f"Status: {scorecard['status']}")
        for r in scorecard['reasons']:
            print(f"  - {r}")
        print(f"Recommendation: {scorecard.get('recommendation', 'N/A')}")
        
        if scorecard["status"] == "FAIL":
            print(f"\n[RUNNER] FATAL: Scorecard triggered FAIL. Aborting experiment.")
            report_fail_path = os.path.join(args.out_dir, f"checkpoint_{ckpt}_report.md")
            with open(report_fail_path, "w") as f:
                f.write(f"# RL Checkpoint {ckpt} ABORTED\n\n**Pathologies:** {scorecard['pathologies']}\n**Reasons:** {scorecard['reasons']}")
            break

    print("\n[RUNNER] Experiment concluded.")

if __name__ == "__main__":
    main()
