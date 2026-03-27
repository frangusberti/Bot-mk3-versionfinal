import os
import sys
import pandas as pd
import numpy as np
from datetime import datetime

def run_snapshot_audit(raw_csv_path="python/audits/temporal_results/feature_temporal_raw.csv"):
    print(f"[AUDIT] Starting Snapshot Causal Audit on {raw_csv_path}")
    
    if not os.path.exists(raw_csv_path):
        print(f"[ERROR] Raw CSV not found: {raw_csv_path}")
        return

    df = pd.read_csv(raw_csv_path)
    
    # 1. Monotonicity Test
    df['ts_diff'] = df['ts_event'].diff()
    monotonic_fail = df[df['ts_diff'] < 0]
    
    # 2. Future Data Test (Clock Basis)
    # In EVENT_TIME_ONLY, ts_event should be the latest possible timestamp
    # We check if age metrics are ever negative
    future_data_fail = df[(df['book_age_ms'] < 0) | (df['trades_age_ms'] < 0)]
    
    # 3. Gap Analysis
    # Large jumps in ts_event (greater than decision_interval + buffer)
    decision_ms = 1000
    gaps = df[df['ts_diff'] > decision_ms * 2] # simple check for large skips

    results = {
        'total_steps': len(df),
        'monotonic_violations': len(monotonic_fail),
        'future_data_violations': len(future_data_fail),
        'large_gaps_count': len(gaps),
        'max_step_gap_ms': float(df['ts_diff'].max()) if len(df) > 1 else 0,
    }

    output_dir = os.path.dirname(raw_csv_path)
    report_path = os.path.join(output_dir, "snapshot_audit_report.md")
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("# Snapshot Causal Audit Report\n\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n\n")
        f.write("## Causal Integrity Checks\n\n")
        f.write(f"| Test | Result | Violations |\n")
        f.write(f"|------|--------|------------|\n")
        f.write(f"| Monotonic Timestamps | {'✅ PASS' if results['monotonic_violations'] == 0 else '❌ FAIL'} | {results['monotonic_violations']} |\n")
        f.write(f"| No Future Data (Age >= 0) | {'✅ PASS' if results['future_data_violations'] == 0 else '❌ FAIL'} | {results['future_data_violations']} |\n")
        f.write(f"| Continuity (Step Gap < 2s) | {'✅ PASS' if results['large_gaps_count'] == 0 else '⚠️ WARN'} | {results['large_gaps_count']} |\n\n")
        
        f.write(f"**Max Step Gap:** {results['max_step_gap_ms']} ms\n")

    print(f"[AUDIT] Snapshot audit report generated at {report_path}")

if __name__ == "__main__":
    run_snapshot_audit()
