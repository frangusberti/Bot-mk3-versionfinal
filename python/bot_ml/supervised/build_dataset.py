"""Build a tabular supervised dataset from raw Binance Futures UM files.

Reads zipped monthly files already downloaded under data/raw/binance/futures/um/
and produces a single parquet with 5m OHLCV bars + multi-timeframe features +
order-flow features + funding + open interest.

This module is intentionally independent of the Rust feature engine so the
supervised pipeline can iterate quickly without gRPC or backend dependencies.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


RAW_ROOT = Path(r"C:\Bot mk3\data\raw\binance\futures\um")
DEFAULT_OUT_ROOT = Path(r"C:\Bot mk3\python\runs_train")

BAR_MINUTES = 5
BAR_MS = BAR_MINUTES * 60_000


@dataclass
class BuildConfig:
    symbol: str
    months: list[str]  # e.g. ["2023-11", "2023-12", ...]
    out_dir: Path
    bar_minutes: int = BAR_MINUTES

    def to_dict(self) -> dict:
        d = asdict(self)
        d["out_dir"] = str(self.out_dir)
        return d


# ---------------------------------------------------------------------------
# Raw file readers
# ---------------------------------------------------------------------------

def _read_zip_csv(path: Path, has_header: bool = True) -> pd.DataFrame:
    with zipfile.ZipFile(path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as f:
            data = f.read()
    if has_header:
        return pd.read_csv(io.BytesIO(data))
    return pd.read_csv(io.BytesIO(data), header=None)


_AGG_COLS = ["agg_trade_id", "price", "quantity", "first_trade_id",
             "last_trade_id", "ts_ms", "is_buyer_maker"]

def load_agg_trades(symbol: str, month: str) -> pd.DataFrame:
    path = RAW_ROOT / "aggTrades" / symbol / f"{symbol}-aggTrades-{month}.zip"
    if not path.exists():
        raise FileNotFoundError(path)
    # Older Binance files (pre-2023) have no header row.
    # Detect by checking whether the first field of the first data row is numeric.
    with zipfile.ZipFile(path) as zf:
        with zf.open(zf.namelist()[0]) as f:
            first_line = f.readline().decode().strip()
    has_header = not first_line.split(",")[0].strip().lstrip("-").isdigit()
    df = _read_zip_csv(path, has_header=has_header)
    if not has_header:
        df.columns = _AGG_COLS
    else:
        # Normalize header: rename transact_time -> ts_ms if present
        df = df.rename(columns={"transact_time": "ts_ms"})
    df["ts_ms"] = df["ts_ms"].astype("int64")
    df["price"] = df["price"].astype("float64")
    df["quantity"] = df["quantity"].astype("float64")
    df["is_buyer_maker"] = df["is_buyer_maker"].astype(bool)
    return df[["ts_ms", "price", "quantity", "is_buyer_maker"]]


def load_funding_rate(symbol: str, month: str) -> pd.DataFrame:
    path = RAW_ROOT / "fundingRate" / symbol / f"{symbol}-fundingRate-{month}.zip"
    if not path.exists():
        return pd.DataFrame(columns=["ts_ms", "funding_rate"])
    df = _read_zip_csv(path, has_header=True)
    ts_col = "calc_time" if "calc_time" in df.columns else "funding_time"
    rate_col = "funding_rate" if "funding_rate" in df.columns else "last_funding_rate"
    df = df.rename(columns={ts_col: "ts_ms", rate_col: "funding_rate"})
    df["ts_ms"] = df["ts_ms"].astype("int64")
    df["funding_rate"] = df["funding_rate"].astype("float64")
    return df[["ts_ms", "funding_rate"]].sort_values("ts_ms").reset_index(drop=True)


def load_metrics(symbol: str, month: str) -> pd.DataFrame:
    """Open interest + long/short ratios. Daily files."""
    root = RAW_ROOT / "metrics" / symbol
    if not root.exists():
        return pd.DataFrame(columns=["ts_ms", "open_interest", "oi_usd"])
    frames = []
    for p in sorted(root.glob(f"{symbol}-metrics-{month}-*.zip")):
        try:
            df = _read_zip_csv(p, has_header=True)
        except Exception:
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["ts_ms", "open_interest", "oi_usd"])
    df = pd.concat(frames, ignore_index=True)
    ts_col = "create_time" if "create_time" in df.columns else "timestamp"
    df["ts_ms"] = pd.to_datetime(df[ts_col]).astype("int64") // 1_000_000
    out_cols = {"ts_ms": "ts_ms"}
    if "sum_open_interest" in df.columns:
        out_cols["sum_open_interest"] = "open_interest"
    if "sum_open_interest_value" in df.columns:
        out_cols["sum_open_interest_value"] = "oi_usd"
    df = df.rename(columns=out_cols)
    keep = [c for c in ["ts_ms", "open_interest", "oi_usd"] if c in df.columns]
    return df[keep].sort_values("ts_ms").reset_index(drop=True)


# ---------------------------------------------------------------------------
# OHLCV + order flow aggregation from aggTrades
# ---------------------------------------------------------------------------

def build_ohlcv_and_flow(trades: pd.DataFrame, bar_ms: int) -> pd.DataFrame:
    """Aggregate trades into OHLCV + buy/sell volume + trade count per bar."""
    if trades.empty:
        return pd.DataFrame()

    t = trades.copy()
    t["bar_ts"] = (t["ts_ms"] // bar_ms) * bar_ms
    t["notional"] = t["price"] * t["quantity"]
    # is_buyer_maker == True  ⇒ aggressor was seller (market sell)
    # is_buyer_maker == False ⇒ aggressor was buyer  (market buy)
    t["buy_vol"] = np.where(~t["is_buyer_maker"], t["quantity"], 0.0)
    t["sell_vol"] = np.where(t["is_buyer_maker"], t["quantity"], 0.0)
    t["buy_notional"] = np.where(~t["is_buyer_maker"], t["notional"], 0.0)
    t["sell_notional"] = np.where(t["is_buyer_maker"], t["notional"], 0.0)

    g = t.groupby("bar_ts", sort=True)
    bars = pd.DataFrame({
        "open": g["price"].first(),
        "high": g["price"].max(),
        "low": g["price"].min(),
        "close": g["price"].last(),
        "volume": g["quantity"].sum(),
        "notional": g["notional"].sum(),
        "trades": g.size(),
        "buy_vol": g["buy_vol"].sum(),
        "sell_vol": g["sell_vol"].sum(),
        "buy_notional": g["buy_notional"].sum(),
        "sell_notional": g["sell_notional"].sum(),
    }).reset_index().rename(columns={"bar_ts": "ts_ms"})
    return bars


def reindex_continuous(bars: pd.DataFrame, bar_ms: int) -> pd.DataFrame:
    """Fill missing bars with forward-filled close and zero volumes."""
    if bars.empty:
        return bars
    full_idx = np.arange(int(bars["ts_ms"].min()), int(bars["ts_ms"].max()) + bar_ms, bar_ms)
    bars = bars.set_index("ts_ms").reindex(full_idx)
    bars["close"] = bars["close"].ffill()
    bars[["open", "high", "low"]] = bars[["open", "high", "low"]].fillna(bars["close"].values[:, None] if False else bars[["open", "high", "low"]])
    for c in ["open", "high", "low"]:
        bars[c] = bars[c].fillna(bars["close"])
    for c in ["volume", "notional", "trades", "buy_vol", "sell_vol", "buy_notional", "sell_notional"]:
        bars[c] = bars[c].fillna(0.0)
    bars.index.name = "ts_ms"
    return bars.reset_index()


# ---------------------------------------------------------------------------
# Feature engineering (multi-timeframe)
# ---------------------------------------------------------------------------

def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
    roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = roll_up / roll_down.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def add_features(bars: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    c = df["close"]

    # Log returns at multiple horizons (in 5m bars)
    for n in [1, 3, 6, 12, 24, 48, 96]:
        df[f"ret_{n}"] = np.log(c).diff(n)

    # Realized volatility (rolling std of 1-bar log returns)
    r1 = df["ret_1"]
    for w in [12, 24, 48, 96]:
        df[f"rv_{w}"] = r1.rolling(w).std()

    # RSI
    for p in [14, 28]:
        df[f"rsi_{p}"] = _rsi(c, p)

    # ATR (as % of price)
    for p in [14, 48]:
        atr = _atr(df["high"], df["low"], c, p)
        df[f"atr_pct_{p}"] = atr / c

    # MACD-like
    ema_fast = c.ewm(span=12, adjust=False).mean()
    ema_slow = c.ewm(span=26, adjust=False).mean()
    df["macd"] = (ema_fast - ema_slow) / c
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # Position within rolling range (0=low, 1=high)
    for w in [12, 48, 288]:  # 1h, 4h, 1d
        hi = df["high"].rolling(w).max()
        lo = df["low"].rolling(w).min()
        df[f"pos_in_range_{w}"] = (c - lo) / (hi - lo).replace(0.0, np.nan)

    # Order flow imbalance
    vol = df["volume"].replace(0.0, np.nan)
    df["ofi_bar"] = (df["buy_vol"] - df["sell_vol"]) / vol
    notional = df["notional"].replace(0.0, np.nan)
    df["ofi_notional"] = (df["buy_notional"] - df["sell_notional"]) / notional
    for w in [12, 48, 96]:
        df[f"ofi_mean_{w}"] = df["ofi_bar"].rolling(w).mean()
    df["trade_intensity_48"] = df["trades"].rolling(48).sum() / df["trades"].rolling(288).sum()

    # Volume z-score
    for w in [96, 288]:
        vmean = df["volume"].rolling(w).mean()
        vstd = df["volume"].rolling(w).std()
        df[f"vol_z_{w}"] = (df["volume"] - vmean) / vstd.replace(0.0, np.nan)

    # Higher timeframe context (resample-align)
    for tf_bars, tag in [(3, "15m"), (12, "1h"), (48, "4h"), (288, "1d")]:
        df[f"ret_{tag}"] = np.log(c / c.shift(tf_bars))
        df[f"rv_{tag}"] = df["ret_1"].rolling(tf_bars).std() * np.sqrt(tf_bars)
        ema_hf = c.ewm(span=tf_bars * 3, adjust=False).mean()
        df[f"slope_{tag}"] = (c - ema_hf) / ema_hf

    # Long-window features — important for 4h+ horizon prediction
    # Trend strength: how far price is from its slow mean
    for span in [288, 576, 2016]:   # 1d, 2d, 7d in 5m bars
        ema = c.ewm(span=span, adjust=False).mean()
        df[f"price_vs_ema_{span}"] = (c - ema) / ema

    # Rolling max drawdown from peak (captures regime exhaustion)
    for w in [288, 2016]:  # 1d, 7d
        roll_max = c.rolling(w).max()
        df[f"dd_from_peak_{w}"] = (c - roll_max) / roll_max

    # Realized volatility at longer horizons
    for w in [288, 576, 2016]:
        df[f"rv_{w}"] = r1.rolling(w).std()

    # RSI at longer period
    df["rsi_96"] = _rsi(c, 96)

    # OI change at longer horizon (if available)
    if "open_interest" in df.columns:
        df["oi_ret_288"] = np.log(df["open_interest"] / df["open_interest"].shift(288))

    # Funding rate cumulative (sum over last 3 funding periods ≈ 24h)
    df["funding_cumsum_48"] = df["funding_rate"].rolling(48, min_periods=1).sum()

    return df


# ---------------------------------------------------------------------------
# Merge funding + OI into 5m bars
# ---------------------------------------------------------------------------

def merge_funding_oi(bars: pd.DataFrame, funding: pd.DataFrame, oi: pd.DataFrame) -> pd.DataFrame:
    df = bars.copy()
    df = df.sort_values("ts_ms").reset_index(drop=True)

    if not funding.empty:
        f = funding.sort_values("ts_ms").reset_index(drop=True)
        df = pd.merge_asof(df, f, on="ts_ms", direction="backward")
        df["funding_rate"] = df["funding_rate"].ffill().fillna(0.0)
    else:
        df["funding_rate"] = 0.0

    if not oi.empty:
        o = oi.sort_values("ts_ms").reset_index(drop=True)
        df = pd.merge_asof(df, o, on="ts_ms", direction="backward")
        if "open_interest" in df.columns:
            df["open_interest"] = df["open_interest"].ffill()
            df["oi_ret_12"] = np.log(df["open_interest"] / df["open_interest"].shift(12))
            df["oi_ret_48"] = np.log(df["open_interest"] / df["open_interest"].shift(48))
    return df


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_for_month(symbol: str, month: str, bar_ms: int) -> pd.DataFrame:
    trades = load_agg_trades(symbol, month)
    bars = build_ohlcv_and_flow(trades, bar_ms)
    bars = reindex_continuous(bars, bar_ms)
    bars["symbol"] = symbol
    bars["month"] = month
    return bars


def build_dataset(cfg: BuildConfig) -> Path:
    cfg.out_dir.mkdir(parents=True, exist_ok=True)
    bar_ms = cfg.bar_minutes * 60_000

    all_bars = []
    for month in cfg.months:
        print(f"[build] {cfg.symbol} {month} — aggregating aggTrades…")
        bars_m = build_for_month(cfg.symbol, month, bar_ms)
        all_bars.append(bars_m)
    bars = pd.concat(all_bars, ignore_index=True).sort_values("ts_ms").reset_index(drop=True)

    print("[build] merging funding + open interest…")
    funding_frames = [load_funding_rate(cfg.symbol, m) for m in cfg.months]
    funding = pd.concat([f for f in funding_frames if not f.empty], ignore_index=True) if any(not f.empty for f in funding_frames) else pd.DataFrame(columns=["ts_ms", "funding_rate"])
    oi_frames = [load_metrics(cfg.symbol, m) for m in cfg.months]
    oi = pd.concat([o for o in oi_frames if not o.empty], ignore_index=True) if any(not o.empty for o in oi_frames) else pd.DataFrame(columns=["ts_ms", "open_interest"])

    bars = merge_funding_oi(bars, funding, oi)

    print("[build] computing features…")
    bars = add_features(bars)

    out_path = cfg.out_dir / "bars_features.parquet"
    bars.to_parquet(out_path, index=False)

    manifest = {
        "config": cfg.to_dict(),
        "rows": len(bars),
        "ts_ms_start": int(bars["ts_ms"].min()),
        "ts_ms_end": int(bars["ts_ms"].max()),
        "columns": list(bars.columns),
    }
    (cfg.out_dir / "build_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"[build] wrote {out_path} ({len(bars)} rows, {len(bars.columns)} cols)")
    return out_path


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--months", nargs="+", required=True,
                   help="YYYY-MM list, e.g. 2023-11 2023-12 2024-01 2024-02 2024-03")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--bar-minutes", type=int, default=BAR_MINUTES)
    args = p.parse_args()

    cfg = BuildConfig(
        symbol=args.symbol,
        months=args.months,
        out_dir=Path(args.out_dir),
        bar_minutes=args.bar_minutes,
    )
    build_dataset(cfg)


if __name__ == "__main__":
    main()
