"""
parity_check_teacher.py — Live / Offline Feature Parity Scaffold for Teacher Policy V1

Compares the 19 teacher-safe features between:
  - Live trading snapshot (from a logged CSV/JSON session)
  - Offline replay reconstruction (from GrpcTradingEnv over parquet data)

PASS/FAIL decision:
  - Each feature: PASS if max absolute delta < TOL (default 0.01)
  - Overall: PASS if all features PASS

Usage:
  # Full offline replay parity check (requires bot-server running):
  python parity_check_teacher.py --mode replay --dataset stage2_eval

  # Compare a live snapshot CSV against offline:
  python parity_check_teacher.py --mode snapshot \
      --live live_features_snapshot.csv \
      --replay replay_features.csv

Expected CSV format:
  ts, spread_bps, spread_vs_baseline, ... (one row per 1s tick)
"""

import os
import sys
import csv
import json
import argparse
import numpy as np
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from teacher_policy import TEACHER_FEATURE_NAMES

# Try importing GrpcTradingEnv
try:
    from grpc_env import GrpcTradingEnv
    GRPC_AVAILABLE = True
except ImportError:
    GRPC_AVAILABLE = False

# Try importing obs_vec_to_feature_dict from generate_bc_dataset
try:
    from generate_bc_dataset import obs_vec_to_feature_dict, OBS_FEATURE_NAMES
except ImportError:
    OBS_FEATURE_NAMES = None

# ---------------------------------------------------------------------------
# Tolerance for PASS/FAIL
# ---------------------------------------------------------------------------
FEATURE_TOL = {feat: 0.01 for feat in TEACHER_FEATURE_NAMES}
# Tighter tolerance for spread features (money-sensitive)
FEATURE_TOL["spread_bps"]           = 0.005
FEATURE_TOL["spread_vs_baseline"]   = 0.02   # EWMA may drift slightly
# Looser tolerance for slow-converging regime features
FEATURE_TOL["regime_range"]         = 0.05
FEATURE_TOL["regime_shock"]         = 0.05
FEATURE_TOL["regime_dead"]          = 0.05
# Account state is injected, expect exact match
FEATURE_TOL["position_flag"]        = 0.001
FEATURE_TOL["current_drawdown_pct"] = 0.001


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def check_parity(live_df: pd.DataFrame, replay_df: pd.DataFrame) -> dict:
    """
    Compare matching teacher features between live and replay DataFrames.

    Both DataFrames should have one row per 1s tick, aligned by index or timestamp.
    The comparison assumes they cover the same time window.

    Returns:
        dict with per-feature results: {feat: {max_delta, status, tol}}
        and an overall 'PASS' bool.
    """
    results = {}
    all_pass = True

    min_len = min(len(live_df), len(replay_df))
    if min_len == 0:
        print("[PARITY] ERROR: empty DataFrame(s).")
        return {"overall": "FAIL_EMPTY"}

    live_slice   = live_df.iloc[:min_len]
    replay_slice = replay_df.iloc[:min_len]

    for feat in TEACHER_FEATURE_NAMES:
        tol = FEATURE_TOL.get(feat, 0.01)

        if feat not in live_df.columns:
            results[feat] = {"max_delta": None, "status": "MISSING_IN_LIVE", "tol": tol}
            all_pass = False
            continue
        if feat not in replay_df.columns:
            results[feat] = {"max_delta": None, "status": "MISSING_IN_REPLAY", "tol": tol}
            all_pass = False
            continue

        live_vals   = live_slice[feat].fillna(0.0).astype(float).values
        replay_vals = replay_slice[feat].fillna(0.0).astype(float).values
        deltas      = np.abs(live_vals - replay_vals)
        max_delta   = float(np.max(deltas))
        mean_delta  = float(np.mean(deltas))

        status = "PASS" if max_delta <= tol else "FAIL"
        if status == "FAIL":
            all_pass = False

        results[feat] = {
            "max_delta":  round(max_delta, 6),
            "mean_delta": round(mean_delta, 6),
            "status":     status,
            "tol":        tol,
        }

    results["_overall"] = "PASS" if all_pass else "FAIL"
    results["_n_compared"] = min_len
    return results


