"""
teacher_policy.py — Teacher Policy Maker V1

A simple, explainable maker-first policy that produces labeled actions
from the 18-feature safe subset of FeatureRow schema v6.

Used for:
  - Behavior Cloning dataset generation
  - Warm-start of the RL policy network
  - Offline analysis / policy comparison

This policy is NOT a trading system. It is a heuristic label generator
for supervised pre-training. It is intentionally simple and auditable.

Action indices match ACTION_LABELS in rl.rs:
#   0=HOLD  1=POST_BID  2=POST_ASK  3=REPRICE_BID  4=REPRICE_ASK
#   5=CLEAR_QUOTES  6=CLOSE_POSITION
"""

import sys
import os
import json
import numpy as np
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Tuple, List

# FeatureRow field names in obs_vec order (must match schema.rs to_obs_vec)
# These are the raw feature names used to reconstruct a feature dict from obs.
# OBS_DIM = 148 (74 values + 74 masks)
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

def obs_vec_to_feature_dict(obs_vec: np.ndarray) -> dict:
    """
    Convert a 148-dim obs_vec (74 values + 74 masks) back to a named feature dict.
    Masked values (mask=0) are restored as None.
    """
    if len(obs_vec) != 148:
        return {}
    values = obs_vec[:74]
    masks  = obs_vec[74:]
    return {
        name: (float(values[i]) if masks[i] > 0.5 else None)
        for i, name in enumerate(OBS_FEATURE_NAMES)
    }

# ---------------------------------------------------------------------------
# Action constants (aligned with ACTION_LABELS in rl.rs)
# ---------------------------------------------------------------------------
# ACTION_LABELS from rl.rs:
# 0: HOLD, 1: POST_BID, 2: POST_ASK, 3: REPRICE_BID, 4: REPRICE_ASK, 5: CLEAR_QUOTES, 6: CLOSE_POSITION
ACT_HOLD        = 0
ACT_POST_BID    = 1
ACT_POST_ASK    = 2
ACT_REPRICE_BID    = 3
ACT_REPRICE_ASK   = 4
ACT_CLEAR_QUOTES  = 5
ACT_CLOSE_POSITION= 6

ACTION_LABELS = {
    ACT_HOLD:       "HOLD",
    ACT_POST_BID:   "POST_BID",
    ACT_POST_ASK:   "POST_ASK",
    ACT_REPRICE_BID:   "REPRICE_BID",
    ACT_REPRICE_ASK:   "REPRICE_ASK",
    ACT_CLEAR_QUOTES:  "CLEAR_QUOTES",
    ACT_CLOSE_POSITION: "CLOSE_POSITION",
}

# Teacher V1 treats POST_BID == JOIN_BID (no queue position distinction yet)
# V2 will differentiate based on queue depth signal.

# ---------------------------------------------------------------------------
# Teacher V1 Parameters
# All thresholds are tunable. Defaults calibrated for BTCUSDT 1s intervals.
# ---------------------------------------------------------------------------
@dataclass
class TeacherParams:
    # Spread gates
    min_spread_bps: float = 0.01     # Lowered for tight BTCUSDT markets
    max_spread_bps: float = 15.0    
    spread_max_hard: float = 25.0   

    # Regime gates
    shock_threshold: float = 0.60   
    dead_threshold:  float = 0.85   

    # Deterioration gates
    spread_deterioration_threshold: float = 0.75  
    cancel_deterioration_threshold: float = 0.50  

    # Account / risk gates
    drawdown_hard_threshold: float = 0.04  
    emergency_exit_threshold: float = 0.75

    # Volatility
    rv_high: float = 0.30  

    # Decision thresholds
    no_trade_threshold:  float = 0.40  # Lowered for more activity
    bid_min_threshold:   float = 0.55
    ask_min_threshold:   float = 0.55
    cancel_threshold:    float = 0.50

TEACHER_PARAMS = TeacherParams()


# ---------------------------------------------------------------------------
# Feature extraction — 18-feature safe subset from FeatureRow / obs dict
# ---------------------------------------------------------------------------

