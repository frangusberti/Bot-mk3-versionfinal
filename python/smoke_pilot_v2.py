
import os
import sys
import torch
import numpy as np
import pandas as pd
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
import grpc

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from bot_ml.grpc_env import GrpcTradingEnv
import bot_pb2

# Configuración del Veto LONG V2 acordada
VETO_CONFIG = dict(
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.2,
    long_veto_bb_pos_5m_threshold=0.45,
    long_veto_regime_dead_threshold=0.6,
)

BASE_CONFIG = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.02,
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    micro_strict=False,
)

def mask_fn(env):
    return env.action_masks()

def run_smoke_pilot(model_path, venv_path, dataset_id, steps=25000):
    def make_env():
        return ActionMasker(GrpcTradingEnv(server_addr="localhost:50051", 
                                          dataset_id=dataset_id,
                                          symbol="BTCUSDT",
                                          fill_model=2,
                                          **BASE_CONFIG,
                                          **VETO_CONFIG), mask_fn)
    
    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False
    venv.norm_reward = False
    
    model = MaskablePPO.load(model_path)
    obs = venv.reset()
    
    total_trades = 0
    longs = 0
    shorts = 0
    never_reach_05 = 0
    
    flow_vetos = 0
    bb_vetos = 0
    dead_vetos = 0
    
    last_realized = 0.0
    current_trade_max_upnl = -999.0
    in_pos = False
    
    for i in range(steps):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        
        obs, reward, done, info_list = venv.step(action)
        info = info_list[0]
        
        pos_qty = info.get("position_qty", 0.0)
        upnl = info.get("unrealized_pnl", 0.0)
        equity = info.get("equity", 10000.0)
        upnl_bps = upnl / equity * 10000.0
        
        # Track vetos
        flow_vetos += info.get("veto_long_flow_count", 0)
        bb_vetos += info.get("veto_long_bb_count", 0)
        dead_vetos += info.get("veto_long_dead_regime_count", 0)

        # Track trades
        trades_this_step = info.get("trades_executed", 0)
        if trades_this_step > 0:
            for fill in info.get("fills", []):
                # Identify entry fills
                # In StepInfo, fills are sent as a list of dicts or objects depending on wrapper
                q = fill.get('qty', 0.0) if isinstance(fill, dict) else getattr(fill, 'qty', 0.0)
                s = fill.get('side', '') if isinstance(fill, dict) else getattr(fill, 'side', '')
                if q > 0 and not in_pos: 
                    total_trades += 1
                    if s == "Side_Buy" or s == "Buy": longs += 1
                    else: shorts += 1
        
        if abs(pos_qty) > 1e-8:
            in_pos = True
            current_trade_max_upnl = max(current_trade_max_upnl, upnl_bps)
        else:
            if in_pos:
                # Trade just closed
                if current_trade_max_upnl < 0.5:
                    never_reach_05 += 1
                current_trade_max_upnl = -999.0
            in_pos = False

        if done: break

    # Final scorecard
    final_info = info
    realized = final_info.get("realized_pnl", 0.0)
    fees = final_info.get("fees_paid", 0.0)
    net_pnl = realized - fees
    
    print("\n" + "="*80)
    print(f" SMOKE PILOT RESULT: {dataset_id} ({steps} steps)")
    print("="*80)
    print(f" 1) Total Trades              : {total_trades}")
    print(f" 2) LONG vs SHORT Trades      : {longs} L / {shorts} S")
    print(f" 3) % Never Reach +0.5 bps    : {never_reach_05/total_trades*100:.1f}%" if total_trades > 0 else "N/A")
    print(f" 4) Net PnL After Fees        : {net_pnl:.2f} USDT")
    print(f" 5) Realized PnL              : {realized:.2f} USDT")
    print(f" 6) Fees Total                : {fees:.2f} USDT")
    
    print(f"\n--- VETO TELEMETRY (Rust) ---")
    print(f" 8) Veto LONG Flow Count     : {flow_vetos}")
    print(f" 9) Veto LONG BB Count       : {bb_vetos}")
    print(f" 10) Veto LONG Dead Regime   : {dead_vetos}")
    print("="*80 + "\n")

if __name__ == "__main__":
    m = "python/runs_train/calibration_v9/branch_A/model_A.zip"
    v = "python/runs_train/calibration_v9/branch_A/venv_A.pkl"
    run_smoke_pilot(m, v, "golden_l2_v1_val", steps=25000)
