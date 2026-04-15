import pandas as pd
import os

path = r"c:\Bot mk3\runs\stage2_eval\datasets\stage2_eval\normalized_events.parquet"
if os.path.exists(path):
    df = pd.read_parquet(path, columns=["stream_name", "event_type", "side", "payload_json"], engine="pyarrow")
    trades = df[df["event_type"] == "trade"].head(20)
    print("--- PARQUET SAMPLES (TRADES) ---")
    print(trades)
    
    # Check if side is constant
    print("\n--- SIDE FREQUENCIES ---")
    print(df[df["event_type"] == "trade"]["side"].value_counts())
    
    # Check payload_json for "m"
    print("\n--- PAYLOAD_JSON SAMPLES ---")
    for sj in trades["payload_json"].head(5):
        print(sj)
else:
    print(f"Path not found: {path}")
