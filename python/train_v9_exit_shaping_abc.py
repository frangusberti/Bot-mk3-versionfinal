
import os
import sys
import torch
import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from bot_ml.grpc_env import GrpcTradingEnv

V20B_BASE_CONFIG = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.02,
    use_winner_unlock=True,
    reward_thesis_decay_weight=0.0001,
    use_selective_entry_long_v2=True,
    long_veto_imbalance_threshold=-0.20,
    long_veto_bb_pos_5m_threshold=0.40,
    long_veto_regime_dead_threshold=0.50,
)

def run_branch(branch_name, taker_penalty, maker_bonus, steps=25000):
    print(f"\n--- STARTING BRANCH {branch_name} (Penalty={taker_penalty}, Bonus={maker_bonus}) ---")
    
    out_dir = f"python/runs_train/abc_exit_shaping/{branch_name}"
    os.makedirs(out_dir, exist_ok=True)
    
    base_model = "python/runs_train/training_v9_selective_v20b/model_v20b_final.zip"
    base_venv = "python/runs_train/training_v9_selective_v20b/venv_v20b_final.pkl"
    
    def mask_fn(env): return env.action_masks()
    
    def make_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            fill_model=2,
            reward_exit_taker_penalty_weight=taker_penalty,
            reward_exit_maker_bonus_weight=maker_bonus,
            **V20B_BASE_CONFIG
        ), mask_fn)
    
    venv = DummyVecEnv([make_env for _ in range(4)])
    venv = VecNormalize.load(base_venv, venv)
    
    model = MaskablePPO.load(base_model, env=venv, device="cuda" if torch.cuda.is_available() else "cpu",
                             custom_objects={"learning_rate": 5e-6})
    
    model.learn(total_timesteps=steps, progress_bar=True)
    
    model_path = os.path.join(out_dir, f"model_{branch_name}.zip")
    venv_path = os.path.join(out_dir, f"venv_{branch_name}.pkl")
    model.save(model_path)
    venv.save(venv_path)
    
    # Audit on Validation
    print(f"--- AUDITING BRANCH {branch_name} ---")
    def make_val_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_val",
            symbol="BTCUSDT",
            fill_model=2,
            reward_exit_taker_penalty_weight=taker_penalty,
            reward_exit_maker_bonus_weight=maker_bonus,
            **V20B_BASE_CONFIG
        ), mask_fn)
    
    val_venv = DummyVecEnv([make_val_env])
    val_venv = VecNormalize.load(venv_path, val_venv)
    val_venv.training = False
    val_venv.norm_reward = False
    
    obs = val_venv.reset()
    total_trades = 0
    maker_exits = 0
    taker_exits = 0
    pnl_long = 0.0
    pnl_short = 0.0
    realized_acc = 0.0
    never_05 = 0
    in_pos = False
    max_upnl = -999.0
    
    for _ in range(30000):
        masks = val_venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, done, info_list = val_venv.step(action)
        info = info_list[0]
        
        pos_qty = info.get("position_qty", 0.0)
        upnl_bps = info.get("unrealized_pnl", 0.0) / info.get("equity", 10000.0) * 10000.0
        
        if info.get("trades_executed", 0) > 0:
            for fill in info.get("fills", []):
                q = fill.get('qty', 0.0) if isinstance(fill, dict) else getattr(fill, 'qty', 0.0)
                l = fill.get('liquidity', '') if isinstance(fill, dict) else getattr(fill, 'liquidity', '')
                if q > 1e-9:
                    if not in_pos: 
                        total_trades += 1
                    else:
                        if abs(pos_qty) < 1e-9: # Closed
                            if "Maker" in l: maker_exits += 1
                            else: taker_exits += 1
        
        if abs(pos_qty) > 1e-9:
            in_pos = True
            max_upnl = max(max_upnl, upnl_bps)
        else:
            if in_pos:
                if max_upnl < 0.5: never_05 += 1
                max_upnl = -999.0
            in_pos = False
        
        if done: break
        
    print(f"RESULTS FOR {branch_name}:")
    print(f" 1) Total Trades    : {total_trades}")
    print(f" 2) Realized PnL    : {info.get('realized_pnl', 0.0):.2f}")
    print(f" 3) Net PnL (Fees)  : {info.get('realized_pnl', 0.0) - info.get('fees_paid', 0.0):.2f}")
    print(f" 4) Fees Total      : {info.get('fees_paid', 0.0):.2f}")
    print(f" 6) Exit Maker/Taker: {maker_exits} / {taker_exits}")
    print(f" 7) % Never reach 05: {never_05/total_trades*100 if total_trades>0 else 0:.1f}%")
    print(f" 17) Done Reason    : {info.get('reason', 'UNKNOWN')}")

def main():
    # A) Baseline
    run_branch("A_Baseline", 0.0, 0.0, steps=25000)
    # B) Taker Penalty
    run_branch("B_Penalty", 0.5, 0.0, steps=25000)
    # C) Penalty + Maker Bonus
    run_branch("C_Hybrid", 0.5, 0.2, steps=25000)

if __name__ == "__main__":
    main()
