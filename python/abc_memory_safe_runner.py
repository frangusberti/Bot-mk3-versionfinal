
import os
import sys
import torch
import gc
import numpy as np
from sb3_contrib import MaskablePPO
from sb3_contrib.common.wrappers import ActionMasker
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
import time

# Ensure local imports are visible
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from bot_ml.grpc_env import GrpcTradingEnv

# BASE CONFIG V2.0b
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
    fill_model=2 # OPTIMISTIC
)

def mask_fn(env): return env.action_masks()

def audit_model(branch_name, model, venv_path, taker_penalty, maker_bonus):
    print(f"\n[AUDIT] Starting detailed audit for {branch_name}...")
    
    def make_val_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_val",
            symbol="BTCUSDT",
            reward_exit_taker_penalty_weight=taker_penalty,
            reward_exit_maker_bonus_weight=maker_bonus,
            **V20B_BASE_CONFIG
        ), mask_fn)
    
    val_venv = DummyVecEnv([make_val_env])
    val_venv = VecNormalize.load(venv_path, val_venv)
    val_venv.training = False
    val_venv.norm_reward = False
    
    # Reset stats
    stats = {
        "trades": 0, "realized_pnl": 0.0, "fees_total": 0.0,
        "entry_fees": 0.0, "exit_fees": 0.0,
        "exit_maker": 0, "exit_taker": 0,
        "CLOSE_count": 0, "REDUCE_count": 0, "REDUCE_to_FLAT": 0,
        "pnl_long": 0.0, "pnl_short": 0.0,
        "done_reasons": {},
        "mfe_capture_sum": 0.0,
        "reach_2_bps": 0, "reach_4_bps": 0, "reach_6_bps": 0,
        "peak_reached_but_closed_below": {2: 0, 4: 0, 6: 0}
    }
    
    obs = val_venv.reset()
    in_pos = False
    current_side = 0
    max_upnl = -999.0
    processed_steps = 0
    
    while processed_steps < 35000:
        masks = val_venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, done, info_list = val_venv.step(action)
        info = info_list[0]
        processed_steps += 1
        
        pos_qty = info.get("position_qty", 0.0)
        upnl_bps = info.get("unrealized_pnl", 0.0) / info.get("equity", 10000.0) * 10000.0
        
        # Action tracking
        act_idx = int(action[0]) if isinstance(action, (np.ndarray, list)) else int(action)
        act_name = ["HOLD","OPEN_LONG","CLOSE_LONG","REDUCE_LONG","OPEN_SHORT","CLOSE_SHORT","REDUCE_SHORT","REPRICE","CANCEL_ALL","NOOP"][act_idx]
        if "CLOSE" in act_name: stats["CLOSE_count"] += 1
        if "REDUCE" in act_name: stats["REDUCE_count"] += 1

        # Trade tracking
        if info.get("trades_executed", 0) > 0:
            for fill in info.get("fills", []):
                price = fill.get('price', 0.0)
                qty = fill.get('qty', 0.0)
                q = qty * price
                l = fill.get('liquidity', '')
                f = fill.get('fee', 0.0)
                side = fill.get('side', '')
                
                stats["fees_total"] += abs(f)
                
                # Detective exit vs entry
                is_exit = (current_side > 0 and "Sell" in side) or (current_side < 0 and "Buy" in side)
                if is_exit:
                    stats["exit_fees"] += abs(f)
                    if "Maker" in l: stats["exit_maker"] += 1
                    else: stats["exit_taker"] += 1
                    if abs(pos_qty) < 1e-9 and "REDUCE" in act_name:
                        stats["REDUCE_to_FLAT"] += 1
                else:
                    stats["entry_fees"] += abs(f)
                    if not in_pos: stats["trades"] += 1
        
        if abs(pos_qty) > 1e-9:
            if not in_pos:
                in_pos = True
                current_side = 1 if pos_qty > 0 else -1
                max_upnl = 0.0
            max_upnl = max(max_upnl, upnl_bps)
        else:
            if in_pos:
                # Closed trade
                rpnl_delta = info.get("realized_pnl", 0.0) - stats["realized_pnl"]
                if current_side > 0: stats["pnl_long"] += rpnl_delta
                else: stats["pnl_short"] += rpnl_delta
                stats["realized_pnl"] = info.get("realized_pnl", 0.0)
                
                # MFE analytics
                if max_upnl >= 2.0: stats["reach_2_bps"] += 1
                if max_upnl >= 4.0: stats["reach_4_bps"] += 1
                if max_upnl >= 6.0: stats["reach_6_bps"] += 1
                
                # Peak logic
                # info["unrealized_pnl"] at close is usually 0, so we check last upnl_bps
                for thr in [2, 4, 6]:
                    if max_upnl >= thr and upnl_bps < thr:
                        stats["peak_reached_but_closed_below"][thr] += 1
                
                in_pos = False
                current_side = 0

        if done:
            reason = info.get("reason", "UNKNOWN")
            stats["done_reasons"][reason] = stats["done_reasons"].get(reason, 0) + 1
            if processed_steps > 30000: break
            obs = val_venv.reset()
            in_pos = False
            current_side = 0

    print(f"\n--- RESULTS FOR {branch_name} ---")
    print(f"Total Trades: {stats['trades']}")
    print(f"Realized PnL: {stats['realized_pnl']:.2f}")
    print(f"Net PnL: {stats['realized_pnl'] - stats['fees_total']:.2f}")
    print(f"Fees (Total/Entry/Exit): {stats['fees_total']:.2f} / {stats['entry_fees']:.2f} / {stats['exit_fees']:.2f}")
    print(f"Exits Maker/Taker: {stats['exit_maker']} / {stats['exit_taker']}")
    print(f"Actions: CLOSE={stats['CLOSE_count']}, REDUCE={stats['REDUCE_count']}, R2FLAT={stats['REDUCE_to_FLAT']}")
    print(f"PnL L/S: {stats['pnl_long']:.2f} / {stats['pnl_short']:.2f}")
    print(f"Quality: Reach2={stats['reach_2_bps']}, Reach4={stats['reach_4_bps']}, Reach6={stats['reach_6_bps']}")
    for thr in [2, 4, 6]:
        fail = stats['peak_reached_but_closed_below'][thr]
        reach = stats[f'reach_{thr}_bps']
        print(f" % Missed {thr}bps Capture: {fail/reach*100 if reach>0 else 0:.1f}%")
    print(f"Reasons: {stats['done_reasons']}")
    
    val_venv.close()
    del val_venv
    gc.collect()

