"""
Causal Audit v2: REDUCE fix verification & Causal Entry Analysis
=================================================================
Verifies that REDUCE can now flatten positions when near minimums.
Audits the feature state exactly at the timestamp of bad entries.
"""
import os
import sys
import json
import torch
import numpy as np
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from sb3_contrib.common.wrappers import ActionMasker

CONFIG = dict(
    dataset_id="golden_l2_v1_train",
    symbol="BTCUSDT",
    random_start_offset=True,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    fill_model=2,
)

EDGE_AUDIT_FILE = "C:\\Bot mk3\\edge_audit_stream.jsonl"

def mask_fn(env: GrpcTradingEnv) -> np.ndarray:
    return env.action_masks()

def run_causal_audit_v2(model, env, steps=15000):
    obs = env.reset()
    
    current_trade = None
    all_trades = []
    
    reduce_to_flat_count = 0
    reduce_with_residue_count = 0
    close_with_pos = 0
    
    last_fees_paid = 0.0

    # For the short scorecard
    total_episodes = 0
    episodes_flat_at_done = 0

    for i in range(steps):
        masks = env.env_method("action_masks")
        action, _ = model.predict(obs, deterministic=True, action_masks=np.array(masks))
        act_int = int(action[0])
        
        pos_qty_before = env.get_attr("unwrapped")[0]._last_info.get("position_qty", 0.0) if hasattr(env.get_attr("unwrapped")[0], '_last_info') else 0.0
        
        ts_before = env.get_attr("unwrapped")[0]._last_info.get("ts", 0) if hasattr(env.get_attr("unwrapped")[0], '_last_info') else 0
        
        obs, reward, done, info = env.step(action)
        info0 = info[0]
        env.get_attr("unwrapped")[0]._last_info = info0
        
        mid = info0['mid_price']
        pos_qty = info0['position_qty'] # signed
        entry_price = info0['entry_price']
        last_fees_paid = info0.get("fees_paid", last_fees_paid)
        
        if act_int in {4, 8} and abs(pos_qty_before) > 1e-9:
            close_with_pos += 1
            
        if act_int in {3, 7}:
            if abs(pos_qty) < 1e-9:
                 reduce_to_flat_count += 1
            else:
                 reduce_with_residue_count += 1

        # -- Track Trade Lifecycle --
        if abs(pos_qty) > 1e-9:
            if current_trade is None:
                current_trade = {
                    "side": "LONG" if pos_qty > 0 else "SHORT",
                    "entry_price": entry_price,
                    "mfe_bps": 0.0,
                    "mae_bps": 0.0,
                    "ts_start": ts_before  # Use the timestamp BEFORE the action took place
                }
            
            if current_trade["side"] == "LONG":
                pnl_bps = (mid - current_trade["entry_price"]) / current_trade["entry_price"] * 10000.0
            else:
                pnl_bps = (current_trade["entry_price"] - mid) / current_trade["entry_price"] * 10000.0
            
            current_trade["mfe_bps"] = max(current_trade["mfe_bps"], pnl_bps)
            current_trade["mae_bps"] = min(current_trade["mae_bps"], pnl_bps)
            
        else:
            if current_trade is not None:
                all_trades.append(current_trade)
                current_trade = None

        if done:
            total_episodes += 1
            if abs(pos_qty) < 1e-9:
                episodes_flat_at_done += 1
    
    if current_trade:
        all_trades.append(current_trade)
        
    return all_trades, {
        "reduce_to_flat_count": reduce_to_flat_count,
        "reduce_with_residue_count": reduce_with_residue_count,
        "close_with_pos": close_with_pos,
        "episodes_flat_at_done": episodes_flat_at_done,
        "total_episodes": total_episodes,
        "fees_total": last_fees_paid
    }

