# Observation Schema V4

The `FeatureEngineV2` architecture produces a vector of observations intended for the Reinforcement Learning policy module. Schema V4 expands upon previous schemas to directly ingest Open Interest primitives into the core observation window.

## Shape and Contract
- **OBS_DIM:** 76 (38 floating point values + 38 boolean masks)
- **OBS_SCHEMA_VERSION:** 4

The vector is strictly formatted as `[V_0, V_1, ..., V_37, M_0, M_1, ..., M_37]`, where `V_n` is the feature value, and `M_n` is the validity mask (0.0 or 1.0).

### Feature Map (`0..37`)

#### A) Price & Spread Group (Group A)
0. `mid_price`: Core BBO mid price (Raw).
1. `spread_abs`: Absolute spread distance (Raw).
2. `spread_bps`: Spread expressed in basis points (Raw).

#### B) Returns & Volatility (Group B)
3. `ret_1s`: Relative Mid-Price return over the last 1 second (Clamped: `[-1.0, 1.0]`).
4. `ret_5s`: Relative Mid-Price return over the last 5 seconds (Clamped: `[-1.0, 1.0]`).
5. `ret_30s`: Relative Mid-Price return over the last 30 seconds (Clamped: `[-1.0, 1.0]`).
6. `rv_30s`: Realized volatility of 1s returns over a 30s window (Clamped: `[-1.0, 1.0]`).
7. `rv_5m`: Realized volatility of 1s returns over a 5m window (Clamped: `[-1.0, 1.0]`).

#### C) Flow & Tape (Group C)
8. `taker_buy_vol_1s`: Taker buy notional accumulated in the last 1 second.
9. `taker_sell_vol_1s`: Taker sell notional accumulated in the last 1 second.
10. `taker_buy_vol_5s`: Taker buy notional accumulated in the last 5 seconds.
11. `taker_sell_vol_5s`: Taker sell notional accumulated in the last 5 seconds.
12. `tape_trades_1s`: Raw count of trade events in the last second.
13. `tape_intensity_z`: Rolling z-score of trade volume (Clamped: `[-10.0, 10.0]`).

#### D) Microstructure & Book (Group D)
14. `obi_top1`: Order Book Imbalance at Level 1 (Clamped: `[-1.0, 1.0]`).
15. `obi_top3`: Order Book Imbalance across the top 3 levels (Clamped: `[-1.0, 1.0]`).
16. `microprice`: Microprice (bid-ask volume weighted price).
17. `microprice_minus_mid_bps`: Difference between microprice and mid_price in bps (Clamped: `[-1.0, 1.0]`).
18. `obi_delta_5s`: Change in OBI over the last 5 seconds.

#### E) Shocks & Derivatives (Group E)
19. `liq_buy_vol_30s`: Notional volume of buy liquidation orders in 30s window.
20. `liq_sell_vol_30s`: Notional volume of sell liquidation orders in 30s window.
21. `liq_net_30s`: Net liquidated volume (Buy - Sell) in 30s window.
22. `liq_count_30s`: Count of distinct liquidation events.
23. `mark_minus_mid_bps`: Difference between Mark price and Mid price in bps (Clamped: `[-1.0, 1.0]`).
24. `funding_rate`: Current instantaneous funding rate.

#### F) Technicals (Group F)
25. `ema200_distance_pct`: Distance of current price to 200 EMA (Clamped: `[-1.0, 1.0]`).
26. `rsi_14`: 14-period RSI (Raw `[0.0, 100.0]`).
27. `bb_width`: Bollinger Bands 20 Width.
28. `bb_pos`: Price Z-Score relative to Bollinger Bands 20 (Clamped: `[-10.0, 10.0]`).

#### G) Local State & Position (Group G) - **External**
29. `position_flag`: Agent's current position state (`-1.0`, `0.0`, `1.0`).
30. `latent_pnl_pct`: Unsettled local PNL percentage (Clamped: `[-1.0, 1.0]`).
31. `max_pnl_pct`: Maximum Latent PNL percentage historically observed (Clamped: `[-1.0, 1.0]`).
32. `current_drawdown_pct`: Drawdown from local peak margin (Clamped: `[-1.0, 1.0]`).

#### H) Canonical Clock (Group H)
33. `time_sin`: UTC Sine wave of Hour component.
34. `time_cos`: UTC Cosine wave of Hour component.

#### I) Open Interest (Group I) - **[NEW IN V4]**
35. `oi_value`: Raw Open Interest value sampled from REST or Stream.
36. `oi_delta_5m`: Rolling 5-minute relative delta of OI (Clamped `-1.0..1.0`). Requires strictly 4.5+ minutes of history.
37. `oi_zscore_30m`: Rolling EWMA Z-score over a 30m equivalent window (Clamped `-10.0..10.0`).

### Mask Application Rule
If a vector's corresponding mask at index `N + 38` equals `0.0`, the neural policy should treat the value as an imputation (non-actionable or "unknown") and route decisions around it unless the model supports `NaN` ingestion natively. All features implement safe defaults when un-masked.
