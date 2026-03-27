import os
import argparse
import numpy as np
import torch
from stable_baselines3 import PPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from collections import defaultdict

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG", 
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE"
]

def run_lifecycle_audit(model_path, venv_path, steps=5000, dataset="golden_l2_v1_val", server="localhost:50051"):
    print(f"\n[AUDIT] Launching Lifecycle Semantic Audit on {dataset}...")
    
    # 1. Setup Env
    env_kwargs = {
        "server_addr": server,
        "dataset_id": dataset,
        "symbol": "BTCUSDT",
        "fill_model": 2, # Optimistic for BC capability check
        "min_post_offset_bps": 0.20,
        "override_action_dim": 10
    }
    
    raw_env = GrpcTradingEnv(**env_kwargs)
    venv = DummyVecEnv([lambda: raw_env])
    
    if os.path.exists(venv_path):
        print(f"[AUDIT] Loading VecNormalize stats from {venv_path}")
        venv = VecNormalize.load(venv_path, venv)
        venv.training = False
        venv.norm_reward = False
        
    # 2. Load Model
    model = PPO.load(model_path, env=venv)
    
    # 3. Step Loop
    obs = venv.reset()
    action_counts = defaultdict(int)
    total_trades = 0
    total_gate_blocked = 0
    
    print(f"[AUDIT] Running {steps} steps...")
    for i in range(steps):
        action, _ = model.predict(obs, deterministic=True)
        action_val = int(action[0])
        action_counts[action_val] += 1
        
        obs, reward, done, info = venv.step(action)
        
        info0 = info[0]
        total_trades += info0.get("trades_executed", 0)
        total_gate_blocked += info0.get("gate_offset_blocked", 0)
        
        if done:
            obs = venv.reset()

    # 4. Report
    total_acts = sum(action_counts.values())
    print("\n" + "="*40)
    print("VNEXT BC LIFECYCLE AUDIT REPORT")
    print("="*40)
    print(f"Total Steps: {total_acts}")
    print(f"Total Trades: {total_trades}")
    print(f"Gate Blocks: {total_gate_blocked} ({(total_gate_blocked/total_acts*100):.1f}% of steps)")
    
    print("\nAction Distribution:")
    for i, label in enumerate(ACTION_LABELS):
        count = action_counts.get(i, 0)
        pct = (count / total_acts * 100) if total_acts > 0 else 0
        status = " [ACTIVE]" if pct > 0.5 else " [IDLE]"
        print(f"  {i}: {label:<15} | {pct:>6.2f}% {status}")

    # Semantic Checks
    open_pct = (action_counts[1] + action_counts[5]) / total_acts * 100
    add_pct = (action_counts[2] + action_counts[6]) / total_acts * 100
    red_pct = (action_counts[3] + action_counts[7]) / total_acts * 100
    close_pct = (action_counts[4] + action_counts[8]) / total_acts * 100
    
    print("\nSemantic Summary:")
    print(f"  OPEN  Actions: {open_pct:.2f}%")
    print(f"  ADD   Actions: {add_pct:.2f}%")
    print(f"  RED   Actions: {red_pct:.2f}%")
    print(f"  CLOSE Actions: {close_pct:.2f}%")
    
    verdict = "PASS"
    if red_pct < 0.1 and close_pct < 0.1:
        verdict = "FAIL: Policy failed to learn exit lifecycle (REDUCE/CLOSE)."
    elif open_pct > 30.0:
        verdict = "WARN: Excessive OPEN attempt. Potential churn."
    elif total_trades == 0:
        verdict = "FAIL: Zero fills. Gate 0.20 bps might still be too tight or policy misaligned."
        
    print(f"\nVERDICT: {verdict}")
    print("="*40 + "\n")
    
    venv.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--venv", type=str, required=True)
    parser.add_argument("--steps", type=int, default=10000)
    parser.add_argument("--dataset", type=str, default="golden_l2_v1_val")
    parser.add_argument("--server", type=str, default="localhost:50051")
    args = parser.parse_args()
    
    # Update hardcoded env_kwargs in main or pass it
    run_lifecycle_audit(args.model, args.venv, steps=args.steps, dataset=args.dataset, server=args.server)
