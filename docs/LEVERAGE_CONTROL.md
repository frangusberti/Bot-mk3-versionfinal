# Leverage Control Layer (Module 7.1)

Per-symbol leverage modes (Manual / Auto / Fixed) with safety controls, integrated with the Orchestrator risk pipeline.

## Sizing Semantics

```
margin_budget   = equity × max_pos_frac     (fraction of equity used as margin)
notional_target = margin_budget × leverage   (what we actually trade)
target_qty      = notional_target / mid_price
```

> **`max_pos_frac` is a MARGIN fraction**, not a notional fraction.
> At `max_pos_frac=0.5` with `10x` leverage, you control `5×` your equity per symbol.

## Modes

| Mode | Behavior | Effective Leverage Source |
|------|----------|--------------------------|
| **MANUAL** | Static value from GUI | `manual_value` |
| **AUTO** | Adjusts on decision ticks using volatility/spread | Policy output, bounded by `[auto_min, auto_max]` |
| **FIXED** | Static value, ignores market data | `fixed_value` |

## AUTO Policy

- **Inputs**: `realized_vol_10`, `relative_spread` from FeatureVector
- **Normalization**: configurable `auto_vol_ref` (default 0.002), `auto_spread_ref` (default 0.001)
- **Risk Score**: `0.7 × clamp(vol/vol_ref, 0..1) + 0.3 × clamp(spread/spread_ref, 0..1)`
- **Leverage**: `lerp(auto_max, auto_min, risk_score)` with smoothing
- **Safety**: cooldown (no changes within N seconds), rate limit (max delta/min)
- **Deterministic**: same `(vol, spread, ts, config, state)` → same output
- **Initialization**: AUTO starts at midpoint `(auto_min + auto_max) / 2`
- **Update cadence**: ONLY on decision ticks (FeatureVector emission), NOT on every market event

## Configuration Defaults

| Parameter | Default | Range |
|-----------|---------|-------|
| `manual_value` | 5.0 | 1–125 |
| `fixed_value` | 5.0 | 1–125 |
| `auto_min` | 3.0 | 1–50 |
| `auto_max` | 10.0 | 1–125 |
| `auto_vol_ref` | 0.002 | >0 |
| `auto_spread_ref` | 0.001 | >0 |
| `auto_cooldown_secs` | 60 | ≥0 |
| `auto_max_change_per_min` | 1.0 | >0 |
| `live_readback_interval_secs` | 120 | 30–600 |

## Proto

- `LeverageMode` enum: `UNSPECIFIED(0)`, `MANUAL(1)`, `AUTO(2)`, `FIXED(3)`
- `SymbolConfig` fields 7–19 for leverage control
- `SymbolStatus` fields 11–16 for leverage display (effective_leverage, risk_score, mode, reason, apply_state, apply_error)

## Live Safety

- `live_apply_enabled`: Must be explicitly toggled to push leverage to Binance via `POST /fapi/v1/leverage`
- `live_readback_enabled`: Reads exchange leverage at `live_readback_interval_seconds` intervals
- Failures are logged and non-crashing → `apply_state` = `"APPLIED_FAIL"`, error stored
- RL agent NEVER controls leverage

## Persistence

Config saved to `data/config/leverage_config.json` on stop and config updates. On startup with missing file, defaults are created for BTCUSDT, ETHUSDT, DOGEUSDT, XRPUSDT.

## GUI

The **Leverage Control** panel provides:
- Mode dropdown, Manual/Fixed spinners, AUTO min/max/cooldown/rate
- **Vol Ref** / **Spread Ref** spinners for AUTO normalization tuning
- Live apply toggles + readback interval
- Status table: 13 columns including Lev, Risk, Mode, Reason, Apply State

## VPS Migration Notes

Files/paths likely to change on VPS deployment:
- `data/config/leverage_config.json` — persistence path (engine.rs L114, L261, L304)
- `http://localhost:50055` — policy gRPC endpoint (engine.rs L208)
- `BINANCE_API_KEY` / `BINANCE_SECRET_KEY` — env vars (engine.rs L173-174)
- Proto stubs regeneration: `python -m grpc_tools.protoc -I proto --python_out=... proto/bot.proto`

## Files

| File | Change |
|------|--------|
| `proto/bot.proto` | `LeverageMode` enum, `SymbolConfig` fields 7-19, `SymbolStatus` fields 11-16 |
| `crates/bot-server/src/services/orchestrator/leverage.rs` | Core policy with configurable refs, 12 unit tests |
| `crates/bot-server/src/services/orchestrator/agent.rs` | Margin→notional sizing, decision-tick-only update |
| `crates/bot-server/src/services/orchestrator/engine.rs` | Proto enum mapping, new config fields |
| `crates/bot-data/src/binance_futures_live/client.rs` | `set_leverage`/`get_leverage` (unchanged in patch) |
| `python/bot_gui/tabs/orchestrator.py` | 13-col status, vol/spread ref, proto enum values |
