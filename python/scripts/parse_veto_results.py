import os
import json
import glob

def calculate_veto_ratios():
    runs_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../runs'))
    print(f"Parsing candidate logs from {runs_dir}...\n")
    
    # Find all candidates.jsonl
    candidates_files = glob.glob(os.path.join(runs_dir, "test_run_*", "analytics", "candidates.jsonl"))
    
    if not candidates_files:
        print("No candidates.jsonl found. The test runs may not have written data to disk.")
        return

    for cf in candidates_files:
        run_id = os.path.basename(os.path.dirname(os.path.dirname(cf)))
        
        # Determine mode from run_id or fallback
        mode = "Unknown"
        if "LegacyRaw" in run_id: mode = "LegacyRaw"
        elif "ScaledX10000" in run_id: mode = "ScaledX10000"
        elif "BaselineOnly" in run_id: mode = "BaselineOnly"
        
        candidates = []
        with open(cf, 'r') as f:
            for line in f:
                if line.strip():
                    try:
                        candidates.append(json.loads(line))
                    except:
                        pass
        
        total = len(candidates)
        if total == 0:
            continue
            
        vetoes = sum(1 for c in candidates if c.get("is_veto", False))
        veto_reasons = {}
        for c in candidates:
             if c.get("is_veto"):
                 reason = c.get("veto_reason", "Unknown")
                 veto_reasons[reason] = veto_reasons.get(reason, 0) + 1
                 
        approved = total - vetoes
        veto_rate = (vetoes / total) * 100 if total > 0 else 0
        
        print(f"Results for Mode: {mode} (Run: {run_id})")
        print(f"  Total Candidates Evaluated: {total}")
        print(f"  Approved Trades:            {approved}")
        print(f"  Vetoed Candidates:          {vetoes} ({veto_rate:.2f}%)")
        print(f"  Veto Reason Breakdown:      {veto_reasons}")
        
        # Calculate some averages to prove the math
        if total > 0:
            avg_raw = sum(c.get("raw_model_value", 0.0) for c in candidates) / total
            avg_used = sum(c.get("expected_move_bps_used", 0.0) for c in candidates) / total
            print(f"  Avg Raw Model Value:        {avg_raw:.6f}")
            print(f"  Avg Expected Bps Used:      {avg_used:.6f}")
            
        print("-" * 50)

if __name__ == "__main__":
    calculate_veto_ratios()
