# scripts/inspect_capture_payload.py
import pandas as pd
import json
import os

path = r"c:\Bot mk3\runs\20260317_2352_BTCUSDT\events\BTCUSDT_2026-03-17_23_part-0000.parquet"

if os.path.exists(path):
    print(f"Reading {path}...")
    # Just read first 100 rows to check payloads
    df = pd.read_parquet(path)
    print(f"\nTotal rows in first part: {len(df):,}")
    
    # Check non-empty payloads
    payload_counts = df[df['payload'] != ""]['event_type'].value_counts()
    print("\nPayload counts by event type:")
    if payload_counts.empty:
        print("ALL PAYLOADS ARE EMPTY!")
    else:
        print(payload_counts)
        
    # Sample a payload
    if not payload_counts.empty:
        sample = df[df['payload'] != ""].iloc[0]['payload']
        print(f"\nSample payload (len {len(sample)}):")
        print(sample[:500] + "...")
else:
    print(f"File not found: {path}")