def run_experiment_branch(branch_name, taker_penalty, maker_bonus, steps=25000):
    print(f"\n\n>>> STARTING BRANCH: {branch_name} (TakerP={taker_penalty}, MakerB={maker_bonus}) <<<")
    
    torch.cuda.empty_cache()
    gc.collect()
    
    out_dir = f"python/runs_train/abc_exit_shaping_v2/{branch_name}"
    os.makedirs(out_dir, exist_ok=True)
    
    base_model = "python/runs_train/training_v9_selective_v20b/model_v20b_final.zip"
    base_venv = "python/runs_train/training_v9_selective_v20b/venv_v20b_final.pkl"
    
    def make_train_env():
        return ActionMasker(GrpcTradingEnv(
            server_addr="localhost:50051",
            dataset_id="golden_l2_v1_train",
            symbol="BTCUSDT",
            reward_exit_taker_penalty_weight=taker_penalty,
            reward_exit_maker_bonus_weight=maker_bonus,
            **V20B_BASE_CONFIG
        ), mask_fn)
    
    # MEMORY SAFE: n_envs=2
    train_venv = DummyVecEnv([make_train_env for _ in range(2)])
    train_venv = VecNormalize.load(base_venv, train_venv)
    
    model = MaskablePPO.load(
        base_model, 
        env=train_venv, 
        device="cuda" if torch.cuda.is_available() else "cpu",
        custom_objects={"learning_rate": 5e-6}
    )
    
    model.learn(total_timesteps=steps, progress_bar=True)
    
    model_path = os.path.join(out_dir, "model.zip")
    venv_path = os.path.join(out_dir, "venv.pkl")
    model.save(model_path)
    train_venv.save(venv_path)
    
    # Detailed Audit
    audit_model(branch_name, model, venv_path, taker_penalty, maker_bonus)
    
    # Cleanup
    del model
    train_venv.close()
    del train_venv
    torch.cuda.empty_cache()
    gc.collect()
    time.sleep(5)

if __name__ == "__main__":
    # Branch A: Baseline
    run_experiment_branch("Baseline", 0.0, 0.0)
    
    # Branch B: Taker Penalty
    run_experiment_branch("TakerPenalty05", 0.5, 0.0)
    
    # Branch C: Hybrid
    run_experiment_branch("Hybrid05_02", 0.5, 0.2)
