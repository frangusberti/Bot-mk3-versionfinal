
import os
import sys
import torch
import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from bot_ml.grpc_env import GrpcTradingEnv

# EXACT V2.1
CONFIG = dict(
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
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.05,
    long_veto_bb_pos_5m_threshold=0.35,
    long_veto_regime_dead_threshold=0.40,
)

def run_audit():
    def make_env():
        return ActionMasker(GrpcTradingEnv(server_addr="localhost:50051", 
                                          dataset_id="golden_l2_v1_val",
                                          symbol="BTCUSDT",
                                          fill_model=2,
                                          **CONFIG), lambda e: e.action_masks())
    
    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load("python/runs_train/calibration_v9/branch_A/venv_A.pkl", venv)
    venv.training = False
    venv.norm_reward = False
    
    model = MaskablePPO.load("python/runs_train/calibration_v9/branch_A/model_A.zip")
    obs = venv.reset()
    
    total_trades = 0
    never_reach_05 = 0
    current_trade_max_upnl = -999.0
    in_pos = False
    
    for i in range(25000):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, _, done, info_list = venv.step(action)
        info = info_list[0]
        
        pos_qty = info.get("position_qty", 0.0)
        upnl_bps = info.get("unrealized_pnl", 0.0) / info.get("equity", 10000.0) * 10000.0
        
        trades_this_step = info.get("trades_executed", 0)
        if trades_this_step > 0:
            for fill in info.get("fills", []):
                q = fill.get('qty', 0.0) if isinstance(fill, dict) else getattr(fill, 'qty', 0.0)
                if q > 0 and not in_pos: total_trades += 1
        
        if abs(pos_qty) > 1e-8:
            in_pos = True
            current_trade_max_upnl = max(current_trade_max_upnl, upnl_bps)
        else:
            if in_pos:
                if current_trade_max_upnl < 0.5: never_reach_05 += 1
                current_trade_max_upnl = -999.0
            in_pos = False
        if done: break

    print(f"AUDIT V2.1 BASELINE RESULT:")
    print(f"Total Trades: {total_trades}")
    print(f"% Trash:      {never_reach_05/total_trades*100:.2f}%" if total_trades > 0 else "N/A")
    print(f"Net PnL:      {info.get('realized_pnl', 0.0) - info.get('fees_paid', 0.0):.2f}")
    print(f"Reason:       {info.get('reason')}")

if __name__ == "__main__":
    run_audit()
