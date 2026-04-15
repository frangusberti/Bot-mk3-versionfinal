
import os
import sys
import gymnasium as gym

# Añadir ruta para importar grpc_env
sys.path.append(os.path.join(os.getcwd(), 'python'))
from bot_ml.grpc_env import GrpcTradingEnv

def run_forensic_audit():
    print("Starting Forensic Fallback Audit (STRESS TEST)...")
    
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50
    )
    
    # Configuración de Auditoría Causal
    rl_config = {
        "use_exit_curriculum_d1": True,
        "maker_first_exit_timeout_ms": 8000, # 8 seconds patience
        "exit_fallback_loss_bps": 5.0,        # Aggressive panic if losing >5bps
        "exit_fallback_mfe_giveback_bps": 3.0, # Panic if profit drops 3bps from peak
        "exit_fallback_thesis_decay_threshold": 0.2, # Panic if microprice shifts against us
        "exit_maker_pricing_multiplier": 0.5, # Aggressive maker pricing
        "profit_floor_bps": 0.0,              # Allow any exit for audit
        "stop_loss_bps": 100.0,
        "use_selective_entry": False,        # Disable entry gate to force positions
    }
    
    obs, info = env.reset(options={"rl_config": rl_config})
    done = False
    
    fallback_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    reasons_map = {
        1: "Timeout (8000ms Exhausted)",
        2: "Loss Escape (Hard Negative uPnL)",
        3: "MFE Giveback (Winning Trade Degrading)",
        4: "Thesis Decay (Alpha inversion)",
        5: "DD Limit (Risk Protection)"
    }
    
    step_count = 0
    while not done and step_count < 20000:
        action = 0 
        
        # Cycle: Open at N, Close at N+200
        mod_step = step_count % 500
        if mod_step == 10: 
            action = 1 # OPEN_LONG
        elif mod_step == 150: 
            action = 4 # CLOSE_LONG -> Should trigger D1 Curriculum
        
        obs, reward, done, truncated, info = env.step(action)
        
        if info:
            reason = info.get("exit_fallback_reason", 0)
            if reason > 0:
                fallback_counts[reason] += 1
                print(f"STEP {step_count} | !!! FALLBACK DETECTED: {reasons_map[reason]}")
            
        step_count += 1
        if truncated: done = True
        
    print("\n" + "="*40)
    print("--- FORENSIC AUDIT SUMMARY ---")
    print("="*40)
    for r_id, count in fallback_counts.items():
        print(f"{reasons_map[r_id]:<40}: {count}")
    print("="*40)
    print(f"Total steps simulated: {step_count}")
    
    env.close()

if __name__ == "__main__":
    run_forensic_audit()