# The 18 teacher-safe features, in canonical order.
# Names match FeatureRow field names from schema.rs.
TEACHER_FEATURE_NAMES = [
    # Group 1: Spread
    "spread_bps",
    "spread_vs_baseline",
    "spread_deterioration",
    # Group 2: OBI & microstructure
    "obi_top1",
    "obi_top3",
    "microprice_minus_mid_bps",
    "depth_imbalance_top5",
    "obi_persistence_buy",
    "obi_persistence_sell",
    # Group 3: Taker flow
    "trade_imbalance_5s",
    "trade_imbalance_1s",
    # Group 4: Volatility & regime
    "rv_5s",
    "regime_range",
    "regime_shock",
    "regime_dead",
    # Group 5: Absorption
    "price_response_buy_5s",
    "breakout_failure_5s",
    # Group 6: Account state
    "position_flag",
    "current_drawdown_pct",
]

N_TEACHER_FEATURES = len(TEACHER_FEATURE_NAMES)  # 19


def extract_teacher_features(feature_dict: dict) -> np.ndarray:
    """
    Extract the 19-feature teacher subset from a feature dictionary.

    Args:
        feature_dict: dict with keys matching FeatureRow field names,
                      values are float or None.
    Returns:
        np.ndarray of shape (19,), with None → 0.0.
    """
    out = np.zeros(N_TEACHER_FEATURES, dtype=np.float32)
    for i, name in enumerate(TEACHER_FEATURE_NAMES):
        val = feature_dict.get(name, None)
        if val is None or (isinstance(val, float) and np.isnan(val)):
            out[i] = 0.0
        else:
            out[i] = float(val)
    return out


