import pandas as pd

df = pd.read_parquet('runs/20260317_2352_BTCUSDT/datasets/golden_l2_v1_train/normalized_events.parquet')
col = 'payload' if 'payload' in df.columns else 'payload_json'
for et in ['bookTicker', 'depthUpdate', 'trade', 'aggTrade']:
    subset = df[df['event_type'] == et]
    if subset.empty:
        print(f"Event: {et} - EMPTY SUBSET")
        continue
    non_empty = subset[col].apply(lambda x: len(str(x)) > 0).sum()
    print(f"Event: {et}, Total: {len(subset)}, Non-Empty Payload: {non_empty}")
    if non_empty > 0:
        print(f"Sample {et} payload: {subset[col].dropna().iloc[0]}")
