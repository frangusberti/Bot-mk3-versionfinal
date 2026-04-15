
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

VNEXT_CONFIG = dict(
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    reward_fee_cost_weight=0.1,
    reward_as_penalty_weight=0.5,
    reward_as_horizon_ms=3000,
    reward_inventory_risk_weight=0.0005,
    reward_trailing_mfe_penalty_weight=0.02, # Branch A
    use_winner_unlock=True,
    use_selective_entry=False,
    reward_thesis_decay_weight=0.0001,
    micro_strict=False,
)

def mask_fn(env):
    return env.action_masks()

def run_deep_diagnostic(model_path, venv_path, dataset_id, steps=200000):
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
    
    trades = []
    current_trade = None
    last_realized = 0.0
    last_pos_qty = 0.0
    
    # Global counters
    stats = {
        "entry_fees": 0.0, "exit_fees": 0.0,
        "maker_fees": 0.0, "taker_fees": 0.0,
        "entry_maker": 0, "entry_taker": 0,
        "exit_maker": 0, "exit_taker": 0,
        "long_gross": 0.0, "long_fees": 0.0,
        "short_gross": 0.0, "short_fees": 0.0,
    }
    
    episode_dd_limit_reached = 0
    total_episodes = 0

    for i in range(steps):
        masks = venv.env_method("action_masks")[0]
        action, _ = model.predict(obs, deterministic=True, action_masks=masks)
        obs, reward, done, info_list = venv.step(action)
        info = info_list[0]
        
        pos_qty = info.get("position_qty", 0.0)
        equity = info.get("equity", 10000.0)
        upnl = info.get("unrealized_pnl", 0.0)
        upnl_bps = upnl / equity * 10000.0
        realized = info.get("realized_pnl", 0.0)
        fills = info.get("fills", [])
        
        # Determine if fills are entry or exit
        for f in fills:
            f_qty = f["qty"]
            f_fee = f["fee"]
            f_liq = f["liquidity"].lower()
            f_side = f["side"].upper() # BUY or SELL
            
            # Type of fee
            if "maker" in f_liq: stats["maker_fees"] += f_fee
            else: stats["taker_fees"] += f_fee
            
            # Entry vs Exit logic:
            # If pos_qty increased in magnitude or changed sign?
            # Simpler: if we had no pos, it's entry. 
            # If we had pos, and fill side same as pos side, it's entry.
            is_entry = False
            if last_pos_qty == 0:
                is_entry = True
            elif (last_pos_qty > 0 and f_side == "BUY") or (last_pos_qty < 0 and f_side == "SELL"):
                is_entry = True
            
            if is_entry:
                stats["entry_fees"] += f_fee
                if "maker" in f_liq: stats["entry_maker"] += 1
                else: stats["entry_taker"] += 1
            else:
                stats["exit_fees"] += f_fee
                if "maker" in f_liq: stats["exit_maker"] += 1
                else: stats["exit_taker"] += 1

            # Side specific fees
            # If last_pos_qty is LONG, or is_entry and BUY
            is_long = (last_pos_qty > 0) or (last_pos_qty == 0 and f_side == "BUY")
            if is_long: stats["long_fees"] += f_fee
            else: stats["short_fees"] += f_fee

        # Trade Tracking
        if abs(pos_qty) > 1e-8:
            if current_trade is None:
                current_trade = {
                    "max_upnl_bps": upnl_bps, 
                    "min_upnl_bps": upnl_bps,
                    "entry_equity": equity, 
                    "is_long": pos_qty > 0
                }
            else:
                current_trade["max_upnl_bps"] = max(current_trade["max_upnl_bps"], upnl_bps)
                current_trade["min_upnl_bps"] = min(current_trade["min_upnl_bps"], upnl_bps)
        else:
            if current_trade is not None:
                pnl_delta = realized - last_realized
                current_trade["realized_pnl"] = pnl_delta
                current_trade["realized_bps"] = pnl_delta / current_trade["entry_equity"] * 10000.0
                trades.append(current_trade)
                
                if current_trade["is_long"]: stats["long_gross"] += pnl_delta
                else: stats["short_gross"] += pnl_delta
                
                current_trade = None
        
        last_realized = realized
        last_pos_qty = pos_qty
        
        if done:
            total_episodes += 1
            if info.get("reason") == "DAILY_DD_LIMIT":
                episode_dd_limit_reached += 1
            break # Single episode or multiepisodes? 
            # In this script we'll just break after one done for brevity, 
            # but user might want multiepisode stats. 
            # Let's run until steps. 
            # obs = venv.reset() ? Wait DummyVecEnv already resets.
            # No, if we break we stop. Let's not break, just reset last_pos_qty.
            last_pos_qty = 0.0
            last_realized = 0.0

    # Diagnostics
    num_t = len(trades)
    if num_t > 0:
        avg_mfe = np.mean([t["max_upnl_bps"] for t in trades])
        avg_mae = np.mean([t["min_upnl_bps"] for t in trades])
        avg_realized_trade = np.mean([t["realized_pnl"] for t in trades])
        
        # Reach quality
        reach_stats = {
            "never_0_5": len([t for t in trades if t["max_upnl_bps"] < 0.5]) / num_t,
            "never_2": len([t for t in trades if t["max_upnl_bps"] < 2.0]) / num_t,
            "never_4": len([t for t in trades if t["max_upnl_bps"] < 4.0]) / num_t,
            "regress_0_5": len([t for t in trades if t["max_upnl_bps"] >= 0.5 and t["realized_bps"] < 0]) / num_t,
            "regress_2": len([t for t in trades if t["max_upnl_bps"] >= 2.0 and t["realized_bps"] < 0]) / num_t,
            "regress_4": len([t for t in trades if t["max_upnl_bps"] >= 4.0 and t["realized_bps"] < 0]) / num_t,
        }
    else:
        avg_mfe = avg_mae = avg_realized_trade = 0.0
        reach_stats = {k: 0.0 for k in ["never_0_5", "never_2", "never_4", "regress_0_5", "regress_2", "regress_4"]}

    print("\n" + "="*80)
    print(f" DEEP DIAGNOSTIC: Branch A (0.02) on {dataset_id}")
    print("="*80)
    
    print(f"\n1) FEES BREAKDOWN:")
    print(f"  Entry Fees:   {stats['entry_fees']:>10.4f} USDT")
    print(f"  Exit Fees:    {stats['exit_fees']:>10.4f} USDT")
    print(f"  Maker Fees:   {stats['maker_fees']:>10.4f} USDT")
    print(f"  Taker Fees:   {stats['taker_fees']:>10.4f} USDT")
    
    print(f"\n2) FILLS BREAKDOWN:")
    print(f"  Entry Maker:  {stats['entry_maker']:>10}")
    print(f"  Entry Taker:  {stats['entry_taker']:>10}")
    print(f"  Exit Maker:   {stats['exit_maker']:>10}")
    print(f"  Exit Taker:   {stats['exit_taker']:>10}")
    
    print(f"\n3) PnL BY SIDE:")
    print(f"  LONG:  Net {(stats['long_gross'] - stats['long_fees']):>10.2f} | Gross {stats['long_gross']:>10.2f} | Fees {stats['long_fees']:>10.2f}")
    print(f"  SHORT: Net {(stats['short_gross'] - stats['short_fees']):>10.2f} | Gross {stats['short_gross']:>10.2f} | Fees {stats['short_fees']:>10.2f}")
    
    print(f"\n4) TRADE QUALITY:")
    print(f"  Total Trades: {num_t}")
    print(f"  Avg Realized: {avg_realized_trade:>10.4f} USDT")
    print(f"  Avg MFE:      {avg_mfe:>10.2f} bps")
    print(f"  Avg MAE:      {avg_mae:>10.2f} bps")
    print(f"  % Never reach +0.5 bps: {reach_stats['never_0_5']:>7.1%}")
    print(f"  % Never reach +2.0 bps: {reach_stats['never_2']:>7.1%}")
    print(f"  % Never reach +4.0 bps: {reach_stats['never_4']:>7.1%}")
    print(f"  % Reach +2.0 but red:    {reach_stats['regress_2']:>7.1%}")
    print(f"  % Reach +4.0 but red:    {reach_stats['regress_4']:>7.1%}")
    
    print(f"\n5) RISK / DD:")
    print(f"  DAILY_DD Episodes: {episode_dd_limit_reached} / {total_episodes}")
    
    # Verdict
    print(f"\n6) CAUSAL VERDICT:")
    fee_drag = stats['taker_fees'] / stats['realized_pnl'] if stats['realized_pnl'] != 0 else 0
    maker_ratio = stats['exit_maker'] / (stats['exit_maker'] + stats['exit_taker']) if (stats['exit_maker'] + stats['exit_taker']) > 0 else 0
    
    if fee_drag > 2.0 or maker_ratio < 0.2:
        print("  >>> MAIN PROBLEM: EXIT EXECUTION COST (Fee Drag / Taker Abuse)")
    elif avg_mfe < 2.0 or reach_stats['never_2'] > 0.6:
        print("  >>> MAIN PROBLEM: ENTRY QUALITY (Low Alpha / Noise Capture)")
    elif abs(stats['long_gross'] - stats['short_gross']) > abs(stats['long_gross'] + stats['short_gross']) * 0.5:
        print("  >>> MAIN PROBLEM: DIRECTIONAL BIAS")
    else:
        print("  >>> MIXED / UNCLEAR")
    print("="*80 + "\n")

if __name__ == "__main__":
    m = "python/runs_train/calibration_v9/branch_A/model_A.zip"
    v = "python/runs_train/calibration_v9/branch_A/venv_A.pkl"
    run_deep_diagnostic(m, v, "golden_l2_v1_val")
