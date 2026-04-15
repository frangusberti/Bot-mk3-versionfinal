"""
Short diagnostic run (2000 steps) to inspect feature health and action flow.
Logs every 100 steps.
"""
import sys, os, json, time
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'python'))
sys.path.insert(0, os.path.join(os.getcwd(), 'python', 'bot_ml'))

import numpy as np
import gymnasium as gym
from grpc_env import GrpcTradingEnv

def main():
    print(f"=== Diagnostic Run: 2000 steps ===")
    
    # Initialize env with ALL parameters in constructor since reset(options) is ignored
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50,
        use_exit_curriculum_d1=True,
        maker_first_exit_timeout_ms=8000,
        exit_maker_pricing_multiplier=0.5,
        profit_floor_bps=0.0,
        use_selective_entry=True,
        fill_model=2, # Optimistic
        seed=111,
    )
    
    obs, info = env.reset()
    print(f"Initial Obs: {obs[:5]}... Length: {len(obs)}")
    print(f"Initial Info: {info}")
    
    trades = 0
    for i in range(2000):
        # Force OPEN_LONG at step 10
        action = 1 if i == 10 else 0
        obs, reward, term, trunc, info = env.step(action)
        
        trades += info.get("trades_executed", 0)
        
        if i % 100 == 0 or info.get("trades_executed", 0) > 0:
            print(f"Step {i:4} | Action {action} | Trades {trades} | Pos {info.get('position_qty',0):.4f} | ObsQual {info.get('feature_health',{}).get('obs_quality',0)}")
            if info.get("trades_executed", 0) > 0:
                print(f"  !!! TRADE EXECUTED AT STEP {i} !!!")

        if term or trunc:
            break
            
    print(f"Run complete. Total trades: {trades}")
    env.close()

if __name__ == "__main__":
    main()
