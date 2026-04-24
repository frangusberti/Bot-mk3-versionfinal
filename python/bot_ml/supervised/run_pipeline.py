"""End-to-end runner for the supervised pipeline.

Default run reproduces the BTC pivot pilot:
  train: 2023-11, 2023-12, 2024-01
  val:   2024-02
  test:  2024-03
"""
from __future__ import annotations

import argparse
from pathlib import Path

from .build_dataset import BuildConfig, build_dataset
from .train import TrainConfig, train
from .evaluate import EvalConfig, evaluate


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

    print("=== EVALUATE ===")
    labeled_bars = model_dir / "labeled_bars.parquet"
    for split in ["val", "test"]:
        for mode in ["reg", "cls"]:
            preds = model_dir / f"preds_{mode}_{split}.parquet"
            if not preds.exists():
                print(f"[eval] skipping {mode}/{split} — {preds} not found")
                continue
            evaluate(EvalConfig(
                preds_parquet=preds,
                labeled_bars_parquet=labeled_bars,
                out_dir=eval_dir,
                mode=mode,
                threshold=0.0,
                horizon=args.horizon,
                split=split,
            ))

    print(f"\nDone. Run dir: {run_dir}")


if __name__ == "__main__":
    main()
