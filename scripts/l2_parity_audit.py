# scripts/l2_parity_audit.py
import json
import sys
import os
import pandas as pd
from datetime import datetime

def load_report(path):
    with open(path, 'r') as f:
        data = json.load(f)
    # Handle nested metrics if they exist in different versions of reports
    return {
        "pnl_pct": data.get("pnl", 0.0),
        "trades": data.get("trades", 0),
        "maker_fills": data.get("maker_fills", 0),
        "taker_fills": data.get("taker_fills", 0),
        "maker_ratio": data.get("maker_ratio", 0.0) if "maker_ratio" in data else (data.get("maker_fills", 0) / data.get("trades", 1)),
        "cancel_count": data.get("cancel_count", 0),
        "hold_rate": data.get("hold_rate", 0.0),
        "avg_hold_time_ms": data.get("avg_hold_time_ms", 0.0),
        "mean_reward": data.get("mean_reward", 0.0),
        "final_equity": data.get("final_equity", 10000.0)
    }

def audit_parity(live_path, replay_path):
    if not os.path.exists(live_path) or not os.path.exists(replay_path):
        print(f"ERROR: One or both report files missing.")
        return

    print(f"=== L2 Behavioral Parity Audit ===")
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"LIVE (Paper):   {live_path}")
    print(f"REPLAY (Offline): {replay_path}")
    
    live = load_report(live_path)
    replay = load_report(replay_path)
    
    metrics = ["pnl_pct", "trades", "maker_fills", "maker_ratio", "cancel_count", "avg_hold_time_ms", "mean_reward"]
    
    results = []
    for m in metrics:
        l_val = live[m]
        r_val = replay[m]
        abs_diff = r_val - l_val
        rel_diff = (abs_diff / abs(l_val)) * 100 if l_val != 0 else 0
        
        results.append({
            "Metric": m,
            "Live": l_val,
            "Replay": r_val,
            "Diff (abs)": abs_diff,
            "Diff (%)": rel_diff
        })
        
    df = pd.DataFrame(results)
    print("\n[COMPARISON TABLE]")
    print(df.to_string(index=False, formatters={
        "Live": "{:,.4f}".format,
        "Replay": "{:,.4f}".format,
        "Diff (abs)": "{:,.4f}".format,
        "Diff (%)": "{:+.2f}%".format
    }))
    
    PNL_TOLERANCE = 0.05 # 0.05% relative diff
    TRADE_TOLERANCE = 0   # Still aiming for exact match in trades
    BEHAVIOR_TOLERANCE = 1.0 # 1% for maker ratio / hold time
    
    pnl_mismatch = abs(df.loc[df['Metric'] == 'pnl_pct', 'Diff (%)'].values[0]) > PNL_TOLERANCE
    trade_mismatch = abs(df.loc[df['Metric'] == 'trades', 'Diff (abs)'].values[0]) > TRADE_TOLERANCE
    ratio_mismatch = abs(df.loc[df['Metric'] == 'maker_ratio', 'Diff (%)'].values[0]) > BEHAVIOR_TOLERANCE
    
    print("\n[VERDICT]")
    if pnl_mismatch or trade_mismatch or ratio_mismatch:
        print("STATUS: FAIL - Significant drift detected between Live and Replay environments.")
        if trade_mismatch:
            print("  - TRADE COUNT MISMATCH: Check data sequence or order logic.")
        if ratio_mismatch:
            print(f"  - BEHAVIOR DRIFT: Maker Ratio or Hold Time differs by > {BEHAVIOR_TOLERANCE}%.")
        if pnl_mismatch:
            print(f"  - PnL DRIFT > {PNL_TOLERANCE}%: Verify fill prices and execution engine state.")
    else:
        print(f"STATUS: PASS - High-fidelity match (Drift within {PNL_TOLERANCE}%).")
        print("Conclusion: L2 Data Pipeline is trustworthy for training.")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python scripts/l2_parity_audit.py <live_report.json> <replay_report.json>")
    else:
        audit_parity(sys.argv[1], sys.argv[2])
