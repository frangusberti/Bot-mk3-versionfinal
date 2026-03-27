# scripts/l2_integrity_audit.py
import pandas as pd
import numpy as np
import sys
import os
from datetime import datetime

def audit_dataset(parquet_path):
    if not os.path.exists(parquet_path):
        print(f"ERROR: File not found: {parquet_path}")
        return

    print(f"=== L2 Integrity Audit ===")
    print(f"File: {parquet_path}")
    
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"FAILED to read parquet: {e}")
        return

    # 1. Time Scope
    start_ts = df['exchange_timestamp'].min()
    end_ts = df['exchange_timestamp'].max()
    duration_hrs = (end_ts - start_ts) / 3600000.0
    
    print(f"Start: {datetime.fromtimestamp(start_ts/1000.0)}")
    print(f"End:   {datetime.fromtimestamp(end_ts/1000.0)}")
    print(f"Duration: {duration_hrs:.2f} hours")
    
    # 2. Stream Statistics
    sc = df['event_type'].value_counts()
    print("\nStream Counts:")
    for stream, count in sc.items():
        print(f"  - {stream:12}: {count:,}")

    # 3. Sequence Integrity (depthUpdate)
    depth_df = df[df['event_type'] == 'depthUpdate'].copy()
    if not depth_df.empty:
        print(f"\n[L2 SEQUENCE INTEGRITY]")
        
        # Parse u and pu from payload
        import json
        def parse_ids(payload):
            try:
                data = json.loads(payload)
                return pd.Series([data.get('u', -1), data.get('pu', -1)])
            except:
                return pd.Series([-1, -1])
        
        depth_df[['u', 'pu']] = depth_df['payload'].apply(parse_ids)
        depth_df = depth_df.sort_values('exchange_timestamp')
        
        total_events = len(depth_df)
        
        # Continuity rule: pu of current event must match u of previous event
        depth_df['prev_u'] = depth_df['u'].shift(1)
        
        mask = depth_df['prev_u'].notnull()
        diffs = depth_df[mask & (depth_df['pu'] != depth_df['prev_u'])]
        
        gap_count = len(diffs)
        
        # Calculate InSync percentage
        # Simplified: consecutive matches
        insync_count = total_events - gap_count
        insync_pct = (insync_count / total_events) * 100 if total_events > 0 else 0
        
        print(f"Total depthUpdate events: {total_events:,}")
        print(f"Continuity Gaps: {gap_count}")
        print(f"InSync Time: {insync_pct:.2f}%")
        
        if gap_count == 0:
            print("STATUS: PASS (100% Sequence Continuity)")
        else:
            print(f"STATUS: WARNING ({gap_count} gaps detected)")
            if insync_pct < 95:
                print("CRITICAL: Significant InSync loss. Re-verify connectivity.")

    # 4. Payload Corruption Check
    depth_payloads = df[df['event_type'] == 'depthUpdate']['payload'].dropna()
    if not depth_payloads.empty:
        print(f"\n[PAYLOAD VALIDATION]")
        sample = depth_payloads.iloc[0]
        try:
            json.loads(sample)
            print("Format: JSON [OK]")
        except:
            print("Format: CORRUPTED [FAIL]")

    # 5. Resource Info
    file_size_mb = os.path.getsize(parquet_path) / (1024*1024)
    print(f"\n[RESOURCE INFO]")
    print(f"Parquet Size: {file_size_mb:.2f} MB")
    print(f"Density: {len(df)/file_size_mb:.0f} events/MB")

    print(f"\nAudit completed at {datetime.now()}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/l2_integrity_audit.py <path_to_parquet>")
    else:
        audit_dataset(sys.argv[1])
