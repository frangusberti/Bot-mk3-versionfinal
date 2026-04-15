"""
Training Continuation: ITR Architecture
=======================================
Resumes training for 100k steps, evaluating at +25k, +50k, +100k checkpoints.
Focuses on tracking causal entry metrics during evaluation to see if the agent 
learns to enter on dips (reversion) rather than at momentum exhaustion.
"""
import os
import sys
import json
import torch
import numpy as np
from collections import defaultdict
from sb3_contrib import MaskablePPO
from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
from stable_baselines3.common.callbacks import BaseCallback

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))
from grpc_env import GrpcTradingEnv
from sb3_contrib.common.wrappers import ActionMasker

CONFIG = dict(
    dataset_id="golden_l2_v1_train",
    symbol="BTCUSDT",
    random_start_offset=True,
    max_daily_dd=0.05,
    profit_floor_bps=0.5,
    stop_loss_bps=30.0,
    fill_model=2,
)

EDGE_AUDIT_FILE = "C:\\Bot mk3\\edge_audit_stream.jsonl"
OUT_DIR = "python/runs_train/itr_causal_chk"
os.makedirs(OUT_DIR, exist_ok=True)

def mask_fn(env: GrpcTradingEnv) -> np.ndarray:
    return env.action_masks()

def run_causal_eval(model, env, steps=15000):
    open(EDGE_AUDIT_FILE, 'w').close() # reset stream
    env.training = False
    obs = env.reset()
    
    current_trade = None
    all_trades = []
    
    close_with_pos = 0
    last_realized_pnl = 0.0
    last_fees_paid = 0.0

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
        pos_qty = info0['position_qty']
        entry_price = info0['entry_price']
        
        last_realized_pnl = info0.get("realized_pnl", last_realized_pnl)
        last_fees_paid = info0.get("fees_paid", last_fees_paid)
        
        if act_int in {4, 8} and abs(pos_qty_before) > 1e-9:
            close_with_pos += 1

        if abs(pos_qty) > 1e-9:
            if current_trade is None:
                current_trade = {
                    "side": "LONG" if pos_qty > 0 else "SHORT",
                    "entry_price": entry_price,
                    "mfe_bps": 0.0,
                    "mae_bps": 0.0,
                    "ts_start": ts_before
                }
            
            pnl_bps = (mid - current_trade["entry_price"]) / current_trade["entry_price"] * 10000.0 if current_trade["side"] == "LONG" else (current_trade["entry_price"] - mid) / current_trade["entry_price"] * 10000.0
            current_trade["mfe_bps"] = max(current_trade["mfe_bps"], pnl_bps)
            current_trade["mae_bps"] = min(current_trade["mae_bps"], pnl_bps)
            
        else:
            if current_trade is not None:
                all_trades.append(current_trade)
                current_trade = None

        if done and current_trade:
            all_trades.append(current_trade)
            current_trade = None
            
    if current_trade:
        all_trades.append(current_trade)
        
    # Analyze Edge Stream
    edge_map = {}
    if os.path.exists(EDGE_AUDIT_FILE):
        with open(EDGE_AUDIT_FILE, 'r') as f:
            for line in f:
                try:
                    d = json.loads(line)
                    act = d.get('act_idx', 0)
                    if act in {1, 2, 5, 6}: 
                        edge_map[d['t']] = d
                except: continue

    edge_times = sorted(edge_map.keys())
    def find_closest_ts(target_ts):
        if not edge_times: return None
        idx = np.searchsorted(edge_times, target_ts)
        if idx == 0: return edge_times[0]
        if idx == len(edge_times): return edge_times[-1]
        b = edge_times[idx - 1]
        a = edge_times[idx]
        return b if (target_ts - b) < (a - target_ts) else a

    bad_trades = [t for t in all_trades if t['mfe_bps'] < 0.5]
    good_trades = [t for t in all_trades if t['mfe_bps'] >= 0.5]
    
    bad_features = defaultdict(list)
    good_features = defaultdict(list)
    
    for t in bad_trades:
        cts = find_closest_ts(t['ts_start'])
        if cts and abs(cts - t['ts_start']) < 5000:
            em = edge_map[cts]
            for k, v in em.items():
                if k in {'bb_1m', 'mp', 'rsi_1m', 'slope_5m'}: bad_features[k].append(v)
                    
    for t in good_trades:
        cts = find_closest_ts(t['ts_start'])
        if cts and abs(cts - t['ts_start']) < 5000:
            em = edge_map[cts]
            for k, v in em.items():
                if k in {'bb_1m', 'mp', 'rsi_1m', 'slope_5m'}: good_features[k].append(v)

    if not all_trades: return "No trades executed."
    
    num_t = len(all_trades)
    reach_05 = len(good_trades)
    reach_20 = sum(1 for t in all_trades if t['mfe_bps'] >= 2.0)
    avg_mfe = np.mean([t['mfe_bps'] for t in all_trades])
    avg_mae = np.mean([t['mae_bps'] for t in all_trades])
    
    out = [
        f"A) CALIDAD DE ENTRADA (N={num_t})",
        f"   Avg MFE:            {avg_mfe:.4f} bps",
        f"   Avg MAE:            {avg_mae:.4f} bps",
        f"   Reach +0.5 bps:     {reach_05 / num_t * 100:.1f}%",
        f"   Reach +2.0 bps:     {reach_20 / num_t * 100:.1f}%",
        "",
        "B) SEÑALES AL ENTRAR (BAD vs GOOD)",
        f"   Mapped {len(bad_features['bb_1m'])} BAD vs {len(good_features['bb_1m'])} GOOD",
        f"   {'Feature':<15} | {'BAD (Toxic)':<15} | {'GOOD (Profit)'}"
    ]
    
    for k in sorted({'bb_1m', 'mp', 'rsi_1m', 'slope_5m'}):
        b_mean = np.mean(bad_features[k]) if bad_features[k] else 0.0
        g_mean = np.mean(good_features[k]) if good_features[k] else 0.0
        out.append(f"   {k:<15} | {b_mean:>15.4f} | {g_mean:>15.4f}")
        
    out.extend([
        "",
        "C) ECONOMIA",
        f"   Net PnL:           {last_realized_pnl - last_fees_paid:.4f}",
        f"   Realized PnL:      {last_realized_pnl:.4f}",
        f"   Total Fees:        {last_fees_paid:.4f}",
        f"   CLOSE_WITH_POS:    {close_with_pos}"
    ])
    
    env.training = True
    return "\n".join(out)

