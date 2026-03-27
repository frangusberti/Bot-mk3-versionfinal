# scripts/verify_golden_features.py
import pandas as pd
import numpy as np
import json
import os
import sys

def verify_dataset(parquet_path):
    if not os.path.exists(parquet_path):
        print(f"ERROR: File not found: {parquet_path}")
        return

    print(f"=== Golden Feature Validation ===")
    print(f"File: {parquet_path}")
    
    try:
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        print(f"FAILED to read parquet: {e}")
        return

    # 1. Basic Stats
    print(f"Total Rows: {len(df):,}")
    
    # 2. Check for NaNs in critical columns
    critical_cols = ['exchange_timestamp', 'bid_price', 'ask_price', 'price']
    for col in critical_cols:
        nan_count = df[col].isna().sum()
        if nan_count > 0:
            print(f"FAIL: Found {nan_count} NaNs in {col}")
        else:
            print(f"CHECK: {col} is NaN-free [OK]")

    # 3. mid_price validity
    df['mid_price'] = (df['bid_price'] + df['ask_price']) / 2
    invalid_mid = df[df['mid_price'] <= 0]
    if not invalid_mid.empty:
        print(f"FAIL: Found {len(invalid_mid)} invalid mid_prices (<= 0)")
    else:
        print(f"CHECK: mid_price is always positive [OK]")

    # 4. L2 Feature Presence (Depth payload)
    depth_updates = df[df['event_type'] == 'depthUpdate']
    if depth_updates.empty:
        print(f"FAIL: No depthUpdate events found!")
    else:
        print(f"CHECK: Found {len(depth_updates):,} depthUpdate events [OK]")
        
        # Verify a sample payload has L2 levels
        sample_payload = json.loads(depth_updates.iloc[0]['payload'])
        if 'b' in sample_payload and 'a' in sample_payload:
            print(f"CHECK: Depth payload contains bids/asks levels [OK]")
            print(f"  Sample Bid Levels: {len(sample_payload['b'])}")
            print(f"  Sample Ask Levels: {len(sample_payload['a'])}")
        else:
            print(f"FAIL: depthUpdate payload missing 'b' or 'a' keys")

    # 5. Synthetic Price Guard
    # We check if there are any specific flags or symbols that would indicate synthetic fallback.
    if any(df['symbol'].str.contains('SYNTH', case=False)):
        print(f"FAIL: Found synthetic-labeled symbols!")
    else:
        print(f"CHECK: No synthetic symbol labels found [OK]")

    print(f"=== Validation Completed ===")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/verify_golden_features.py <parquet>")
    else:
        verify_dataset(sys.argv[1])
