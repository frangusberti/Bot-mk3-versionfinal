import os
import sys
import numpy as np
import pandas as pd
from tqdm import tqdm

# Ensure paths are correct for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv

def policy_v1(obs, position, holding_s, info=None):
    # Signals from observation (S2 Schema)
    mid = obs[0]
    spread_bps = obs[2]
    buy_v_5s = obs[16]
    sell_v_5s = obs[17]
    imb_5s = obs[21]
    pnl_pct = obs[49]
    
    action = 0
    if position != 0:
        pnl_bps = pnl_pct * 10000.0
        if pnl_bps >= 4.0 or pnl_bps <= -3.0 or holding_s >= 20:
            action = 6 # TAKER_EXIT
    else:
        # Permissive flow-based entry
        if imb_5s > 0.6 and buy_v_5s > 0.5 and spread_bps < 3.0:
            action = 1 # POST_BID (Long)
        elif imb_5s < -0.6 and sell_v_5s > 0.5 and spread_bps < 3.0:
            action = 3 # POST_ASK (Short)
    return action

def policy_v2(obs, position, holding_s, info=None, debug=False):
    # Signals from observation (S2 Schema v6)
    mid = obs[0]
    spread_bps = obs[2]
    ret_1s = obs[4]
    ret_5s = obs[6]
    buy_v_5s = obs[16]
    sell_v_5s = obs[17]
    imb_1s = obs[20]
    imb_5s = obs[21]
    microprice = obs[27]
    pnl_pct = obs[49] # latent_pnl_pct
    
    # Feature Health (from info)
    health = info.get("feature_health", {}) if info else {}
    obs_quality = health.get("obs_quality", 1.0)
    book_age = health.get("book_age_ms", 0)
    
    eps = 1e-9
    action = 0
    
    # Missing Microprice Fallback (if masked to 0.0, treat as mid)
    eff_micro = microprice if microprice > 0 else mid
    
    # Global Filters
    if obs_quality < 0.99 or book_age > 50 or spread_bps >= 3.0:
        if position != 0:
            return 6 # TAKER_EXIT (Safe out)
        return 0
    
    if position != 0:
        pnl_bps = pnl_pct * 10000.0
        
        # EXIT RULES V2
        if pnl_bps >= 5.0: return 6
        if pnl_bps <= -3.0: return 6
        if holding_s >= 12: return 6
        if holding_s > 8 and abs(pnl_bps) < 1.0: return 6
        
        if position > 0: # Long
            if imb_1s < -0.05 or ret_1s < 0 or eff_micro < mid:
                return 6
        else: # Short
            if imb_1s > 0.05 or ret_1s > 0 or eff_micro > mid:
                return 6
    else:
        # ENTRY RULES V2
        flow_ratio = buy_v_5s / max(sell_v_5s, eps)
        
        # LONG ENTRY
        if (imb_5s > 0.18 and 
            flow_ratio > 1.35 and 
            ret_1s > 0 and 
            ret_5s > 0 and 
            eff_micro >= mid):
            return 1 # POST_BID
            
        # SHORT ENTRY
        flow_ratio_s = sell_v_5s / max(buy_v_5s, eps)
        if (imb_5s < -0.18 and 
            flow_ratio_s > 1.35 and 
            ret_1s < 0 and 
            ret_5s < 0 and 
            eff_micro <= mid):
            return 3 # POST_ASK
            
    return action

def run_policy_eval(name, policy_fn, steps=2000, debug=False):
    env = GrpcTradingEnv(
        server_addr="localhost:50051",
        dataset_id="stage2_eval",
        symbol="BTCUSDT",
        seed=42, # Stable for both
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
        fill_model=2, # Optimistic
        random_start_offset=False
    )
    
    obs, info = env.reset()
    position = 0
    holding_s = 0
    prev_realized_pnl = 0.0
    
    stats = {
        "trades": 0,
        "gross_pnl": 0.0,
        "fees": 0.0,
        "net_pnl": 0.0,
        "wins": 0,
        "holds": []
    }
    
    print(f"[*] Evaluating {name}...")
    for i in tqdm(range(steps)):
        # Normalize obs for policy (some are raw, some clamped)
        if "policy_v2" in str(policy_fn):
            action = policy_fn(obs, position, holding_s, info, debug=debug)
        else:
            action = policy_fn(obs, position, holding_s, info)
        
        obs, reward, terminated, truncated, info = env.step(action)
        
        # Track realized pnl change
        curr_realized = info.get("realized_pnl", 0.0)
        curr_fees = info.get("fees_paid", 0.0)
        curr_pos_side = info.get("position_side", "FLAT")
        curr_pos_qty = abs(info.get("position_qty", 0.0))
        
        # A trade is "finished" when position returns to FLAT or realized pnl changes
        if curr_realized != prev_realized_pnl:
            trade_net = curr_realized - prev_realized_pnl
            stats["net_pnl"] += trade_net
            stats["trades"] += 1
            if trade_net > 0: stats["wins"] += 1
            prev_realized_pnl = curr_realized
            
        if curr_pos_qty > 1e-9 and position == 0:
            holding_s = 0
            position = 1 if curr_pos_side == "LONG" else -1
        elif curr_pos_qty > 1e-9:
            holding_s += 1
        else:
            if position != 0:
                stats["holds"].append(holding_s)
            holding_s = 0
            position = 0
            
        if terminated or truncated:
            obs, info = env.reset()
            position = 0
            holding_s = 0
            prev_realized_pnl = 0.0
            
    stats["fees"] = info.get("fees_paid", 0.0)
    stats["gross_pnl"] = stats["net_pnl"] + stats["fees"]
    
    return {
        "Policy": name,
        "Trades": stats["trades"],
        "Win Rate": f"{(stats['wins']/stats['trades']*100 if stats['trades']>0 else 0):.1f}%",
        "Avg PnL": f"{(stats['net_pnl']/stats['trades'] if stats['trades']>0 else 0):.4f}",
        "Gross PF": f"{(stats['gross_pnl']/stats['fees'] if stats['fees']>0 else 0):.2f}",
        "Avg Hold": f"{(np.mean(stats['holds']) if stats['holds'] else 0):.1f}s",
        "Net PnL": f"{stats['net_pnl']:.2f}"
    }

if __name__ == "__main__":
    v1_res = run_policy_eval("Teacher V1", policy_v1)
    v2_res = run_policy_eval("Teacher V2", policy_v2, debug=True)
    
    df = pd.DataFrame([v1_res, v2_res])
    print("\n--- ABLATION COMPARISON ---")
    print(df.to_string(index=False))
    df.to_csv("teacher_ablation_results.csv", index=False)