def process_edge_data(all_trades):
    # Parse edge stream
    edge_map = {}
    if os.path.exists(EDGE_AUDIT_FILE):
        with open(EDGE_AUDIT_FILE, 'r') as f:
            for line in f:
                try:
                    d = json.loads(line)
                    # Maps entry timestamp to feature state.
                    # Due to possible slight mismatches, we can tolerate minor drift,
                    # but usually it's exact since the action occurs on the same TS.
                    # Wait, we log BEFORE action logic in rl.rs, so TS maps perfectly. 
                    # If it's OPEN_LONG (1) or OPEN_SHORT (5)
                    act = d.get('act_idx', 0)
                    if act in {1, 2, 5, 6}: # OPEN / ADD
                         edge_map[d['t']] = d
                except:
                    continue

    edge_times = sorted(edge_map.keys())
    
    def find_closest_ts(target_ts):
        if not edge_times: return None
        idx = np.searchsorted(edge_times, target_ts)
        if idx == 0: return edge_times[0]
        if idx == len(edge_times): return edge_times[-1]
        before = edge_times[idx - 1]
        after = edge_times[idx]
        return before if (target_ts - before) < (after - target_ts) else after

    bad_trades = [t for t in all_trades if t['mfe_bps'] < 0.5]
    good_trades = [t for t in all_trades if t['mfe_bps'] >= 0.5]

    bad_features = defaultdict(list)
    good_features = defaultdict(list)
    
    found_bad = 0
    found_good = 0
    
    # We tolerate max 5 seconds diff
    TOLERANCE = 5000 
    
    for t in bad_trades:
        cts = find_closest_ts(t['ts_start'])
        if cts and abs(cts - t['ts_start']) < TOLERANCE:
            em = edge_map[cts]
            found_bad += 1
            for k, v in em.items():
                if k not in {'t', 'act_idx', 'mid'}:
                    bad_features[k].append(v)
                    
    for t in good_trades:
        cts = find_closest_ts(t['ts_start'])
        if cts and abs(cts - t['ts_start']) < TOLERANCE:
            em = edge_map[cts]
            found_good += 1
            for k, v in em.items():
                if k not in {'t', 'act_idx', 'mid'}:
                    good_features[k].append(v)

    return bad_features, good_features, len(bad_trades), found_bad, len(good_trades), found_good

def main():
    model_path = "python/runs_train/itr_serious_50k/model_itr_serious.zip"
    venv_path = "python/runs_train/itr_serious_50k/venv_itr_serious.pkl"
    
    # Reset audit stream
    open(EDGE_AUDIT_FILE, 'w').close()
    
    model = MaskablePPO.load(model_path)
    
    def make_env():
        env = GrpcTradingEnv(server_addr="localhost:50051", **CONFIG)
        return ActionMasker(env, mask_fn)

    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(venv_path, venv)
    venv.training = False
    
    print("[CAUSAL AUDIT V2] Running 15000 steps with Zeno Fix...")
    trades, stats = run_causal_audit_v2(model, venv, steps=15000)
    
    print("\n" + "="*60)
    print("      CAUSAL AUDIT: REDUCE FIX SCORECARD")
    print("="*60)
    ep_pct = (stats['episodes_flat_at_done'] / max(stats['total_episodes'], 1)) * 100
    print(f"Total Trades Analyzed:     {len(trades)}")
    print(f"% Trades Flat at Done:     {ep_pct:.2f}%")
    print(f"REDUCE leading to FLAT:    {stats['reduce_to_flat_count']}")
    print(f"REDUCE with residue:       {stats['reduce_with_residue_count']}")
    print(f"CLOSE_WITH_POS:            {stats['close_with_pos']}")
    print(f"Total Fees:                {stats['fees_total']:.4f}")
    
    print("\n[CAUSAL AUDIT V2] Analyzing underlying features...")
    b_feat, g_feat, total_bad, found_bad, total_good, found_good = process_edge_data(trades)
    
    print("\n" + "="*60)
    print(f"     CAUSAL ENTRY PATTERN AUDIT (Bad Trades: <0.5 bps MFE)")
    print("="*60)
    print(f"Mapped {found_bad}/{total_bad} BAD trades to their entry state.")
    print(f"Mapped {found_good}/{total_good} GOOD trades to their entry state.\n")
    
    if found_bad > 0:
        print("AVERAGE FEATURE STATE AT ENTRY (BAD vs GOOD):")
        print(f"{'Feature':<20} | {'BAD (Toxic)':<15} | {'GOOD (Profit)':<15}")
        print("-" * 55)
        for k in sorted(b_feat.keys()):
            mean_b = np.mean(b_feat[k])
            mean_g = np.mean(g_feat[k]) if k in g_feat and len(g_feat[k]) > 0 else 0.0
            print(f"{k:<20} | {mean_b:>15.4f} | {mean_g:>15.4f}")
    else:
        print("No edge telemetry found for Bad trades. Did the stream log correctly?")

if __name__ == "__main__":
    main()
