
import os
import sys
import torch
import numpy as np
import pandas as pd
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from bot_ml.grpc_env import GrpcTradingEnv
import bot_pb2

# Feature Index Mapping (0-based, first 83 elements of obs vector)
IDX_MAP = {
    "slope_1m": 14,
    "slope_5m": 15,
    "slope_15m": 16,
    "rsi_1m": 48,
    "rsi_5m": 51,
    "bb_pos_1m": 50,
    "bb_pos_5m": 53,
    "microprice_minus_mid_bps": 31,
    "trade_imbalance_5s": 24,
    "regime_trend": 79,
    "regime_range": 80,
    "regime_shock": 81,
    "regime_dead": 82
}

VNEXT_CONFIG = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.02,
    use_winner_unlock=True,
    use_selective_entry=False,
    reward_thesis_decay_weight=0.0001,
    micro_strict=False,
)

def mask_fn(env):
    return env.action_masks()

def run_entry_audit(model_path, venv_path, dataset_id, steps=100000):
    def make_env():
        return ActionMasker(GrpcTradingEnv(server_addr="localhost:50051", 
                                          dataset_id=dataset_id,
                                          symbol="BTCUSDT",
                                          fill_model=2,
                                          **VNEXT_CONFIG), mask_fn)
    
    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False
    venv.norm_reward = False
    
    model = MaskablePPO.load(model_path)
    obs = venv.reset()
    
    entries = []
    last_realized = 0.0
    current_trade_entries = []
    current_trade_max_upnl = -999.0
    
    for i in range(steps):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        
        act_idx = int(action[0])
        # 0:HOLD, 1:OPEN_LONG, 2:ADD_LONG, 3:REDUCE_LONG, 4:CLOSE_LONG, 5:OPEN_SHORT...
        is_entry = act_idx in [1, 2, 5, 6]
        
        # Unnormalize obs to get raw values
        raw_obs = venv.unnormalize_obs(obs)[0]
        
        if is_entry:
            entry_data = {
                "action": act_idx,
                "obs": raw_obs.copy(),
                "i": i
            }
            current_trade_entries.append(entry_data)
        
        obs, reward, done, info_list = venv.step(action)
        info = info_list[0]
        
        pos_qty = info.get("position_qty", 0.0)
        upnl = info.get("unrealized_pnl", 0.0)
        equity = info.get("equity", 10000.0)
        upnl_bps = upnl / equity * 10000.0
        
        if abs(pos_qty) > 1e-8:
            current_trade_max_upnl = max(current_trade_max_upnl, upnl_bps)
        else:
            # Trade closed, flush entries
            for e in current_trade_entries:
                e["outcome_mfe"] = current_trade_max_upnl
                entries.append(e)
            current_trade_entries = []
            current_trade_max_upnl = -999.0

        if done: break

    if not entries:
        print("No entries recorded.")
        return

    df_data = []
    for e in entries:
        row = {"action": e["action"], "outcome_mfe": e["outcome_mfe"]}
        for name, idx in IDX_MAP.items():
            row[name] = e["obs"][idx]
        df_data.append(row)
    
    df = pd.DataFrame(df_data)
    
    longs = df[df["action"].isin([1, 2])]
    trash_longs = longs[longs["outcome_mfe"] < 0.5]
    winner_longs = longs[longs["outcome_mfe"] >= 2.0]
    
    shorts = df[df["action"].isin([5, 6])]
    winner_shorts = shorts[shorts["outcome_mfe"] >= 2.0]

    print("\n" + "="*80)
    print(f" ENTRY SIGNATURE AUDIT: {dataset_id}")
    print("="*80)
    
    def report_group(name, subdf):
        print(f"\n--- {name} (N={len(subdf)}) ---")
        if len(subdf) == 0: return
        stats = subdf.describe().loc[['mean', 'std']]
        for col in IDX_MAP.keys():
            if col in stats.columns:
                print(f"  {col:<25}: {stats.at['mean', col]:>8.4f} (std {stats.at['std', col]:.4f})")

    report_group("TRASH LONGs (MFE < 0.5)", trash_longs)
    report_group("WINNER LONGs (MFE > 2.0)", winner_longs)
    report_group("WINNER SHORTs (MFE > 2.0)", winner_shorts)
    print("="*80 + "\n")

if __name__ == "__main__":
    m = "python/runs_train/calibration_v9/branch_A/model_A.zip"
    v = "python/runs_train/calibration_v9/branch_A/venv_A.pkl"
    run_entry_audit(m, v, "golden_l2_v1_val")