def _g(d: dict, key: str, default: float = 0.0) -> float:
    """Safe get with None/NaN → default."""
    v = d.get(key, None)
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return default
    return float(v)


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def compute_scores(features: dict, params: TeacherParams = TEACHER_PARAMS) -> Dict[str, float]:
    """
    Compute all 5 teacher scores from a feature dict.

    Returns:
        dict with keys: bid, ask, cancel, no_trade, emergency_exit
    """
    # --- Raw feature reads ---
    spread_bps            = _g(features, "spread_bps")
    spread_vs_baseline    = _g(features, "spread_vs_baseline")
    spread_deterioration  = _g(features, "spread_deterioration")
    obi_top1              = _g(features, "obi_top1")
    obi_top3              = _g(features, "obi_top3")
    microprice_mmb        = _g(features, "microprice_minus_mid_bps")
    depth_imbalance       = _g(features, "depth_imbalance_top5")
    obi_pers_buy          = _g(features, "obi_persistence_buy")
    obi_pers_sell         = _g(features, "obi_persistence_sell")
    flow_pers_buy         = _g(features, "flow_persistence_buy",  default=0.0)
    flow_pers_sell        = _g(features, "flow_persistence_sell", default=0.0)
    ti_5s                 = _g(features, "trade_imbalance_5s")
    ti_1s                 = _g(features, "trade_imbalance_1s")
    rv_5s                 = _g(features, "rv_5s")
    regime_range          = _g(features, "regime_range")
    regime_shock          = _g(features, "regime_shock")
    regime_dead           = _g(features, "regime_dead")
    price_resp_buy        = _g(features, "price_response_buy_5s")
    price_resp_sell       = _g(features, "price_response_sell_5s", default=0.0)
    breakout_fail         = _g(features, "breakout_failure_5s")
    position_flag         = _g(features, "position_flag")
    drawdown_pct          = _g(features, "current_drawdown_pct")

    # --- no_trade_score ---
    no_trade = _clip(
        0.30
        + 0.25 * _clip(regime_dead / params.dead_threshold)
        + 0.15 * _clip(spread_deterioration)
        + 0.15 * _clip(spread_bps / params.spread_max_hard)
        + 0.15 * _clip(rv_5s / params.rv_high)
    )

    # --- bid_score ---
    obi_lean_buy  = _clip((obi_top1 + obi_top3 + depth_imbalance) / 3.0, -1.0, 1.0)
    flow_lean_buy = _clip(
        (ti_5s + 0.5 * obi_pers_buy - 0.5 * flow_pers_sell) / 1.5,
        -1.0, 1.0
    )
    # absorption_safe: high buy absorption = risky bid
    abs_safe_buy  = 1.0 - _clip(abs(price_resp_buy) / 100.0)
    spread_ok     = _clip((spread_bps - params.min_spread_bps) / (params.max_spread_bps - params.min_spread_bps))

    bid = _clip(
        0.15 * _clip((obi_lean_buy + 1.0) / 2.0)
        + 0.15 * _clip((flow_lean_buy + 1.0) / 2.0)
        + 0.20 * abs_safe_buy
        + 0.15 * (1.0 - spread_ok)            # prefer spread not too wide
        + 0.15 * _clip(regime_range)
        + 0.10 * _clip(breakout_fail)
        + 0.10 * (1.0 - _clip(regime_shock / 0.5))
    )

    # --- ask_score (symmetric) ---
    obi_lean_sell  = _clip((-obi_top1 - obi_top3 - depth_imbalance) / 3.0, -1.0, 1.0)
    flow_lean_sell = _clip(
        (-ti_5s + 0.5 * obi_pers_sell - 0.5 * flow_pers_buy) / 1.5,
        -1.0, 1.0
    )
    abs_safe_sell  = 1.0 - _clip(abs(price_resp_sell) / 100.0)

    ask = _clip(
        0.15 * _clip((obi_lean_sell + 1.0) / 2.0)
        + 0.15 * _clip((flow_lean_sell + 1.0) / 2.0)
        + 0.20 * abs_safe_sell
        + 0.15 * (1.0 - spread_ok)
        + 0.15 * _clip(regime_range)
        + 0.10 * _clip(breakout_fail)
        + 0.10 * (1.0 - _clip(regime_shock / 0.5))
    )

    # --- cancel_score ---
    cancel = _clip(
        0.25 * _clip(spread_deterioration)
        + 0.25 * _clip(rv_5s / params.rv_high)
        + 0.20 * _clip(regime_shock / 0.5)
        + 0.15 * _clip(abs(microprice_mmb) / 5.0)
        + 0.15 * _clip(abs(ti_1s))
    )

    # --- emergency_exit_score ---
    has_pos = 1.0 if abs(position_flag) > 0.5 else 0.0
    emergency = _clip(
        has_pos * (  # hard zero if no position — nothing to exit
            0.40 * _clip(drawdown_pct / max(params.drawdown_hard_threshold, 1e-6))
            + 0.30 * _clip(regime_shock / 0.5)
            + 0.10 * _clip(spread_bps / params.spread_max_hard)
        ) + 0.20 * has_pos  # base penalty just for having a position
    )

    return {
        "bid":           round(bid, 5),
        "ask":           round(ask, 5),
        "cancel":        round(cancel, 5),
        "no_trade":      round(no_trade, 5),
        "emergency_exit": round(emergency, 5),
    }


# ---------------------------------------------------------------------------
# Decision logic
# ---------------------------------------------------------------------------

