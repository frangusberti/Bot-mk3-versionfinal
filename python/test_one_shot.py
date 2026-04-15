"""
One-shot entry diagnostic. Sends Action 1 once, then HOLD for 2000 steps.
This prevents the "order kill-spam" issue if every step replaces the passive order.
"""
import sys, os, time
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'python'))
sys.path.insert(0, os.path.join(os.getcwd(), 'python', 'bot_ml'))

from grpc_env import GrpcTradingEnv

def main():
    print("=== One-Shot Entry Test ===")
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50,
        fill_model=1, # Semi-Optimistic
        seed=444,
    )
    
    obs, info = env.reset()
    print(f"Start Step 0. Pos: {info.get('position_qty',0)}")
    
    for i in range(2000):
        # Action 1 only at step 5
        action = 1 if i == 5 else 0
        obs, reward, term, trunc, info = env.step(action)
        
        pos = info.get("position_qty", 0)
        trades = info.get("trades_executed", 0)
        
        if trades > 0 or pos != 0:
            print(f"Step {i:4} | Pos {pos:.4f} | Trades {trades} | Reward {reward:.6f} | uPnL {info.get('unrealized_pnl',0):.2f}")
            if pos != 0 and i > 10:
                print("TRADE CONFIRMED. Waiting to see movement...")
                # Stay in position to observe PnL
        elif i % 500 == 0:
            print(f"Step {i:4} | Waiting... Orders: {info.get('active_order_count',0)}")

    env.close()

if __name__ == "__main__":
    main()
