
import os
import sys
import gymnasium as gym

# Añadir ruta para importar grpc_env
sys.path.append(os.path.join(os.getcwd(), 'python'))
from bot_ml.grpc_env import GrpcTradingEnv

def run_performance_audit():
    print("Starting Performance Mix Audit (Maker vs Taker Implementation)...")
    
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50
    )
    
    rl_config = {
        "use_exit_curriculum_d1": True,
        "maker_first_exit_timeout_ms": 8000,
        "exit_maker_pricing_multiplier": 0.5,
        "profit_floor_bps": 0.0,
        "use_selective_entry": False,
    }
    
    obs, info = env.reset(options={"rl_config": rl_config})
    done = False
    
    stats = {
        "maker_fills": 0,
        "taker_fills": 0,
        "exit_fallbacks": 0,
        "total_trades": 0
    }
    
    step_count = 0
    while not done and step_count < 15000:
        mod_step = step_count % 500
        action = 0 
        if mod_step == 10: action = 1 # OPEN_LONG
        elif mod_step == 150: action = 4 # CLOSE_LONG
        
        obs, reward, done, truncated, info = env.step(action)
        
        if info:
            stats["total_trades"] += info.get("trades_executed", 0)
            stats["maker_fills"] += info.get("maker_fills", 0)
            
            # Un fill Taker en salida es un fallback
            fallback_reason = info.get("exit_fallback_reason", 0)
            if fallback_reason > 0:
                stats["exit_fallbacks"] += 1
                stats["taker_fills"] += 1
            
        step_count += 1
        if truncated: done = True
        
    print("\n" + "="*40)
    print("--- EXECUTION QUALITY AUDIT ---")
    print("="*40)
    print(f"Total Trades Executed   : {stats['total_trades']}")
    print(f"Maker Fills (Passive)   : {stats['maker_fills']}")
    print(f"Taker Fills (Fallbacks) : {stats['exit_fallbacks']}")
    
    maker_ratio = (stats['maker_fills'] / stats['total_trades'] * 100) if stats['total_trades'] > 0 else 0
    print(f"Maker Capture Ratio     : {maker_ratio:.1f}%")
    print("="*40)
    
    env.close()

if __name__ == "__main__":
    run_performance_audit()
