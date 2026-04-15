"""
Sanity Audit V7 — ITR Feature Warmup Verification
===================================================
Verifies that the 5.5h pre-roll correctly populates ALL ITR features
(RSI, Bollinger, Slopes) before episode Step 0.

Reports:
  - % valid features at step 0
  - % valid RSI 1m, BB 5m, slope 15m
  - 3 real examples of each with mask=1
  
Memory-optimized: holds only 1 observation at a time.
"""

import os, sys
import grpc
import numpy as np

# Force UTF-8 output on Windows
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.join(os.getcwd(), 'python'))
sys.path.insert(0, os.path.join(os.getcwd(), 'python', 'bot_ml'))

import bot_pb2
import bot_pb2_grpc


# ─── Feature Index Map (Schema v7: 83 features) ───
# A) Price/Spread: 0-3
# B) Returns/Vol:  4-16
# C) Taker Flow:   17-26
# D) Micro:        27-39
# E) Shocks:       40-46
# F) Technicals:   47-56
# G) Account:      57-60
# H) Time:         61-62
# I) OI:           63-67
# J) Absorption:   68-71
# K) Persistence:  72-78
# L) Regime:       79-82

FEATURE_NAMES = {
    # F) Technicals (indices 47-56)
    47: "ema200_dist_pct",
    48: "rsi_1m",
    49: "bb_width_1m",
    50: "bb_pos_1m",
    51: "rsi_5m",
    52: "bb_width_5m",
    53: "bb_pos_5m",
    54: "rsi_15m",
    55: "bb_width_15m",
    56: "bb_pos_15m",
    # B) Returns/Vol slopes (indices 12-16)
    12: "slope_mid_5s",
    13: "slope_mid_15s",
    14: "slope_mid_60s",
    15: "slope_mid_5m",
    16: "slope_mid_15m",
    # A) Price
    0: "mid_price",
    1: "spread_abs",
    2: "spread_bps",
    3: "spread_vs_baseline",
    # B) Returns
    4: "ret_1s",
    5: "ret_3s",
    6: "ret_5s",
    7: "ret_10s",
    8: "ret_30s",
    9: "rv_5s",
    10: "rv_30s",
    11: "rv_5m",
}

# Groups to audit individually
AUDIT_GROUPS = {
    "RSI 1m":       [48],
    "BB width 5m":  [52],
    "BB pos 5m":    [53],
    "slope 15m":    [16],
    "RSI 5m":       [51],
    "RSI 15m":      [54],
    "BB width 1m":  [49],
    "BB pos 1m":    [50],
    "BB width 15m": [55],
    "BB pos 15m":   [56],
    "slope 5m":     [15],
    "slope 60s":    [14],
    "rv_5m":        [11],
    "rv_30s":       [10],
    "ret_30s":      [8],
}


def analyze_obs(obs_vec, step_label="Step 0"):
    """Analyze a single observation vector, return summary dict."""
    n = len(obs_vec) // 2  # 83
    vals = obs_vec[:n]
    masks = obs_vec[n:]
    
    total_valid = int(np.sum(masks > 0.5))
    pct_valid = total_valid / n * 100
    
    print(f"\n{'='*60}")
    print(f"  {step_label} Feature Audit")
    print(f"{'='*60}")
    print(f"  Total Features:  {n}")
    print(f"  Valid (mask=1):  {total_valid} ({pct_valid:.1f}%)")
    print(f"  Invalid (mask=0): {n - total_valid}")
    
    print(f"\n  --- ALL INVALID FEATURES ---")
    for i in range(n):
        if masks[i] <= 0.5:
            fname = FEATURE_NAMES.get(i, f"feat_{i}")
            print(f"  [MISSING] {i:2d} {fname}")

    # Per-group validity
    print(f"\n  --- ITR Feature Validity ---")
    for group_name, indices in AUDIT_GROUPS.items():
        for idx in indices:
            if idx < n:
                v = vals[idx]
                m = masks[idx]
                status = "[OK] VALID" if m > 0.5 else "[!!] INVALID"
                fname = FEATURE_NAMES.get(idx, f"feat_{idx}")
                print(f"  [{idx:2d}] {fname:20s}: {v:12.6f} | {status}")
    
    return {
        "total": n,
        "valid": total_valid, 
        "pct": pct_valid,
        "vals": vals,
        "masks": masks,
    }


def collect_examples(stub, episode_id, n_steps=10):
    """Collect a few steps to gather valid examples."""
    examples = {}  # group_name -> list of (step, value)
    
    for step in range(n_steps):
        try:
            step_req = bot_pb2.StepRequest(
                episode_id=episode_id,
                action=bot_pb2.Action(type=0)  # HOLD
            )
            resp = stub.Step(step_req)
            obs = np.array(resp.obs.vec)
            n = len(obs) // 2
            vals = obs[:n]
            masks = obs[n:]
            
            for group_name, indices in AUDIT_GROUPS.items():
                if group_name not in examples:
                    examples[group_name] = []
                for idx in indices:
                    if idx < n and masks[idx] > 0.5 and len(examples[group_name]) < 3:
                        examples[group_name].append((step + 1, idx, vals[idx]))
        except Exception as e:
            print(f"  Step {step} failed: {e}")
            break
    
    return examples


