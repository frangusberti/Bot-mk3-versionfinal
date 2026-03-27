# Feature Temporal Contract — BOTMK3

This document defines the formal temporal validity requirements for all features computed by `FeatureEngineV2`.

## Global Standards

- **Clock Basis**: `EventTime` (ts_exchange) is the primary clock for all replay and live-maker decisions.
- **Monotonicity**: Feature snapshots MUST have strictly monotonic timestamps (`ts_event`).
- **Causality**: No future-data leakage. In replay mode, observability is limited to events where `ts_exchange <= current_step_ts`.

## Feature Group Requirements

| Group | Subgroup | Clock Basis | Max Age (ms) | Stale Policy | Missing Policy |
|-------|----------|-------------|--------------|--------------|----------------|
| **Core** | Price / OHLC | EventTime | 0 (Atomic) | DROP | FAIL |
| **Market** | Orderbook (L2) | EventTime | 10ms | CARRY_LAST | FAIL |
| **Micro** | Absorption | EventTime | 50ms | DECAY | ZERO |
| **Micro** | Imbalance | EventTime | 50ms | DECAY | ZERO |
| **Shocks** | Liquidations | EventTime | 500ms | DROP | ZERO |
| **Tech** | Volatility | EventTime | 2000ms | CARRY_LAST | ZERO |

## AGE Definitions

- **Feature Age** = `current_obs_ts - feature_last_update_ts`
- **Valid**: Age < Max Age
- **Stale**: Max Age <= Age < 10x Max Age
- **Degenerate**: Age >= 10x Max Age

## Audit Targets

1. **Snapshot Audit**: Verify `ts_event` is monotonic and age is within bounds for 99% of samples.
2. **Missing Flag Audit**: Ensure `NaN` or `Inf` values are 0% in verified groups.
3. **Parity Audit**: Replay snapshot values MUST match Live snapshot values with error < 1e-7.
