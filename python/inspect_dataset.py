import pandas as pd
import sys

path = r"C:\Bot mk3\runs\stage2_eval\datasets\stage2_eval\normalized_events.parquet"
try:
    df = pd.read_parquet(path)
    print(f"Dataset: {path}")
    print(f"Rows: {len(df)}")
    print("\nColumns and Dtypes:")
    print(df.dtypes)
    
    print("\nDescriptive Statistics (Numeric):")
    stats = df.describe()
    print(stats)
    
    # Check for extreme values specifically
    for col in ['price', 'best_bid', 'best_ask', 'mark_price', 'funding_rate', 'qty']:
        if col in df.columns:
            print(f"\nOutliers for {col}:")
            print(f"  Max: {df[col].max()}")
            print(f"  Min: {df[col].min()}")
            print(f"  Mean: {df[col].mean()}")
            
except Exception as e:
    print(f"Error: {e}")
