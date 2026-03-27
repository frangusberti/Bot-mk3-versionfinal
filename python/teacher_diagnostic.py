
import pandas as pd
import numpy as np
import os
import sys

# Add python dir to path
sys.path.insert(0, os.path.dirname(__file__))

from teacher_policy import TEACHER_PARAMS, compute_scores, obs_vec_to_feature_dict

def diagnose_dataset(parquet_path):
    print(f"--- TEACHER V1 DIAGNOSITIC: {os.path.basename(parquet_path)} ---")
    df = pd.read_parquet(parquet_path)
    print(f"Total Rows: {len(df)}")
    
    # Extract obs vectors
    obs_matrix = np.stack(df['obs_vec'].values)
    
    # We want to use the compute_scores logic on these vectors
    all_scores = []
    all_features = []
    
    print("Re-evaluating Teacher scores...")
    for obs in obs_matrix[:10000]: # Sample 10k rows for speed
        f_dict = obs_vec_to_feature_dict(obs)
        scores = compute_scores(f_dict, TEACHER_PARAMS)
        all_features.append(f_dict)
        all_scores.append(scores)
        
    sdf = pd.DataFrame(all_scores)
    fdf = pd.DataFrame(all_features)
    
    print("\n--- Feature Distributions (Sample Mean/Std) ---")
    cols_to_check = ["spread_bps", "obi_top1", "regime_dead", "rv_5s", "regime_range", "regime_shock", "depth_imbalance_top5"]
    for c in cols_to_check:
        if c in fdf.columns:
            # Handle None/NaN
            vals = fdf[c].dropna().astype(float)
            if len(vals) > 0:
                print(f"  {c:20}: {vals.mean():8.4f} (std {vals.std():8.4f}, max {vals.max():8.4f})")
    
    print("\n--- Score Distributions ---")
    for c in sdf.columns:
        print(f"  {c:20}: {sdf[c].mean():8.4f} (std {sdf[c].std():8.4f}, max {sdf[c].max():8.4f})")
        
    print("\n--- Bottleneck Analysis ---")
    no_trade_pass = (sdf['no_trade'] >= TEACHER_PARAMS.no_trade_threshold).sum()
    print(f"  No Trade Gate Triggered: {no_trade_pass/len(sdf)*100:6.2f}% of rows")
    
    avg_bid = sdf['bid'].mean()
    avg_ask = sdf['ask'].mean()
    print(f"  Avg Bid Score: {avg_bid:.4f} (Target 0.55)")
    print(f"  Avg Ask Score: {avg_ask:.4f} (Target 0.55)")
    
    # Check why bid/ask is low
    # Recall bid/ask base components
    print("\n--- Signal Strength Check ---")
    if len(fdf) > 0:
        obi_strength = (fdf['obi_top1'].abs() + fdf['obi_top3'].abs()).mean()
        print(f"  Average OBI Strength (|top1|+|top3|): {obi_strength:.4f}")
        
    # Check if spread is too narrow or too wide
    narrow_spread = (fdf['spread_bps'] < TEACHER_PARAMS.min_spread_bps).sum()
    wide_spread = (fdf['spread_bps'] > TEACHER_PARAMS.max_spread_bps).sum()
    print(f"  Spread too narrow (<{TEACHER_PARAMS.min_spread_bps}bps): {narrow_spread/len(fdf)*100:6.2f}%")
    print(f"  Spread too wide   (>{TEACHER_PARAMS.max_spread_bps}bps): {wide_spread/len(fdf)*100:6.2f}%")

if __name__ == "__main__":
    import glob
    files = glob.glob("python/runs_train/bc_datasets/*.parquet")
    if files:
        # Sort by date
        files.sort()
        latest = files[-1]
        diagnose_dataset(latest)
    else:
        print("No BC datasets found in python/runs_train/bc_datasets/")