def teacher_decide(
    features: dict,
    params: TeacherParams = TEACHER_PARAMS,
) -> Tuple[int, str, Dict[str, float]]:
    """
    Choose a maker action from feature dict.

    Returns:
        (action_int, dominant_reason, score_dict)

    Priority order (hard gates first):
      1. TAKER_EXIT — emergency
      2. HOLD       — regime dead / spread blown
      3. CANCEL_ALL — conditions deteriorated
      4. POST_BID / POST_ASK — directional lean
      5. HOLD       — low confidence fallback
    """
    s = compute_scores(features, params)
    position_flag = _g(features, "position_flag")
    has_position  = abs(position_flag) > 0.5

    # 1. Emergency exit — highest priority
    if s["emergency_exit"] >= params.emergency_exit_threshold and has_position:
        return ACT_CLOSE_POSITION, "drawdown_or_shock", s

    # 2. Regime / spread gate
    if s["no_trade"] >= params.no_trade_threshold:
        return ACT_HOLD, "no_trade_regime", s

    # 3. Cancel (if conditions deteriorated — assumes active orders exist)
    if s["cancel"] >= params.cancel_threshold:
        return ACT_CLEAR_QUOTES, "conditions_deteriorated", s

    # 4. Directional maker entry
    can_bid = position_flag <= 0.5   # not already fully long
    can_ask = position_flag >= -0.5  # not already fully short

    if can_bid and can_ask:
        if s["bid"] >= params.bid_min_threshold and s["bid"] >= s["ask"]:
            return ACT_POST_BID, "book_leans_bid", s
        if s["ask"] >= params.ask_min_threshold and s["ask"] > s["bid"]:
            return ACT_POST_ASK, "book_leans_ask", s
    elif can_bid and s["bid"] >= params.bid_min_threshold:
        return ACT_POST_BID, "book_leans_bid", s
    elif can_ask and s["ask"] >= params.ask_min_threshold:
        return ACT_POST_ASK, "book_leans_ask", s

    return ACT_HOLD, "low_confidence", s


# ---------------------------------------------------------------------------
# Smoke test / standalone usage
# ---------------------------------------------------------------------------

def _smoke_test():
    """Quick sanity check: run teacher on 1000 random feature vectors."""
    import random
    action_counts = {v: 0 for v in ACTION_LABELS.values()}

    params = TEACHER_PARAMS

    for _ in range(1000):
        f = {
            "spread_bps":              random.uniform(0.5, 15.0),   # realistic: 0.5-8 bps typical, wider in stress
            "spread_vs_baseline":      random.gauss(0, 1.5),
            "spread_deterioration":    random.betavariate(1.5, 4),  # mostly low, spikes occasionally
            "obi_top1":                random.gauss(0, 0.35),
            "obi_top3":                random.gauss(0, 0.30),
            "microprice_minus_mid_bps": random.gauss(0, 1.5),
            "depth_imbalance_top5":    random.gauss(0, 0.30),
            "obi_persistence_buy":     random.betavariate(2, 3),
            "obi_persistence_sell":    random.betavariate(2, 3),
            "flow_persistence_buy":    random.betavariate(2, 3),
            "flow_persistence_sell":   random.betavariate(2, 3),
            "trade_imbalance_5s":      random.gauss(0, 0.4),
            "trade_imbalance_1s":      random.gauss(0, 0.5),
            "rv_5s":                   random.betavariate(1.5, 6) * 0.5,  # mostly 0-0.15
            "regime_range":            random.betavariate(3, 2),   # slightly range-biased
            "regime_shock":            random.betavariate(1, 8),   # rare shocks
            "regime_dead":             random.betavariate(1, 5),
            "price_response_buy_5s":   random.gauss(0, 15),
            "price_response_sell_5s":  random.gauss(0, 15),
            "breakout_failure_5s":     float(random.random() < 0.2),  # 20% of ticks see this
            "position_flag":           random.choices([-1, 0, 0, 0, 0, 1], weights=[1,4,4,4,4,1])[0],
            # realistic: mostly flat, brief positions — drawdown mostly very low
            "current_drawdown_pct":    min(random.expovariate(2.0) * 0.02, 0.06),
        }
        act, reason, scores = teacher_decide(f, params)
        action_counts[ACTION_LABELS[act]] += 1

    total = 1000
    print("\n=== Teacher Policy V1 Smoke Test (n=1000) ===")
    for label, cnt in sorted(action_counts.items()):
        print(f"  {label:15s}: {cnt:5d}  ({cnt/total*100:.1f}%)")
    print("\nExpected rough ranges:")
    print("  HOLD        : 30-55% (regime dead + low confidence)")
    print("  POST_BID    : 10-25%")
    print("  POST_ASK    : 10-25%")
    print("  CLEAR_QUOTES  :  5-15%")
    print("  CLOSE_POSITION  :  1-8%  (only when position_flag != 0)")


if __name__ == "__main__":
    _smoke_test()
