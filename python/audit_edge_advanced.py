import pandas as pd
import json
import numpy as np
import os

def run_advanced_audit(file_path="edge_audit_stream.jsonl"):
    if not os.path.exists(file_path):
        print(f"Error: {file_path} not found.")
        return

    data = []
    with open(file_path, 'r') as f:
        for line in f:
            data.append(json.loads(line))
    
    df = pd.DataFrame(data)
    if df.empty:
        print("Error: No data in audit stream.")
        return

    # Horizons
    horizons = [5, 10, 15, 30, 60]
    for h in horizons:
        df[f'ret_{h}s'] = (df['mid'].shift(-h) / df['mid'] - 1) * 10000

    df = df.dropna()

    results = []

    def analyze_subset(name, mask):
        subset = df[mask]
        count = len(subset)
        if count < 10: return None
        
        row = {"Subset": name, "Samples": count}
        for h in horizons:
            ret_col = f'ret_{h}s'
            mean_ret = subset[ret_col].mean()
            # For Short side (imb < 0), we care about negative returns
            # So we use absolute mean for the "Edge" metric
            edge = abs(mean_ret)
            beat_4bps = "YES" if edge > 4.0 else "NO"
            row[f'Mean_{h}s'] = mean_ret
            row[f'Beat4_{h}s'] = beat_4bps
        return row

    # 1. Global
    results.append(analyze_subset("Global", [True] * len(df)))

    # 2. Imbalance Tails
    results.append(analyze_subset("IMB > 0.9", df['imb'] > 0.9))
    results.append(analyze_subset("IMB < -0.9", df['imb'] < -0.9))
    results.append(analyze_subset("IMB > 0.99", df['imb'] > 0.99))
    results.append(analyze_subset("IMB < -0.99", df['imb'] < -0.99))

    # 3. Combinations (IMB + MP)
    results.append(analyze_subset("IMB > 0.9 & MP > 0.5", (df['imb'] > 0.9) & (df['mp'] > 0.5)))
    results.append(analyze_subset("IMB < -0.9 & MP < -0.5", (df['imb'] < -0.9) & (df['mp'] < -0.5)))

    # 4. Combinations (IMB + Spread)
    # Low spread = tighter market, maybe more signal?
    median_sp = df['sp'].median()
    results.append(analyze_subset("IMB > 0.9 & LowSp", (df['imb'] > 0.9) & (df['sp'] <= median_sp)))
    results.append(analyze_subset("IMB < -0.9 & LowSp", (df['imb'] < -0.9) & (df['sp'] <= median_sp)))

    # 5. Regimes (if available and not just placeholders)
    if 'r_trend' in df.columns:
        results.append(analyze_subset("Regime Trend (>0.6)", df['r_trend'] > 0.6))
        results.append(analyze_subset("Regime Range (>0.6)", df['r_range'] > 0.6))
        results.append(analyze_subset("Regime Shock (>0.6)", df['r_shock'] > 0.6))
        results.append(analyze_subset("Regime Dead (>0.6)", df['r_dead'] > 0.6))

    # Filter out None
    results = [r for r in results if r is not None]
    res_df = pd.DataFrame(results)
    
    print("\n--- ADVANCED EDGE AUDIT RESULTS (bps) ---")
    # Pretty print with selected columns to avoid clutter
    display_cols = ["Subset", "Samples"]
    for h in horizons:
        display_cols.append(f'Mean_{h}s')
        display_cols.append(f'Beat4_{h}s')
    
    print(res_df[display_cols].to_string(index=False))

    # Detailed Corridors for Veredicto
    print("\n--- SIGNAL CORRELATIONS (Spearman) ---")
    for h in horizons:
        corr_mp = df['mp'].corr(df[f'ret_{h}s'], method='spearman')
        corr_imb = df['imb'].corr(df[f'ret_{h}s'], method='spearman')
        print(f"Horizon {h}s: MP Corr={corr_mp:.4f}, IMB Corr={corr_imb:.4f}")

if __name__ == "__main__":
    run_advanced_audit()
