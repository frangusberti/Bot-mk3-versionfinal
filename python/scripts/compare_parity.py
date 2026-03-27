import sys
import gzip
import json
import os
import numpy as np

OBS_DIM = 148

def load_jsonl_gz(path):
    # Returns dict: step_seq -> [obs_vec]
    if not os.path.exists(path):
        return {}
    data = {}
    with gzip.open(path, 'rt') as f:
        for line in f:
            if not line.strip(): continue
            row = json.loads(line)
            # Preference: step_seq, fallback: recv_time
            key = row.get("step_seq", row.get("recv_time"))
            obs = row["obs"]
            data[key] = obs
    return data

def main():
    if len(sys.argv) < 2:
        print("Usage: python compare_parity.py <run_id>")
        sys.exit(1)
        
    run_id = sys.argv[1]
    
    live_path = os.path.join("runs", run_id, "parity", "live_obs.jsonl.gz")
    replay_path = os.path.join("runs", run_id, "parity", "replay_obs.jsonl.gz")
    
    print(f"Loading Live Capture: {live_path}")
    live_data = load_jsonl_gz(live_path)
    if not live_data:
        print("Live Capture missing!")
        sys.exit(1)
        
    print(f"Loading Replay Capture: {replay_path}")
    replay_data = load_jsonl_gz(replay_path)
    if not replay_data:
        print("Replay Capture missing!")
        sys.exit(1)
        
    # Join by step_seq (or recv_time fallback)
    common_keys = sorted(list(set(live_data.keys()).intersection(set(replay_data.keys()))))
    
    print(f"\nStats:")
    print(f"  Live Rows: {len(live_data)}")
    print(f"  Replay Rows: {len(replay_data)}")
    print(f"  Joined Rows: {len(common_keys)}")
    
    if len(common_keys) == 0:
        print("ERROR: No common keys to compare! (Step sequence or Timestamps diverged)")
        sys.exit(1)
        
    # Check
    value_mismatches = 0
    mask_mismatches = 0
    total_samples = 0
    
    report = []
    
    for key in common_keys:
        l_obs = np.array(live_data[key], dtype=np.float32)
        r_obs = np.array(replay_data[key], dtype=np.float32)
        
        if len(l_obs) != OBS_DIM or len(r_obs) != OBS_DIM:
            print(f"Dim mismatch at SEQ/TS={key}: {len(l_obs)} vs {len(r_obs)}")
            continue
            
        total_samples += 1
        
        for i in range(OBS_DIM):
            v1 = float(l_obs[i])
            v2 = float(r_obs[i])
            diff = abs(v1 - v2)
            
            # Mask index parity check (Stage 1 uses masks at indexes 74..147)
            # obs[0..73] = values, obs[74..147] = masks
            is_mask = (i >= 74) 
            
            if is_mask:
                if diff > 1e-9:
                    mask_mismatches += 1
                    report.append(f"MASK_ERR @ {key} [idx={i}]: Live={v1} Replay={v2}")
            else:
                if diff > 1e-6:
                    value_mismatches += 1
                    report.append(f"VAL_ERR @ {key} [idx={i}]: Live={v1} Replay={v2} Diff={diff:.7f}")
    
    # Write report
    report_path = os.path.join("runs", run_id, "parity", "parity_report.txt")
    with open(report_path, "w") as f:
        f.write("# Parity Comparison Report\n\n")
        f.write(f"Total Samples Compared: {total_samples}\n")
        f.write(f"Mask Mismatches: {mask_mismatches}\n")
        f.write(f"Value Mismatches: {value_mismatches}\n")
        f.write("\n## Error Log\n")
        if not report:
            f.write("No mismatches found. Perfect parity!\n")
        else:
            f.write("\n".join(report[:100])) # Limit report size
            if len(report) > 100:
                f.write(f"\n... and {len(report)-100} more errors.")
    
    print("\nResults:")
    print(f"  Mask exact mismatches: {mask_mismatches}")
    print(f"  Value mismatches (1e-6): {value_mismatches}")
    print(f"Saved detailed report to: {report_path}")

    if mask_mismatches == 0 and value_mismatches < (total_samples * OBS_DIM * 0.001):
        print("\nPASS: Valid Parity Match Achieved!")
        sys.exit(0)
    else:
        print("\nFAIL: Mismatches threshold exceeded.")
        sys.exit(1)

if __name__ == "__main__":
    main()