def run_audit():
    channel = grpc.insecure_channel('localhost:50051')
    stub = bot_pb2_grpc.RLServiceStub(channel)
    
    print("=" * 50)
    print("  Sanity Audit V7 - ITR Warmup Verifier")
    print("=" * 50)
    
    # --- Reset Episode ---
    # Use random_start_offset=True to test the fix
    reset_req = bot_pb2.ResetRequest(
        dataset_id="golden_l2_v1_train",
        symbol="BTCUSDT",
        seed=42,
        config=bot_pb2.RLConfig(
            random_start_offset=True,
            min_episode_events=5000,
            allow_bad_quality=True,
        )
    )
    
    try:
        print("\n  Requesting Reset Episode...")
        response = stub.ResetEpisode(reset_req, timeout=3600)
        print(f"  Episode ID: {response.episode_id}")
        print(f"  Obs timestamp: {response.obs.ts}")
        
        # --- Feature Health from server ---
        health = response.feature_health
        print(f"\n  --- Server-Side Warmup Report ---")
        print(f"  1m Candles:   {health.h1m_candles:4d} / 20  {'OK' if health.h1m_candles >= 20 else 'MISSING'}")
        print(f"  5m Candles:   {health.h5m_candles:4d} / 20  {'OK' if health.h5m_candles >= 20 else 'MISSING'}")
        print(f"  15m Candles:  {health.h15m_candles:4d} / 20  {'OK' if health.h15m_candles >= 20 else 'MISSING'}")
        print(f"  Mid History:  {health.mid_history_len:4d} / 900 {'OK' if health.mid_history_len >= 900 else 'MISSING'}")
        
        # --- Analyze Step 0 observation ---
        obs = np.array(response.obs.vec)
        if len(obs) != 166:
            print(f"\n  !! Unexpected OBS_DIM: {len(obs)} (expected 166)")
            return
        
        result = analyze_obs(obs, "Step 0")
        
        # --- Summary ---
        n = result["total"]
        masks = result["masks"]
        vals = result["vals"]
        
        # Per-group % valid
        print(f"\n  --- Group Validity Summary ---")
        groups = [
            ("RSI 1m",      [48]),
            ("BB 5m",       [52, 53]),
            ("slope 15m",   [16]),
            ("All ITR Tech", list(range(47, 57))),
            ("All Slopes",  [12, 13, 14, 15, 16]),
            ("All Returns", list(range(4, 12))),
        ]
        for gname, gidx in groups:
            valid = sum(1 for i in gidx if i < n and masks[i] > 0.5)
            total = len([i for i in gidx if i < n])
            pct = valid / total * 100 if total > 0 else 0
            print(f"  {gname:20s}: {valid}/{total} ({pct:.0f}%)")
        
        # --- Collect 3 real examples ---
        print(f"\n  --- Collecting Examples (10 steps) ---")
        examples = collect_examples(stub, response.episode_id, n_steps=10)
        
        print(f"\n  --- Real Examples (mask=1) ---")
        target_groups = ["RSI 1m", "BB width 5m", "BB pos 5m", "slope 15m", 
                         "RSI 5m", "RSI 15m", "BB width 15m", "BB pos 15m"]
        for group_name in target_groups:
            exs = examples.get(group_name, [])
            if exs:
                print(f"\n  {group_name}:")
                for step, idx, val in exs[:3]:
                    fname = FEATURE_NAMES.get(idx, f"feat_{idx}")
                    print(f"    Step {step:3d} | [{idx:2d}] {fname}: {val:.6f}")
            else:
                print(f"\n  {group_name}: [!!] No valid examples found in 10 steps")
        
        # --- Final Verdict ---
        print(f"\n{'='*60}")
        all_tech_valid = all(masks[i] > 0.5 for i in range(47, 57) if i < n)
        all_slopes_valid = all(masks[i] > 0.5 for i in [12, 13, 14, 15, 16] if i < n)
        
        if result["pct"] > 95 and all_tech_valid and all_slopes_valid:
            print("  PASS: All ITR features are warmed up at Step 0!")
        elif result["pct"] > 80:
            print("  PARTIAL: Most features valid but some ITR gaps remain.")
        else:
            print(f"  FAIL: Only {result['pct']:.1f}% valid. Warmup is broken.")
        print(f"{'='*60}")
        
    except grpc.RpcError as e:
        print(f"\n  ❌ gRPC Error: {e.code()} — {e.details()}")
    except Exception as e:
        print(f"\n  ❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_audit()
