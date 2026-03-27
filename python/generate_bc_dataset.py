"""
generate_bc_dataset.py — Behavior Cloning Dataset Generator for Teacher Policy V1

Generates a labeled dataset by running the Teacher Policy V1 over replay datasets.
Each row contains:
  - full 148-dim observation vector (for BC network)
  - 19-feature teacher subset
  - teacher action + label + dominant reason
  - all 5 scores

Usage:
  python generate_bc_dataset.py [--datasets stage2_train stage2_eval] [--episodes 1]

Output:
  python/runs_train/bc_datasets/bc_teacher_v1_<N>_<date>.parquet
  python/runs_train/bc_datasets/bc_teacher_v1_<N>_<date>.metadata.json
"""

import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
from datetime import datetime

# Insert bot_ml path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'bot_ml'))

from grpc_env import GrpcTradingEnv
from teacher_policy import (
    teacher_decide,
    compute_scores,
    extract_teacher_features,
    TEACHER_FEATURE_NAMES,
    ACTION_LABELS,
    TEACHER_PARAMS,
    TeacherParams,
)

# Format: {proto_int: human_name} — must match grpc_env fill_model param
try:
    import bot_pb2
    FILL_MODEL_OPTIMISTIC = bot_pb2.MAKER_FILL_MODEL_OPTIMISTIC
except Exception:
    FILL_MODEL_OPTIMISTIC = 2   # fallback if proto not importable here

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TEACHER_VERSION         = "v2"
FEATURE_SCHEMA_VERSION  = 6
OBS_DIM                 = 148

OUTPUT_DIR = os.path.join("python", "runs_train", "bc_datasets")

# FeatureRow field names in obs_vec order (must match schema.rs to_obs_vec)
# These are the raw feature names used to reconstruct a feature dict from obs.
# We store the raw feature names alongside the obs_vec for interpretability.
OBS_FEATURE_NAMES = [
    # A) Price/Spread (4)
    "mid_price", "spread_abs", "spread_bps", "spread_vs_baseline",
    # B) Returns & Volatility (10)
    "ret_1s", "ret_3s", "ret_5s", "ret_10s", "ret_30s",
    "rv_5s", "rv_30s", "rv_5m", "slope_mid_5s", "slope_mid_15s",
    # C) Taker Flow (10)
    "taker_buy_vol_1s", "taker_sell_vol_1s", "taker_buy_vol_5s", "taker_sell_vol_5s",
    "tape_trades_1s", "tape_intensity_z", "trade_imbalance_1s", "trade_imbalance_5s",
    "trade_imbalance_15s", "tape_intensity_5s_z",
    # D) Microstructure (13)
    "obi_top1", "obi_top3", "obi_top10", "microprice", "microprice_minus_mid_bps",
    "obi_delta_5s", "delta_obi_top1_1s", "delta_microprice_1s",
    "depth_bid_top5", "depth_ask_top5", "depth_imbalance_top5",
    "depth_change_bid_1s", "depth_change_ask_1s",
    # E) Shocks (7)
    "liq_buy_vol_30s", "liq_sell_vol_30s", "liq_net_30s", "liq_count_30s",
    "mark_minus_mid_bps", "funding_rate", "funding_zscore",
    # F) Technicals (4)
    "ema200_distance_pct", "rsi_14", "bb_width", "bb_pos",
    # G) Account (4)
    "position_flag", "latent_pnl_pct", "max_pnl_pct", "current_drawdown_pct",
    # H) Time (2)
    "time_sin", "time_cos",
    # I) Open Interest (5)
    "oi_value", "oi_delta_30s", "oi_delta_1m", "oi_delta_5m", "oi_zscore_30m",
    # J) Absorption (4)
    "price_response_buy_5s", "price_response_sell_5s",
    "microprice_confirmation_5s", "breakout_failure_5s",
    # K) Persistence (7)
    "obi_persistence_buy", "obi_persistence_sell",
    "flow_persistence_buy", "flow_persistence_sell",
    "spread_deterioration", "depth_deterioration_bid", "depth_deterioration_ask",
    # L) Regime (4)
    "regime_trend", "regime_range", "regime_shock", "regime_dead",
]
assert len(OBS_FEATURE_NAMES) == 74, f"OBS_FEATURE_NAMES count mismatch: {len(OBS_FEATURE_NAMES)}"


