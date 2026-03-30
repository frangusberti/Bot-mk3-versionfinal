import pandas as pd
import numpy as np

# Load the dataset
df = pd.read_parquet('data/teacher_vnext_10k_rapid_v4.parquet')

print("--- BC Dataset Audit ---")
print(f"Total samples: {len(df)}")
print("\nAction counts:")
counts = df['action'].value_counts().sort_index()
for act, count in counts.items():
    print(f"Action {act}: {count}")

# Check for ADD_LONG (2)
add_longs = df[df['action'] == 2]
print(f"\nFound {len(add_longs)} samples of ADD_LONG (2).")
if not add_longs.empty:
    sample = add_longs.head(5)
    for i, row in sample.iterrows():
        # obs[48] is position_flag in Sprint 2 Schema v6
        pos = row['obs'][48]
        print(f"Row {i} | TS: {row['ts']} | Action: {row['action']} | PosFlag (Obs[48]): {pos}")

# Check for ADD_SHORT (6)
add_shorts = df[df['action'] == 6]
print(f"\nFound {len(add_shorts)} samples of ADD_SHORT (6).")
if not add_shorts.empty:
    sample = add_shorts.head(5)
    for i, row in sample.iterrows():
        pos = row['obs'][48]
        print(f"Row {i} | TS: {row['ts']} | Action: {row['action']} | PosFlag (Obs[48]): {pos}")

# Check if any action=1 (OPEN_LONG) has PosFlag != 0
open_long_mismatch = df[(df['action'] == 1) & (df['obs'].apply(lambda x: x[48] != 0))]
print(f"\nOPEN_LONG (1) with non-zero position: {len(open_long_mismatch)}")

# Check if any action=2 (ADD_LONG) has PosFlag == 0
add_long_mismatch = df[(df['action'] == 2) & (df['obs'].apply(lambda x: x[48] == 0))]
print(f"ADD_LONG (2) with zero position: {len(add_long_mismatch)}")

# Check if any action=5 (OPEN_SHORT) has PosFlag != 0
open_short_mismatch = df[(df['action'] == 5) & (df['obs'].apply(lambda x: x[48] != 0))]
print(f"OPEN_SHORT (5) with non-zero position: {len(open_short_mismatch)}")

# Check if any action=6 (ADD_SHORT) has PosFlag == 0
add_short_mismatch = df[(df['action'] == 6) & (df['obs'].apply(lambda x: x[48] == 0))]
print(f"ADD_SHORT (6) with zero position: {len(add_short_mismatch)}")
