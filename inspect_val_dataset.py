import pandas as pd
import json

df = pd.read_parquet(r"C:\Bot mk3\runs\stage2_eval\datasets\stage2_eval\normalized_events.parquet")
print("Columns:", df.columns.tolist())
print("\nUnique Event Types:", df['event_type'].unique())

print("\nFirst 10 rows:")
for i in range(min(10, len(df))):
    print(f"\nRow {i}:")
    print(f"  event_type: {df.iloc[i]['event_type']}")
    print(f"  payload_json: {df.iloc[i]['payload_json'][:200]}...")
