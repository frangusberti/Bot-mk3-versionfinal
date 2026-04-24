"""End-to-end runner for the supervised pipeline.

Default run reproduces the BTC pivot pilot:
  train: 2023-11, 2023-12, 2024-01
  val:   2024-02
  test:  2024-03
"""
from __future__ import annotations

import argparse
from pathlib import Path

import json

import pandas as pd

from .build_dataset import BuildConfig, build_dataset
from .train import TrainConfig, train
from .evaluate import evaluate_with_val_calibration


DEFAULT_RUN_ROOT = Path(r"C:\Bot mk3\python\runs_train")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--run-name", default="supervised_btc_v1")
    p.add_argument("--train-months", nargs="+", default=["2023-11", "2023-12", "2024-01"])
    p.add_argument("--val-months", nargs="+", default=["2024-02"])
    p.add_argument("--test-months", nargs="+", default=["2024-03"])
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--skip-build", action="store_true")
    p.add_argument("--skip-train", action="store_true")
    args = p.parse_args()

    run_dir = DEFAULT_RUN_ROOT / args.run_name
    data_dir = run_dir / "dataset"
    model_dir = run_dir / "models"
    eval_dir = run_dir / "eval"

    all_months = list(args.train_months) + list(args.val_months) + list(args.test_months)

    dataset_path = data_dir / "bars_features.parquet"

    if not args.skip_build:
        print(f"=== BUILD DATASET ({args.symbol}, {len(all_months)} months) ===")
        build_dataset(BuildConfig(
            symbol=args.symbol,
            months=all_months,
            out_dir=data_dir,
        ))
    else:
        print(f"[skip] build; expecting {dataset_path}")

    if not args.skip_train:
        print("=== TRAIN LIGHTGBM ===")
        train(TrainConfig(
            dataset_path=dataset_path,
            out_dir=model_dir,
            horizon=args.horizon,
            train_months=args.train_months,
            val_months=args.val_months,
            test_months=args.test_months,
        ))
    else:
        print(f"[skip] train; expecting artifacts in {model_dir}")

    print("=== EVALUATE (threshold calibrated on val, reported on test) ===")
    labeled_bars = model_dir / "labeled_bars.parquet"
    bars = pd.read_parquet(labeled_bars)
    all_results = {}

    for mode in ["reg", "cls"]:
        val_preds_path = model_dir / f"preds_{mode}_val.parquet"
        test_preds_path = model_dir / f"preds_{mode}_test.parquet"
        if not val_preds_path.exists() or not test_preds_path.exists():
            print(f"[eval] skipping {mode} — preds not found")
            continue
        val_preds = pd.read_parquet(val_preds_path)
        test_preds = pd.read_parquet(test_preds_path)
        results = evaluate_with_val_calibration(
            val_preds=val_preds,
            test_preds=test_preds,
            bars=bars,
            mode=mode,
            horizon=args.horizon,
            out_dir=eval_dir,
        )
        all_results[mode] = results

    # Summary table
    print("\n=== SUMMARY (out-of-sample test) ===")
    print(f"{'mode':<6} {'sharpe':>8} {'sortino':>8} {'ann_ret':>9} {'max_dd':>8} {'trades':>7} {'hit_rate':>9}")
    for mode, res in all_results.items():
        t = res["test"]
        print(f"{mode:<6} {t['sharpe']:>8.3f} {t['sortino']:>8.3f} "
              f"{t['annualized_return']:>8.1%} {t['max_drawdown']:>8.2%} "
              f"{t['n_trades']:>7d} {t['hit_rate']:>8.1%}")
    (eval_dir / "summary.json").write_text(json.dumps(all_results, indent=2))
    print(f"\nDone. Run dir: {run_dir}")


if __name__ == "__main__":
    main()