def obs_vec_to_feature_dict(obs_vec: np.ndarray) -> dict:
    """
    Convert a 148-dim obs_vec (74 values + 74 masks) back to a named feature dict.
    Masked values (mask=0) are restored as None.
    """
    assert len(obs_vec) == OBS_DIM, f"Expected obs_dim={OBS_DIM}, got {len(obs_vec)}"
    values = obs_vec[:74]
    masks  = obs_vec[74:]
    return {
        name: (float(values[i]) if masks[i] > 0.5 else None)
        for i, name in enumerate(OBS_FEATURE_NAMES)
    }


def collect_episode(env: GrpcTradingEnv, model=None, params: TeacherParams = TEACHER_PARAMS):
    """
    Run one full episode through the env using the teacher policy.
    Optionally pass a trained model to also record its predicted action (for comparison).

    Returns: list of row dicts
    """
    obs, info = env.reset()
    rows = []
    step = 0

    while True:
        # Reconstruct feature dict from obs_vec
        feature_dict = obs_vec_to_feature_dict(obs)

        # Teacher decision
        act_int, reason, scores = teacher_decide(feature_dict, params)
        act_label = ACTION_LABELS[act_int]

        # Teacher 19-feature subset (raw floats)
        teacher_feats = extract_teacher_features(feature_dict)

        row = {
            "ts":                   info.get("ts", 0),
            "symbol":               env.symbol,
            # full obs vector as list
            "obs_vec":              obs.tolist(),
            # teacher 19-feature subset
            "teacher_obs":          teacher_feats.tolist(),
            # teacher decision
            "action":               act_int,
            "action_label":         act_label,
            "dominant_reason":      reason,
            # scores
            "score_bid":            scores["bid"],
            "score_ask":            scores["ask"],
            "score_cancel":         scores["cancel"],
            "score_no_trade":       scores["no_trade"],
            "score_emergency_exit": scores["emergency_exit"],
            # account context (already in obs_vec, kept here for convenience)
            "position_flag":        feature_dict.get("position_flag") or 0.0,
            "current_drawdown_pct": feature_dict.get("current_drawdown_pct") or 0.0,
            # env info
            "mid_price":            info.get("mid_price", 0.0),
            "equity":               info.get("equity", 0.0),
        }

        # Optional: record model's predicted action for comparison
        if model is not None:
            model_act, _ = model.predict(obs, deterministic=True)
            row["model_action"]       = int(model_act)
            row["model_action_label"] = ACTION_LABELS.get(int(model_act), "UNKNOWN")

        rows.append(row)

        # Execute the teacher action in the env (teacher drives the episode)
        obs, reward, terminated, truncated, info = env.step(act_int)
        step += 1

        if terminated or truncated:
            break

    return rows


def generate_dataset(
    dataset_ids: list,
    episodes_per_dataset: int = 1,
    server_addr: str = "localhost:50051",
    params: TeacherParams = TEACHER_PARAMS,
    model=None,
) -> pd.DataFrame:
    """
    Generate BC dataset by running teacher over multiple replay datasets.
    """
    all_rows = []

    for dataset_id in dataset_ids:
        print(f"\n[BC_GEN] Dataset: {dataset_id}")
        for ep in range(episodes_per_dataset):
            env = GrpcTradingEnv(
                server_addr=server_addr,
                dataset_id=dataset_id,
                symbol="BTCUSDT",
                initial_equity=10000.0,
                maker_fee=2.0,
                taker_fee=5.0,
                slip_bps=1.0,
                fill_model=FILL_MODEL_OPTIMISTIC,  # audit/bc uses optimistic for smooth episodes
            )
            try:
                rows = collect_episode(env, model=model, params=params)
                for r in rows:
                    r["dataset_id"] = dataset_id
                    r["episode_idx"] = ep
                all_rows.extend(rows)
                print(f"  Episode {ep+1}/{episodes_per_dataset}: {len(rows)} steps")
            except Exception as e:
                print(f"  [ERROR] Episode {ep+1} failed: {e}")
            finally:
                env.close()

    return pd.DataFrame(all_rows)


