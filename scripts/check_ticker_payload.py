import pandas as pd
import json

df = pd.read_parquet('runs/20260317_2352_BTCUSDT/datasets/golden_l2_v1_train/normalized_events.parquet')
print(f"Columns: {df.columns.tolist()}")
ticker_events = df[df['event_type'] == 'bookTicker']
if not ticker_events.empty:
    col = 'payload_json' if 'payload_json' in df.columns else 'payload'
    first_payload = ticker_events.iloc[0][col]
    print(f"Ticker Payload: {first_payload}")
else:
    print("No bookTicker events found")
