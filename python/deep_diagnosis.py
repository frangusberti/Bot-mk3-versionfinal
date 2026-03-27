"""
deep_diagnosis.py -- Analyze policy logits and economic barriers.

1. Policy Dominance: Get probabilities for HOLD vs others.
2. Economic Barrier: Calculate BPS costs vs Volatility in the dataset.
3. Verdict: Selectivity vs Trivial Optimum.
"""
import sys
import os
import torch
import torch.nn.functional as F
import pandas as pd
import numpy as np
from collections import defaultdict

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from stable_baselines3 import PPO
from grpc_env import GrpcTradingEnv

ACTION_LABELS = [
    "HOLD", "OPEN_LONG", "OPEN_SHORT", "CLOSE_ALL",
    "REDUCE_25", "REDUCE_50", "REDUCE_100"
]

def diagnose_policy(model_path, dataset_id):
    print(f"\n--- POLICY DOMINANCE DIAGNOSIS ---")
    model = PPO.load(model_path)
    env = GrpcTradingEnv(server_addr="localhost:50051", dataset_id=dataset_id, symbol="BTCUSDT")
    
    obs, info = env.reset()
    all_probs = []
    entropies = []
    
    # Analyze 500 steps
    for i in range(500):
        # Convert obs to torch tensor
        obs_tensor = torch.as_tensor(obs).unsqueeze(0).to(model.policy.device)
        
        # Get distribution
        with torch.no_grad():
            distribution = model.policy.get_distribution(obs_tensor)
            probs = F.softmax(distribution.distribution.logits, dim=-1).cpu().numpy()[0]
            entropy = distribution.entropy().cpu().numpy()[0]
        
        all_probs.append(probs)
        entropies.append(entropy)
        
        # Take step (deterministic)
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, terminated, truncated, info = env.step(int(action))
        if terminated or truncated:
            obs, info = env.reset()

    avg_probs = np.mean(all_probs, axis=0)
    avg_entropy = np.mean(entropies)
    
    print(f"\nAverage Action Probabilities (Eval Context):")
    for i, label in enumerate(ACTION_LABELS):
        print(f"  {label:10}: {avg_probs[i]*100:6.2f}%")
    
    print(f"\nPolicy Entropy: {avg_entropy:.4f}")
    
    # Analyze Margin: HOLD vs Second Best
    hold_prob = avg_probs[0]
    others = avg_probs[1:]
    second_best_idx = np.argmax(others) + 1
    second_best_prob = avg_probs[second_best_idx]
    
    margin = hold_prob - second_best_prob
    print(f"Margin (HOLD - {ACTION_LABELS[second_best_idx]}): {margin*100:.2f}%")
    
    if hold_prob > 0.99 and second_best_prob < 0.001:
        print("Verdict: OVERWHELMING DOMINANCE. HOLD wins by a landslide.")
    else:
        print("Verdict: NARROW COMPETITION. Other actions are 'alive' but not crossing the threshold.")

def diagnose_economics(dataset_path):
    print(f"\n--- ECONOMIC BARRIER DIAGNOSIS ---")
    df = pd.read_parquet(dataset_path)
    
    # Assume taker fee = 2 bps (0.02%) and spread = 1 bps (approx)
    # Market cost = spread + taker_fee * 2 (entry/exit)
    # But in bps: entry_cost (half spread + fee) + exit_cost (half spread + fee)
    taker_fee_bps = 2.0
    avg_spread_bps = 0.5 # typical for BTCUSDT liquid periods
    round_trip_cost_bps = avg_spread_bps + (taker_fee_bps * 2) # ~4.5 bps
    
    print(f"Assumed Round-Trip Cost: {round_trip_cost_bps:.2f} bps")
    
    # Analyze volatility at different horizons (steps)
    prices = df['price'].values
    windows = [50, 200, 1000] # steps
    
    print(f"\nPrice Magnitude vs Costs:")
    for w in windows:
        # Calculate max-min move within sliding window
        # simplified: absolute pct change after W steps
        pct_changes = np.abs((prices[w:] - prices[:-w]) / prices[:-w] * 10000) # in bps
        avg_move = np.mean(pct_changes)
        p90_move = np.percentile(pct_changes, 90)
        
        ratio = avg_move / round_trip_cost_bps
        print(f"  Horizon {w:4} steps: Avg Move {avg_move:6.2f} bps | Ratio: {ratio:4.2f}x | 90th Pct: {p90_move:6.2f} bps")
        
    print("\nInterpretation:")
    print("If Ratio < 1.0x: Taker strategy is mathematically impossible (costs > move).")
    print("If Ratio 1.0-2.0x: Discovery is 'Hard Mode'. Needs perfect timing.")
    print("If Ratio > 3.0x: Strategy should be discoverable.")

if __name__ == "__main__":
    # 1. Diagnose Policy
    model_p = "python/runs_train/stage3/model_1000000.zip"
    if os.path.exists(model_p):
        diagnose_policy(model_p, "stage2_eval")
    else:
        print(f"Model not found: {model_p}")
        
    # 2. Diagnose Economics
    data_p = "runs/stage2_eval/datasets/stage2_eval/normalized_events.parquet"
    if os.path.exists(data_p):
        diagnose_economics(data_p)
    else:
        print(f"Dataset not found: {data_p}")
