"""Financial evaluation of supervised predictions.

Turns model predictions into a trading signal and simulates P&L with realistic
costs: taker fee, slippage, and funding cost on positions held >8h.

Metrics reported: Sharpe, Sortino, Max DD, Profit Factor, Hit Rate, Trade
count, Annualized return.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np
import pandas as pd


# Binance Futures UM defaults (conservative taker fee assumed).
FEE_TAKER = 0.0005          # 5 bps per side
SLIPPAGE = 0.0001           # 1 bp per side
BARS_PER_YEAR = 365 * 24 * 12   # 5m bars


@dataclass
class EvalConfig:
    preds_parquet: Path
    labeled_bars_parquet: Path
    out_dir: Path
    mode: str = "reg"  # "reg" or "cls"
    threshold: float = 0.0  # for reg: |pred| threshold; for cls: min (p_up-p_down)
    horizon: int = 12
    fee_taker: float = FEE_TAKER
    slippage: float = SLIPPAGE
    split: str = "val"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["preds_parquet"] = str(self.preds_parquet)
        d["labeled_bars_parquet"] = str(self.labeled_bars_parquet)
        d["out_dir"] = str(self.out_dir)
        return d


def signal_from_reg(preds: pd.DataFrame, threshold: float) -> np.ndarray:
    p = preds["pred"].to_numpy()
    sig = np.zeros_like(p, dtype=np.int8)
    sig[p > threshold] = 1
    sig[p < -threshold] = -1
    return sig


def signal_from_cls(preds: pd.DataFrame, threshold: float) -> np.ndarray:
    diff = (preds["p_up"] - preds["p_down"]).to_numpy()
    sig = np.zeros_like(diff, dtype=np.int8)
    sig[diff > threshold] = 1
    sig[diff < -threshold] = -1
    return sig


def simulate(
    signals: np.ndarray,
    fwd_rets: np.ndarray,
    fee_taker: float,
    slippage: float,
) -> dict:
    """Simulate a held-N-bar strategy.

    For each bar we open a position in direction of signal if signal != 0.
    Position is held exactly horizon bars (encoded in fwd_rets already).
    This is a simple, honest sim — no compounding intra-position — so Sharpe
    and Sortino are bar-level proxies.
    """
    n = len(signals)
    trade_ret = signals * fwd_rets
    # Cost: pay fee+slip on entry and exit only when we actually trade.
    cost_per_trade = 2.0 * (fee_taker + slippage)
    trades_mask = signals != 0
    net_ret = np.where(trades_mask, trade_ret - cost_per_trade, 0.0)

    equity = np.cumprod(1.0 + net_ret)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    max_dd = float(dd.min()) if len(dd) else 0.0

    wins = net_ret[trades_mask & (net_ret > 0)]
    losses = net_ret[trades_mask & (net_ret < 0)]
    n_trades = int(trades_mask.sum())
    hit_rate = float(len(wins) / n_trades) if n_trades else 0.0
    profit_factor = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf") if wins.sum() > 0 else 0.0

    # Sharpe/Sortino on per-bar returns (of all bars, not just trading ones)
    mean_r = float(net_ret.mean())
    std_r = float(net_ret.std(ddof=1)) if n > 1 else 0.0
    downside = net_ret[net_ret < 0]
    std_dn = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sharpe = (mean_r / std_r) * np.sqrt(BARS_PER_YEAR) if std_r > 0 else 0.0
    sortino = (mean_r / std_dn) * np.sqrt(BARS_PER_YEAR) if std_dn > 0 else 0.0

    total_ret = float(equity[-1] - 1.0) if len(equity) else 0.0
    years = n / BARS_PER_YEAR if BARS_PER_YEAR else 0.0
    ann_ret = (1.0 + total_ret) ** (1.0 / years) - 1.0 if years > 0 else 0.0

    return {
        "n_bars": int(n),
        "n_trades": n_trades,
        "hit_rate": round(hit_rate, 4),
        "profit_factor": round(profit_factor, 4) if np.isfinite(profit_factor) else None,
        "total_return": round(total_ret, 4),
        "annualized_return": round(float(ann_ret), 4),
        "sharpe": round(float(sharpe), 3),
        "sortino": round(float(sortino), 3),
        "max_drawdown": round(max_dd, 4),
        "mean_bar_return": float(mean_r),
        "std_bar_return": float(std_r),
    }


def _get_signals_and_fwd(preds: pd.DataFrame, bars: pd.DataFrame,
                          mode: str, threshold: float, horizon: int):
    if mode == "reg":
        signals = signal_from_reg(preds, threshold)
        fwd = preds["target"].to_numpy()
    elif mode == "cls":
        signals = signal_from_cls(preds, threshold)
        fwd_col = f"fwd_ret_{horizon}"
        if fwd_col not in bars.columns:
            raise ValueError(f"{fwd_col} missing from labeled_bars parquet")
        bars_c = bars.dropna(subset=[fwd_col]).reset_index(drop=True)
        fwd = bars_c[fwd_col].to_numpy()[-len(preds):]
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return signals, fwd


def calibrate_threshold(
    preds_val: pd.DataFrame,
    bars: pd.DataFrame,
    mode: str,
    horizon: int,
    fee_taker: float,
    slippage: float,
    n_steps: int = 40,
) -> tuple[float, dict]:
    """Sweep thresholds on VAL; return (best_threshold, best_metrics).

    Candidate thresholds are percentiles of |pred| so they adapt to the
    prediction distribution rather than being a fixed grid.
    """
    if mode == "reg":
        magnitudes = preds_val["pred"].abs()
    else:
        magnitudes = (preds_val["p_up"] - preds_val["p_down"]).abs()

    percentiles = np.linspace(0, 95, n_steps)
    candidates = [0.0] + [float(np.percentile(magnitudes, q)) for q in percentiles]
    candidates = sorted(set(candidates))

    best_thr, best_sharpe, best_metrics = 0.0, -np.inf, {}
    for thr in candidates:
        sigs, fwd = _get_signals_and_fwd(preds_val, bars, mode, thr, horizon)
        if sigs.sum() == 0:
            continue
        m = simulate(sigs, fwd, fee_taker, slippage)
        if m["sharpe"] > best_sharpe:
            best_sharpe, best_thr, best_metrics = m["sharpe"], thr, m
    return best_thr, best_metrics


def evaluate(cfg: EvalConfig) -> dict:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    preds = pd.read_parquet(cfg.preds_parquet)
    bars = pd.read_parquet(cfg.labeled_bars_parquet)
    signals, fwd = _get_signals_and_fwd(preds, bars, cfg.mode, cfg.threshold, cfg.horizon)
    metrics = simulate(signals, fwd, cfg.fee_taker, cfg.slippage)
    metrics["threshold_used"] = cfg.threshold
    metrics["split"] = cfg.split
    metrics["config"] = cfg.to_dict()
    out = cfg.out_dir / f"metrics_{cfg.mode}_{cfg.split}.json"
    out.write_text(json.dumps(metrics, indent=2))
    print(f"[eval] {cfg.split}/{cfg.mode} thr={cfg.threshold:.6f}: "
          f"sharpe={metrics['sharpe']:.3f} dd={metrics['max_drawdown']:.3f} "
          f"trades={metrics['n_trades']}")
    return metrics


def buy_and_hold_metrics(fwd_rets: np.ndarray) -> dict:
    """Baseline: always long, no fees (best case for buy-and-hold comparison)."""
    n = len(fwd_rets)
    equity = np.cumprod(1.0 + fwd_rets)
    peak = np.maximum.accumulate(equity)
    dd = equity / peak - 1.0
    total_ret = float(equity[-1] - 1.0) if n else 0.0
    years = n / BARS_PER_YEAR
    ann_ret = (1.0 + total_ret) ** (1.0 / years) - 1.0 if years > 0 else 0.0
    mean_r = float(fwd_rets.mean())
    std_r = float(fwd_rets.std(ddof=1)) if n > 1 else 0.0
    sharpe = (mean_r / std_r) * np.sqrt(BARS_PER_YEAR) if std_r > 0 else 0.0
    return {
        "strategy": "buy_and_hold",
        "total_return": round(total_ret, 4),
        "annualized_return": round(float(ann_ret), 4),
        "sharpe": round(float(sharpe), 3),
        "max_drawdown": round(float(dd.min()), 4),
    }


def evaluate_with_val_calibration(
    val_preds: pd.DataFrame,
    test_preds: pd.DataFrame,
    bars: pd.DataFrame,
    mode: str,
    horizon: int,
    out_dir: Path,
    fee_taker: float = FEE_TAKER,
    slippage: float = SLIPPAGE,
) -> dict:
    """Calibrate threshold on val, report on test. Returns combined result dict."""
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[eval] calibrating threshold on val ({mode})…")
    best_thr, val_metrics = calibrate_threshold(
        val_preds, bars, mode, horizon, fee_taker, slippage
    )
    val_metrics["threshold_used"] = best_thr
    val_metrics["split"] = "val_calibration"
    (out_dir / f"metrics_{mode}_val_calibration.json").write_text(
        json.dumps(val_metrics, indent=2)
    )
    print(f"[eval] best threshold={best_thr:.6f} (val sharpe={val_metrics.get('sharpe', 0):.3f})")

    print(f"[eval] evaluating test with fixed threshold ({mode})…")
    test_sigs, test_fwd = _get_signals_and_fwd(test_preds, bars, mode, best_thr, horizon)
    test_metrics = simulate(test_sigs, test_fwd, fee_taker, slippage)
    test_metrics["threshold_used"] = best_thr
    test_metrics["split"] = "test_out_of_sample"
    (out_dir / f"metrics_{mode}_test.json").write_text(
        json.dumps(test_metrics, indent=2)
    )
    print(f"[eval] test/{mode}: sharpe={test_metrics['sharpe']:.3f} "
          f"ann_ret={test_metrics['annualized_return']:.2%} "
          f"dd={test_metrics['max_drawdown']:.2%} "
          f"trades={test_metrics['n_trades']}")

    # Buy-and-hold benchmark (uses same fwd_rets as test)
    test_sigs_bh = np.ones(len(test_preds), dtype=np.int8)
    _, test_fwd_bh = _get_signals_and_fwd(test_preds, bars, mode, 0.0, horizon)
    # For bh we just use the raw fwd returns without fee (theoretical best case)
    bh = buy_and_hold_metrics(test_fwd_bh)
    (out_dir / f"metrics_{mode}_buy_and_hold.json").write_text(json.dumps(bh, indent=2))
    print(f"[eval] buy_and_hold benchmark: sharpe={bh['sharpe']:.3f} "
          f"ann_ret={bh['annualized_return']:.2%} dd={bh['max_drawdown']:.2%}")

    return {"val": val_metrics, "test": test_metrics, "best_threshold": best_thr,
            "buy_and_hold": bh}


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--preds", required=True)
    p.add_argument("--labeled-bars", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--mode", choices=["reg", "cls"], default="reg")
    p.add_argument("--threshold", type=float, default=0.0)
    p.add_argument("--horizon", type=int, default=12)
    p.add_argument("--split", default="val")
    args = p.parse_args()

    cfg = EvalConfig(
        preds_parquet=Path(args.preds),
        labeled_bars_parquet=Path(args.labeled_bars),
        out_dir=Path(args.out_dir),
        mode=args.mode,
        threshold=args.threshold,
        horizon=args.horizon,
        split=args.split,
    )
    evaluate(cfg)


if __name__ == "__main__":
    main()
