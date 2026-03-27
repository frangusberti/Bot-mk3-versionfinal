# scripts/create_golden_splits.py
import pandas as pd
import os
import json
import shutil

# Config
SOURCE_PARQUET = "runs/20260317_2352_BTCUSDT/events/BTCUSDT_2026-03-18_19_part-0001.parquet"
RUN_ID = "20260317_2352_BTCUSDT"
OUTPUT_ROOT = f"runs/{RUN_ID}/datasets"

# Exact Boundaries (from my previous calculation)
# START: 1773862066776
# END:   1773933550830
# Duration: 71,484,054 ms
BOUNDARIES = {
    "golden_l2_v1_train": (1773862066776, 1773912105613), # 70%
    "golden_l2_v1_val":   (1773912105614, 1773922828221), # 15%
    "golden_l2_v1_test":  (1773922828222, 1773933550830), # 15%
}

def create_splits():
    print(f"Loading source: {SOURCE_PARQUET}")
    df = pd.read_parquet(SOURCE_PARQUET)
    
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    
    for ds_id, (start, end) in BOUNDARIES.items():
        print(f"\nProcessing {ds_id}...")
        
        # Filter
        split_df = df[(df['exchange_timestamp'] >= start) & (df['exchange_timestamp'] <= end)]
        
        # Create dir
        ds_dir = os.path.join(OUTPUT_ROOT, ds_id)
        os.makedirs(ds_dir, exist_ok=True)
        
        # Save Parquet (Standard naming for bot-server)
        parquet_out = os.path.join(ds_dir, "normalized_events.parquet")
        split_df.to_parquet(parquet_out, index=False)
        
        # Create Manifest
        manifest = {
            "dataset_id": ds_id,
            "source_run_id": RUN_ID,
            "symbol": "BTCUSDT",
            "start_ts": int(start),
            "end_ts": int(end),
            "n_rows": len(split_df),
            "created_at": pd.Timestamp.now().isoformat(),
            "is_golden": True
        }
        
        with open(os.path.join(ds_dir, "dataset_manifest.json"), "w") as f:
            json.dump(manifest, f, indent=2)
            
        print(f"  Saved {len(split_df):,} rows to {parquet_out}")
        print(f"  Manifest: {os.path.join(ds_dir, 'dataset_manifest.json')}")

    print("\nSplitting and Registration Complete.")

if __name__ == "__main__":
    create_splits()
