import os
import argparse
import numpy as np
from stable_baselines3 import PPO
from collections import defaultdict

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
import bot_pb2

ACTION_LABELS = [
    "HOLD", "POST_BID", "JOIN_BID", "POST_ASK", "JOIN_ASK", "CANCEL_ALL", "TAKER_EXIT"
]

def run_eval(model, env_kwargs, deterministic=True, steps=5000):
    env = GrpcTradingEnv(**env_kwargs)
    obs, info = env.reset()
    
    actions = defaultdict(int)
    initial_equity = info.get("equity", 10000.0)
    
    maker_fills = 0
    stale_expiries = 0
    toxic_fills = 0
    
    steps_run = 0
    for i in range(steps):
        steps_run = i + 1
        action, _ = model.predict(obs, deterministic=deterministic)
        action_val = int(action)
        actions[action_val] += 1
        
        obs, reward, terminated, truncated, info = env.step(action_val)
        
        maker_fills += info.get("maker_fills", 0)
        stale_expiries += info.get("stale_expiries", 0)
        toxic_fills += info.get("toxic_fills", 0)
        
        if terminated or truncated:
            print(f"    [EVAL-TERM] Ended at Step {i} | Reason: {info.get('reason', 'UNKNOWN')}")
            break
            
    final_equity = info.get("equity", initial_equity)
    pnl_pct = (final_equity / initial_equity - 1) * 100
    
    total_acts = sum(actions.values())
    dist = {}
    for j, label in enumerate(ACTION_LABELS):
        count = actions.get(j, 0)
        dist[label] = (count / total_acts * 100) if total_acts > 0 else 0
        
    env.close()
    return {
        "steps": steps_run,
        "dist": dist,
        "maker_fills": maker_fills,
        "stale_expiries": stale_expiries,
        "toxic_fills": toxic_fills,
        "pnl_pct": pnl_pct,
        "final_equity": final_equity
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--server", type=str, default="localhost:50051")
    args = parser.parse_args()
    
    print(f"Loading model from {args.model}")
    model = PPO.load(args.model)
    
    env_kwargs = {
        "server_addr": args.server,
        "dataset_id": "stage2_eval",
        "symbol": "BTCUSDT",
        "maker_fee": 2.0,
        "taker_fee": 5.0,
        "slip_bps": 1.0,
        "fill_model": bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
    }
    
    print("\n--- Deterministic Evaluation ---")
    det_res = run_eval(model, env_kwargs, deterministic=True, steps=5000)
    print(f"Steps run: {det_res['steps']}")
    print("Action dist:")
    for k, v in det_res['dist'].items():
        if v > 0: print(f"  {k}: {v:.1f}%")
    print(f"Maker fills: {det_res['maker_fills']} | Toxic fills: {det_res['toxic_fills']} | Stale: {det_res['stale_expiries']}")
    print(f"Equity: {det_res['final_equity']:.2f} | PnL: {det_res['pnl_pct']:.2f}%")
    
    print("\n--- Stochastic Evaluation ---")
    sto_res = run_eval(model, env_kwargs, deterministic=False, steps=5000)
    print(f"Steps run: {sto_res['steps']}")
    print("Action dist:")
    for k, v in sto_res['dist'].items():
        if v > 0: print(f"  {k}: {v:.1f}%")
    print(f"Maker fills: {sto_res['maker_fills']} | Toxic fills: {sto_res['toxic_fills']} | Stale: {sto_res['stale_expiries']}")
    print(f"Equity: {sto_res['final_equity']:.2f} | PnL: {sto_res['pnl_pct']:.2f}%")

if __name__ == "__main__":
    main()
