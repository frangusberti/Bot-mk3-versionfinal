import os
import argparse
import json
from stable_baselines3 import PPO

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
import bot_pb2
from bc_eval import run_eval
from rl_scorecard import generate_scorecard

def evaluate_checkpoint(model_path, server_addr="localhost:50051", steps=5000, baseline_path=None):
    print(f"Loading checkpoint: {model_path}")
    model = PPO.load(model_path)
    
    env_kwargs = {
        "server_addr": server_addr,
        "dataset_id": "stage2_eval",
        "symbol": "BTCUSDT",
        "maker_fee": 2.0,
        "taker_fee": 5.0,
        "slip_bps": 1.0,
        "fill_model": bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
    }
    
    print("\n--- Deterministic Evaluation ---")
    det_metrics = run_eval(model, env_kwargs, deterministic=True, steps=steps)
    
    print("\n--- Stochastic Evaluation ---")
    sto_metrics = run_eval(model, env_kwargs, deterministic=False, steps=steps)

    metrics = {
        "deterministic": det_metrics,
        "stochastic": sto_metrics,
    }

    baseline = None
    if baseline_path and os.path.exists(baseline_path):
        with open(baseline_path, "r") as f:
            baseline = json.load(f)

    scorecard = generate_scorecard(metrics, baseline)
    return metrics, scorecard

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--baseline", type=str, default=None)
    parser.add_argument("--out_metrics", type=str, default="checkpoint_metrics.json")
    parser.add_argument("--out_scorecard", type=str, default="checkpoint_scorecard.json")
    args = parser.parse_args()

    m, s = evaluate_checkpoint(args.model, baseline_path=args.baseline)
    
    with open(args.out_metrics, "w") as f:
        json.dump(m, f, indent=2)

    with open(args.out_scorecard, "w") as f:
        json.dump(s, f, indent=2)

    print("\n=== SCORECARD OUTPUT ===")
    print(json.dumps(s, indent=2))

if __name__ == "__main__":
    main()