def print_parity_report(results: dict):
    """Pretty-print the parity check results."""
    print("\n=== Teacher Feature Parity Report ===")
    overall = results.get("_overall", "?")
    n = results.get("_n_compared", "?")
    print(f"Overall: [{overall}]  (n={n} ticks compared)\n")

    header = f"{'Feature':<30} {'Max Delta':>10} {'Mean Delta':>12} {'Tol':>8} {'Status':>8}"
    print(header)
    print("-" * len(header))

    for feat in TEACHER_FEATURE_NAMES:
        r = results.get(feat, {})
        if not r or "max_delta" not in r or r["max_delta"] is None:
            status = r.get("status", "MISSING")
            print(f"  {feat:<28} {'N/A':>10} {'N/A':>12} {'':>8} {status:>8}")
            continue
        max_d  = r["max_delta"]
        mean_d = r["mean_delta"]
        tol    = r["tol"]
        status = r["status"]
        flag   = "✓" if status == "PASS" else "✗"
        print(f"  {feat:<28} {max_d:>10.6f} {mean_d:>12.6f} {tol:>8.4f} {flag} {status}")


# ---------------------------------------------------------------------------
# Replay mode: extract features from a live GrpcTradingEnv episode
# ---------------------------------------------------------------------------

def collect_replay_features(dataset_id: str, server_addr: str = "localhost:50051",
                             n_steps: int = 1000) -> pd.DataFrame:
    """
    Run an offline replay episode and extract per-step feature dicts.
    Returns a DataFrame with teacher feature columns.
    """
    if not GRPC_AVAILABLE:
        raise RuntimeError("grpc_env not available. Is bot_ml on the Python path?")

    env = GrpcTradingEnv(
        server_addr=server_addr,
        dataset_id=dataset_id,
        symbol="BTCUSDT",
        initial_equity=10000.0,
        maker_fee=2.0,
        taker_fee=5.0,
        slip_bps=1.0,
    )

    try:
        from generate_bc_dataset import obs_vec_to_feature_dict as o2f
    except ImportError:
        def o2f(obs):
            return {}

    obs, info = env.reset()
    rows = []

    for _ in range(n_steps):
        fd   = o2f(obs)
        row  = {"ts": info.get("ts", 0)}
        row.update({f: fd.get(f, None) for f in TEACHER_FEATURE_NAMES})
        rows.append(row)

        # HOLD to not perturb market state
        obs, reward, terminated, truncated, info = env.step(0)
        if terminated or truncated:
            break

    env.close()
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Teacher Feature Parity Check")
    parser.add_argument("--mode", choices=["snapshot", "replay", "replay_only"],
                        default="replay_only",
                        help="snapshot: compare two CSVs | replay: run replay + compare | "
                             "replay_only: just dump replay features to CSV")
    parser.add_argument("--live",    default=None, help="Path to live features CSV")
    parser.add_argument("--replay",  default=None, help="Path to replay features CSV (for snapshot mode)")
    parser.add_argument("--dataset", default="stage2_eval", help="Dataset ID for replay mode")
    parser.add_argument("--server",  default="localhost:50051")
    parser.add_argument("--steps",   type=int, default=1000, help="Steps to run in replay mode")
    parser.add_argument("--output",  default="parity_replay_features.csv",
                        help="Output path for replay features (replay_only mode)")
    parser.add_argument("--report",  default=None, help="Optional: save JSON report to path")
    args = parser.parse_args()

    if args.mode == "replay_only":
        print(f"[PARITY] Collecting {args.steps} replay steps from dataset={args.dataset}...")
        replay_df = collect_replay_features(args.dataset, args.server, args.steps)
        replay_df.to_csv(args.output, index=False)
        print(f"[PARITY] Saved replay features: {args.output}  ({len(replay_df)} rows)")
        print(f"[PARITY] Compare this file against a live session CSV to validate parity.")
        return

    if args.mode == "snapshot":
        if not args.live or not args.replay:
            print("[PARITY] ERROR: --live and --replay paths required for snapshot mode.")
            sys.exit(1)
        live_df   = pd.read_csv(args.live)
        replay_df = pd.read_csv(args.replay)

    elif args.mode == "replay":
        if not args.live:
            print("[PARITY] ERROR: --live path required for replay mode.")
            sys.exit(1)
        live_df   = pd.read_csv(args.live)
        print(f"[PARITY] Collecting {args.steps} replay steps from dataset={args.dataset}...")
        replay_df = collect_replay_features(args.dataset, args.server, min(len(live_df), args.steps))

    else:
        print(f"[PARITY] Unknown mode: {args.mode}")
        sys.exit(1)

    results = check_parity(live_df, replay_df)
    print_parity_report(results)

    if args.report:
        with open(args.report, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n[PARITY] JSON report saved: {args.report}")

    overall = results.get("_overall", "FAIL")
    sys.exit(0 if overall == "PASS" else 1)


if __name__ == "__main__":
    main()
