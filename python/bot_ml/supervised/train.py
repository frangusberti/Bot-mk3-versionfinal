"""Train LightGBM models on the supervised dataset with walk-forward splits.

Two heads are trained:
  - reg: regression on fwd_ret_H (default H=12 = 1h forward)
  - cls: multiclass on triple-barrier label {-1,0,+1}

Splits are strictly temporal with a purge gap to avoid leakage from
overlapping forward-looking labels.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import lightgbm as lgb
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "lightgbm is required. Install with: py -m pip install lightgbm pyarrow"
    ) from e


# Columns that are never features (identifiers, raw price, labels, etc.)
NON_FEATURE_COLS = {
    "ts_ms", "symbol", "month",
    "open", "high", "low", "close", "volume", "notional", "trades",
    "buy_vol", "sell_vol", "buy_notional", "sell_notional",
    "open_interest", "oi_usd",
}


@dataclass
class TrainConfig:
    dataset_path: Path
    out_dir: Path
    horizon: int = 12
    train_months: list[str] = None
    val_months: list[str] = None
    test_months: list[str] = None
    purge_bars: int = 12  # >= horizon to prevent label leakage between splits
    num_boost_round: int = 2000
    early_stopping: int = 100

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dataset_path"] = str(self.dataset_path)
        d["out_dir"] = str(self.out_dir)
        return d


def select_features(df: pd.DataFrame) -> list[str]:
    cols = []
    for c in df.columns:
        if c in NON_FEATURE_COLS:
            continue
        if c.startswith("fwd_ret_") or c.startswith("tb_label_"):
            continue
        if pd.api.types.is_numeric_dtype(df[c]):
            cols.append(c)
    return cols


def split_by_month(df: pd.DataFrame, train_months, val_months, test_months, purge_bars: int):
    tr = df[df["month"].isin(train_months)].copy()
    va = df[df["month"].isin(val_months)].copy()
    te = df[df["month"].isin(test_months)].copy()
    # Purge tail of train and head of val/test to avoid overlapping labels.
    if purge_bars > 0:
        if len(tr) > purge_bars:
            tr = tr.iloc[:-purge_bars]
        if len(va) > purge_bars:
            va = va.iloc[purge_bars:]
        if len(te) > purge_bars:
            te = te.iloc[purge_bars:]
    return tr, va, te


def _clean(df: pd.DataFrame, feat: list[str], target: str) -> pd.DataFrame:
    d = df[feat + [target]].copy()
    d = d.replace([np.inf, -np.inf], np.nan)
    d = d.dropna(subset=[target] + feat)
    return d


def train_regression(tr, va, te, feat, target, params_extra, cfg: TrainConfig):
    tr_c = _clean(tr, feat, target)
    va_c = _clean(va, feat, target)
    dtrain = lgb.Dataset(tr_c[feat], label=tr_c[target])
    dval = lgb.Dataset(va_c[feat], label=va_c[target], reference=dtrain)
    params = {
        "objective": "regression",
        "metric": "rmse",
        "learning_rate": 0.02,
        "num_leaves": 63,
        "min_data_in_leaf": 100,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l1": 0.1,
        "lambda_l2": 1.0,
        "verbose": -1,
    }
    params.update(params_extra or {})
    model = lgb.train(
        params,
        dtrain,
        num_boost_round=cfg.num_boost_round,
        valid_sets=[dtrain, dval],
        valid_names=["train", "val"],
        callbacks=[lgb.early_stopping(cfg.early_stopping, verbose=True),
                   lgb.log_evaluation(period=100)],
    )
    preds = {}
    for name, split in [("train", tr_c), ("val", va_c), ("test", _clean(te, feat, target))]:
        if len(split) == 0:
            preds[name] = pd.DataFrame()
            continue
        p = model.predict(split[feat], num_iteration=model.best_iteration)
        preds[name] = pd.DataFrame({
            "pred": p,
            "target": split[target].to_numpy(),
        })
    return model, preds


def train_classification(tr, va, te, feat, target, cfg: TrainConfig):
    # Remap {-1,0,+1} -> {0,1,2} for LightGBM multiclass
    def remap(df):
        d = df.copy()
        d = d.replace([np.inf, -np.inf], np.nan).dropna(subset=feat + [target])
        d[target] = d[target].astype(int) + 1
        return d
    tr_c = remap(tr)
    va_c = remap(va)
    te_c = remap(te)
    dtrain = lgb.Dataset(tr_c[feat], label=tr_c[target])
    dval = lgb.Dataset(va_c[feat], label=va_c[target], reference=dtrain)
    params = {
        "objective": "multiclass",
        "num_class": 3,
        "metric": "multi_logloss",
        "learning_rate": 0.02,
        "num_leaves": 63,
        "min_data_in_leaf": 100,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "lambda_l1": 0.1,
        "lambda_l2": 1.0,
        "verbose": -1,
    }
    model = lgb.train(
        params,
        dtrain,
        num_boost_round=cfg.num_boost_round,
        valid_sets=[dtrain, dval],
        valid_names=["train", "val"],
        callbacks=[lgb.early_stopping(cfg.early_stopping, verbose=True),
                   lgb.log_evaluation(period=100)],
    )
    preds = {}
    for name, split in [("train", tr_c), ("val", va_c), ("test", te_c)]:
        if len(split) == 0:
            preds[name] = pd.DataFrame()
            continue
        proba = model.predict(split[feat], num_iteration=model.best_iteration)
        preds[name] = pd.DataFrame({
            "p_down": proba[:, 0],
            "p_flat": proba[:, 1],
            "p_up": proba[:, 2],
            "target": split[target].to_numpy() - 1,  # back to {-1,0,+1}
        })
    return model, preds


def train(cfg: TrainConfig):
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_parquet(cfg.dataset_path)
    print(f"[train] loaded {len(df)} rows, {len(df.columns)} cols from {cfg.dataset_path}")

    # Labels
    from .labeling import add_forward_returns, add_triple_barrier
    df = add_forward_returns(df, [3, cfg.horizon, 48])
    df = add_triple_barrier(df, horizon=cfg.horizon)

    reg_target = f"fwd_ret_{cfg.horizon}"
    cls_target = f"tb_label_{cfg.horizon}"

    tr, va, te = split_by_month(df, cfg.train_months, cfg.val_months, cfg.test_months, cfg.purge_bars)
    print(f"[train] split sizes: train={len(tr)} val={len(va)} test={len(te)}")

    feat = select_features(df)
    print(f"[train] {len(feat)} features selected")

    # --- Regression ---
    print("[train] --- regression head ---")
    reg_model, reg_preds = train_regression(tr, va, te, feat, reg_target, None, cfg)
    reg_model.save_model(str(cfg.out_dir / "lgbm_regression.txt"))
    for name, p in reg_preds.items():
        if len(p):
            p.to_parquet(cfg.out_dir / f"preds_reg_{name}.parquet", index=False)

    # --- Classification (triple-barrier) ---
    print("[train] --- triple-barrier classification head ---")
    cls_model, cls_preds = train_classification(tr, va, te, feat, cls_target, cfg)
    cls_model.save_model(str(cfg.out_dir / "lgbm_classification.txt"))
    for name, p in cls_preds.items():
        if len(p):
            p.to_parquet(cfg.out_dir / f"preds_cls_{name}.parquet", index=False)

    # Feature importance
    fi = pd.DataFrame({
        "feature": reg_model.feature_name(),
        "gain_reg": reg_model.feature_importance(importance_type="gain"),
        "gain_cls": cls_model.feature_importance(importance_type="gain"),
    }).sort_values("gain_reg", ascending=False)
    fi.to_csv(cfg.out_dir / "feature_importance.csv", index=False)

    # IC (Information Coefficient) — Spearman rank correlation of pred vs actual
    ic_stats = {}
    for split_name, split_preds in reg_preds.items():
        if len(split_preds) < 10:
            continue
        ic = float(split_preds[["pred", "target"]].corr(method="spearman").iloc[0, 1])
        ic_stats[f"ic_spearman_{split_name}"] = round(ic, 4)
        # IC > 0.05 is considered meaningful in practice
    print(f"[train] IC stats: {ic_stats}")

    manifest = {
        "config": cfg.to_dict(),
        "features": feat,
        "regression_best_iter": reg_model.best_iteration,
        "classification_best_iter": cls_model.best_iteration,
        **ic_stats,
    }
    (cfg.out_dir / "train_manifest.json").write_text(json.dumps(manifest, indent=2))

    # Save bars+labels for downstream eval (close, ts, preds alignment)
    aux_cols = ["ts_ms", "symbol", "month", "open", "high", "low", "close", "atr_pct_48"]
    aux_cols = [c for c in aux_cols if c in df.columns]
    df[aux_cols + [reg_target, cls_target]].to_parquet(
        cfg.out_dir / "labeled_bars.parquet", index=False
    )
    print(f"[train] done. Artifacts in {cfg.out_dir}")


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--train-months", nargs="+", required=True)
    p.add_argument("--val-months", nargs="+", required=True)
    p.add_argument("--test-months", nargs="+", required=True)
    p.add_argument("--purge-bars", type=int, default=12)
    args = p.parse_args()

    cfg = TrainConfig(
        dataset_path=Path(args.dataset),
        out_dir=Path(args.out_dir),
        horizon=args.horizon,
        train_months=args.train_months,
        val_months=args.val_months,
        test_months=args.test_months,
        purge_bars=args.purge_bars,
    )
    train(cfg)


if __name__ == "__main__":
    main()
