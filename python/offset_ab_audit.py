import sys
import os
import json
import pandas as pd

# Add local paths
sys.path.insert(0, os.path.dirname(__file__))
from ppo_eval_checkpoint import run_ppo_audit

# Config
MODEL = "python/runs_train/phase27_scaling/model_50k.zip"
VENV = "python/runs_train/phase27_scaling/venv_50k.pkl"
DATASET = "golden_l2_v1_val"
STEPS = 5000

# Variant A: Control (0.2, Realistic)
# Variant B: Treatment (0.1, Realistic)
# Variant C: Diagnostic (0.1, Optimistic)
# Variant D: Vitality ( -1.0, Taker)

results = {}

for label, offset, fill_model in [
    ("A_0.2_Real", 0.2, 1), 
    ("B_0.1_Real", 0.1, 1), 
    ("C_0.1_Opt", 0.1, 2),
    ("D_Taker", -1.0, 1)
]:
    print(f"\n--- Running Variant {label} (Offset: {offset}, Fill: {fill_model}) ---")
    try:
        res = run_ppo_audit(
            model_path=MODEL,
            venv_path=VENV,
            dataset_id=DATASET,
            steps_per_eval=STEPS,
            min_post_offset_bps=offset,
            fill_model=fill_model, 
            profit_floor_bps=2.0,
            server="localhost:50051"
        )
        results[label] = res
    except Exception as e:
        print(f"FAILED variant {label}: {e}")

# Compare
if results:
    print("\n--- Comparative Summary ---")
    
    rows = []
    for label, res in results.items():
        lc = res["lifecycle"]
        detailed = lc["action_usage_detailed"]
        rows.append({
            "Variant": label,
            "HOLD %": f"{lc['semantic_summary']['HOLD']:.2f}",
            "OPEN %": f"{lc['semantic_summary']['OPEN']:.2f}",
            "Blocks": res["gate_telemetry"]["offset"],
            "Trades": res["total_trades"],
            "InvRate": f"{detailed.get('ADD_LONG', 0) + detailed.get('ADD_SHORT', 0):.2f}"
        })
    
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))

    # Save
    out_path = "python/runs_train/phase27_scaling/ab_audit_offset.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to {out_path}")
else:
    print("\nNo results to compare.")
