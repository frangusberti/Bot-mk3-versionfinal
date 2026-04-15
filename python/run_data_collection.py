import os, sys, numpy as np
from collections import Counter

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from grpc_env import GrpcTradingEnv

def collect_data(steps=30000):
    # Ensure previous data is gone
    if os.path.exists("edge_audit_stream.jsonl"):
        os.remove("edge_audit_stream.jsonl")
        
    print(f"Starting gRPC env for {steps} steps of data collection...")
    env = GrpcTradingEnv(server_addr="localhost:50051", dataset_id="stage2_eval", symbol="BTCUSDT", decision_interval_ms=1000)
    obs = env.reset()
    
    for i in range(steps):
        # Action 0 = HOLD
        obs, reward, terminated, truncated, info = env.step(0)
        if (i+1) % 500 == 0:
            print(f" Progress: {i+1}/{steps} steps")
        if terminated or truncated:
            break

    print("Data collection complete. edge_audit_stream.jsonl generated.")

if __name__ == "__main__":
    collect_data()
