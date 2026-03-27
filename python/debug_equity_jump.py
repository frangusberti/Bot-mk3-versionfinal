import os
import sys
from datetime import datetime

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from grpc_env import GrpcTradingEnv
import bot_pb2

def debug_reset_jump():
    print("Connecting to bot-server...")
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_eval",
        symbol="BTCUSDT",
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
        fill_model=bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC,
        reward_tib_bonus_bps=0.0
    )
    
    print("Resetting environment...")
    obs, info = env.reset()
    print(f"Initial Equity: {info.get('equity')}")
    
    # We'll use JOIN_ASK (action 4) which was dominant in the failed audit
    action = 4 
    
    for i in range(100):
        obs, reward, terminated, truncated, info = env.step(action)
        equity = info.get('equity')
        if equity > 20000:
            print(f"\n!!! JUMP DETECTED at step {i} !!!")
            print(f"Equity: {equity}")
            print(f"Info: {info}")
            break
        if i % 10 == 0:
            print(f"Step {i}: Equity={equity}")
            
    env.close()

if __name__ == "__main__":
    debug_reset_jump()
