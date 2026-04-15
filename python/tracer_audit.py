
import os
import sys
import gymnasium as gym

# Añadir ruta para importar grpc_env
sys.path.append(os.path.join(os.getcwd(), 'python'))
from bot_ml.grpc_env import GrpcTradingEnv

def run_performance_audit():
    print("Starting Performance Mix Audit (THE FINAL FRONTIER)...")
    
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
    
    # EMPEZAMOS EL DIAGNÓSTICO REAL TRAS EL WARMUP
    # El servidor inyectará acciones automáticamente en Rust
    step_count = 0
    while step_count < 5000:
        action = 0 
        obs, reward, done, truncated, info = env.step(action)
        
        # Monitor para ver si hay fallbacks reportados
        fallback = info.get("exit_fallback_reason", 0)
        if fallback > 0:
            print(f"STEP {step_count} | !!! FALLBACK: {fallback}")
            
        step_count += 1
        if done or truncated: break
        
    env.close()

if __name__ == "__main__":
    run_performance_audit()