def main():
    base_model_path = "python/runs_train/itr_serious_50k/model_itr_serious.zip"
    base_venv_path = "python/runs_train/itr_serious_50k/venv_itr_serious.pkl"
    
    def make_env():
        env = GrpcTradingEnv(server_addr="localhost:50051", **CONFIG)
        return ActionMasker(env, mask_fn)

    venv = DummyVecEnv([make_env])
    venv = VecNormalize.load(base_venv_path, venv)
    venv.training = True
    
    model = MaskablePPO.load(base_model_path, env=venv)
    
    # Checkpoints
    steps_per_block = 25000
    blocks = [25_000, 50_000, 75_000, 100_000] # We will do 4 blocks for 100k total
    
    print("[TRAINING] Starting 100k Continuation Curriculum...")
    
    current_total = 0
    for target in blocks:
        diff = target - current_total
        print(f"\n>>>> TRAINING PHASE: +{diff} steps (Targeting {target}k total this run)...")
        model.learn(total_timesteps=diff)
        current_total += diff
        
        model_path = f"{OUT_DIR}/model_chk_{target}"
        model.save(model_path)
        venv.save(f"{OUT_DIR}/venv_chk_{target}.pkl")
        
        if target in {25000, 50000, 100000}:
            print(f"\n=================================================")
            print(f"      CHECKPOINT SCORECARD: +{target//1000}k STEPS")
            print(f"=================================================")
            report = run_causal_eval(model, venv, steps=10000)
            print(report)

if __name__ == "__main__":
    main()
