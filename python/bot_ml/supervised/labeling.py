"""Labeling for supervised training.

Supported schemes:
- Forward return (regression target): log return over H bars ahead.
- Triple-barrier (López de Prado): {-1, 0, +1} depending on which barrier is hit
  first — upper (k*ATR), lower (-k*ATR) or vertical (H bars).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_forward_returns(df: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    out = df.copy()
    c = out["close"]
    for h in horizons:
        out[f"fwd_ret_{h}"] = np.log(c.shift(-h) / c)
    return out


def triple_barrier_labels(
    df: pd.DataFrame,
    horizon: int,
    atr_col: str = "atr_pct_48",
    k_up: float = 1.5,
    k_dn: float = 1.5,
) -> pd.Series:
    """Return a series of {-1, 0, +1} labels.

    For each bar t, we define barriers relative to close[t]:
        upper = close[t] * (1 + k_up * atr_pct[t])
        lower = close[t] * (1 - k_dn * atr_pct[t])
    Over the next `horizon` bars we check which barrier is hit first using the
    bar's high/low. If neither is hit, label = sign(close[t+horizon] - close[t])
    when it exceeds half the barrier; otherwise 0.
    """
    c = df["close"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    atr = df[atr_col].to_numpy()
    n = len(df)
    labels = np.zeros(n, dtype=np.int8)

    for t in range(n - horizon):
        a = atr[t]
        if not np.isfinite(a) or a <= 0:
            labels[t] = 0
            continue
        up = c[t] * (1.0 + k_up * a)
        dn = c[t] * (1.0 - k_dn * a)
        hit = 0
        for s in range(1, horizon + 1):
            if h[t + s] >= up:
                hit = 1
                break
            if l[t + s] <= dn:
                hit = -1
                break
        if hit == 0:
            diff = c[t + horizon] - c[t]
            threshold = 0.5 * k_up * a * c[t]
            if diff > threshold:
                hit = 1
            elif diff < -threshold:
                hit = -1
        labels[t] = hit
    # Trailing bars without full horizon: leave as 0, will be filtered.
    labels[n - horizon:] = 0
    out = pd.Series(labels, index=df.index, name=f"tb_label_{horizon}")
    return out


def add_triple_barrier(
    df: pd.DataFrame,
    horizon: int = 12,
    atr_col: str = "atr_pct_48",
    k_up: float = 1.5,
    k_dn: float = 1.5,
) -> pd.DataFrame:
    out = df.copy()
    out[f"tb_label_{horizon}"] = triple_barrier_labels(out, horizon, atr_col, k_up, k_dn)
    return out