def save_dataset(df: pd.DataFrame, params: TeacherParams, dataset_ids: list, output_dir: str):
    """Save the BC dataset and its metadata JSON."""
    os.makedirs(output_dir, exist_ok=True)

    date_str  = datetime.now().strftime("%Y%m%d_%H%M%S")
    n_rows    = len(df)
    base_name = f"bc_teacher_{TEACHER_VERSION}_{n_rows}_{date_str}"
    parquet_path  = os.path.join(output_dir, base_name + ".parquet")
    metadata_path = os.path.join(output_dir, base_name + ".metadata.json")

    # Save parquet
    df.to_parquet(parquet_path, index=False)
    print(f"\n[BC_GEN] Saved: {parquet_path}  ({n_rows} rows)")

    # Compute action distribution
    act_dist = {}
    if "action_label" in df.columns:
        for label, cnt in df["action_label"].value_counts().items():
            act_dist[label] = round(cnt / n_rows, 4)

    # Save metadata
    metadata = {
        "schema_version":         1,
        "teacher_version":        TEACHER_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "obs_dim":                OBS_DIM,
        "n_teacher_features":     len(TEACHER_FEATURE_NAMES),
        "teacher_feature_names":  TEACHER_FEATURE_NAMES,
        "parameters":             {k: v for k, v in vars(params).items()},
        "dataset_ids":            dataset_ids,
        "n_rows":                 n_rows,
        "action_distribution":    act_dist,
        "generated_at":           datetime.now().isoformat(),
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    print(f"[BC_GEN] Saved: {metadata_path}")

    return parquet_path, metadata_path


def main():
    parser = argparse.ArgumentParser(description="Generate BC dataset from Teacher Policy V1")
    parser.add_argument("--datasets",  nargs="+", default=["stage2_train", "stage2_eval"],
                        help="Dataset IDs to replay")
    parser.add_argument("--episodes",  type=int, default=1,
                        help="Episodes per dataset")
    parser.add_argument("--server",    default="localhost:50051")
    parser.add_argument("--output",    default=OUTPUT_DIR)
    parser.add_argument("--model",     default=None,
                        help="Optional: path to .zip PPO model for side-by-side comparison")
    args = parser.parse_args()

    # Optionally load a trained model for comparison
    model = None
    if args.model:
        from stable_baselines3 import PPO
        model = PPO.load(args.model)
        print(f"[BC_GEN] Loaded comparison model: {args.model}")

    print(f"[BC_GEN] Generating BC dataset...")
    print(f"  datasets:  {args.datasets}")
    print(f"  episodes:  {args.episodes} per dataset")
    print(f"  server:    {args.server}")

    df = generate_dataset(
        dataset_ids=args.datasets,
        episodes_per_dataset=args.episodes,
        server_addr=args.server,
        params=TEACHER_PARAMS,
        model=model,
    )

    if len(df) == 0:
        print("[BC_GEN] ERROR: zero rows generated. Check server connection.")
        sys.exit(1)

    parquet_path, metadata_path = save_dataset(df, TEACHER_PARAMS, args.datasets, args.output)

    # Summary
    print("\n=== BC Dataset Summary ===")
    print(f"  Total rows:     {len(df)}")
    if "action_label" in df.columns:
        print(f"  Action dist:")
        for label, cnt in df["action_label"].value_counts().items():
            print(f"    {label:15s}: {cnt:6d}  ({cnt/len(df)*100:.1f}%)")
    print(f"\n  Files:")
    print(f"    {parquet_path}")
    print(f"    {metadata_path}")


if __name__ == "__main__":
    main()
