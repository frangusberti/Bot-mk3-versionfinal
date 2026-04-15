
import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime

# Añadir ruta para importar grpc_env
sys.path.append(os.path.join(os.getcwd(), 'python'))
from bot_ml.grpc_env import GrpcTradingEnv

def run_forensic_validation_50k():
    print(f"[{datetime.now()}] Starting Long Forensic Validation (50,000 steps)...")
    
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_train",
        symbol="BTCUSDT",
        initial_equity=50000.0,
        max_pos_frac=0.50
    )
    
    # CONFIGURACIÓN ESTABILIZADA (D1 + P2)
    rl_config = {
        "use_exit_curriculum_d1": True,
        "maker_first_exit_timeout_ms": 8000,
        "exit_maker_pricing_multiplier": 0.5,
        "profit_floor_bps": 0.0,
        "use_selective_entry": True, # Guardrail V2.0b activo
    }
    
    obs, info = env.reset(options={"rl_config": rl_config})
    
    # TELEMETRÍA DE SEGUIMIENTO
    history = []
    fallbacks = []
    
    total_steps = 50000
    for step in range(total_steps):
        # En esta validación forense, usamos una política de Hold 
        # para que el Agente RL (servidor) maneje la lógica interna 
        # o se comporte según su estado actual cargado.
        # El objetivo es ver el comportamiento del currículum de salida.
        
        # Nota: Como queremos una lectura limpia del régimen actual,
        # dejamos que el Agente tome sus decisiones (si hay policy cargada)
        # o simplemente observamos el flujo del mercado con el currículum activo.
        
        action = 0 # En este entorno, el servidor maneja la política si hay una cargada
        obs, reward, done, truncated, info = env.step(action)
        
        # Capturamos datos forenses en cada step
        if info.get("trades_executed", 0) > 0 or info.get("exit_fallback_reason", 0) > 0:
            history.append(info.copy())
            if info.get("exit_fallback_reason", 0) > 0:
                fallbacks.append(info.get("exit_fallback_reason"))
        
        if step % 5000 == 0:
            print(f"[{datetime.now()}] Progress: {step}/{total_steps} steps...")
            
        if done or truncated:
            obs, info = env.reset(options={"rl_config": rl_config})
            
    print(f"[{datetime.now()}] Validation Complete.")
    
    # PROCESAMIENTO DE SCORECARD (Simulado para estructura de reporte final)
    # En una corrida real, extraeríamos estos datos del cumulative_info y trade_logs descriptos en StepInfo
    
    env.close()

if __name__ == "__main__":
    run_forensic_validation_50k()
