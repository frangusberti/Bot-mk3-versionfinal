import pandas as pd
import json
import numpy as np

def run_audit():
    data = []
    with open("C:\\Bot mk3\\edge_audit_stream.jsonl", "r") as f:
        for line in f:
            data.append(json.loads(line))
            
    df = pd.DataFrame(data)
    if df.empty:
        print("No data collected.")
        return

    # Calculate forward returns (bps)
    # Each step is ~1s because decision_interval_ms=1000
    df['ret_1s'] = (df['mid'].shift(-1) / df['mid'] - 1) * 10000
    df['ret_3s'] = (df['mid'].shift(-3) / df['mid'] - 1) * 10000
    df['ret_5s'] = (df['mid'].shift(-5) / df['mid'] - 1) * 10000
    df['ret_10s'] = (df['mid'].shift(-10) / df['mid'] - 1) * 10000
    df['ret_30s'] = (df['mid'].shift(-30) / df['mid'] - 1) * 10000
    df['ret_60s'] = (df['mid'].shift(-60) / df['mid'] - 1) * 10000

    features = ['mp', 'imb', 'rv', 'sp']
    targets = ['ret_1s', 'ret_5s', 'ret_10s', 'ret_30s', 'ret_60s']

    print("--- FEATURE CORRELATIONS (Spearman) ---")
    corr = df[features + targets].corr(method='spearman')
    print(corr.loc[features, targets])
    print("\n")

    print("--- CONDITIONAL EDGE ANALYSIS ---")
    for h in ['ret_5s', 'ret_10s', 'ret_30s', 'ret_60s']:
        print(f"\nHorizon: {h}")
        bull_mp = df[df['mp'] > 0.5][h].mean()
        bear_mp = df[df['mp'] < -0.5][h].mean()
        print(f"  Avg Ret when MP > 0.5 bps: {bull_mp:.2f} bps")
        print(f"  Avg Ret when MP < -0.5 bps: {bear_mp:.2f} bps")

        bull_imb = df[df['imb'] > 0.8][h].mean()
        bear_imb = df[df['imb'] < -0.8][h].mean()
        print(f"  Avg Ret when IMB > 0.8: {bull_imb:.2f} bps")
        print(f"  Avg Ret when IMB < -0.8: {bear_imb:.2f} bps")

        # Selective entry audit: return when signals align
        aligned_long = df[(df['mp'] > 0.2) & (df['imb'] > 0.5)]
        aligned_short = df[(df['mp'] < -0.2) & (df['imb'] < -0.5)]
        print(f"  Aligned LONG edge: {aligned_long[h].mean():.2f} bps ({len(aligned_long)} samples)")
        print(f"  Aligned SHORT edge: {aligned_short[h].mean():.2f} bps ({len(aligned_short)} samples)")

        # Probability of beating 4 bps roundtrip cost
        success = (df[h].abs() > 4.0).mean()
        print(f"  Prob(|Ret| > 4 bps): {success:.1%}")

if __name__ == "__main__":
    run_audit()
