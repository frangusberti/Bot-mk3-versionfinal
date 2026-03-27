# Policy Server Contract (v1.0)

The Policy Server is a FastAPI-based HTTP JSON service that handles trade inference requests from the Rust Orchestrator.

## Connection
- **Endpoint**: `http://localhost:50055`
- **Inference**: `POST /infer`
- **Reload**: `POST /reload`
- **Health**: `GET /health`
- **Metrics**: `GET /metrics`

## Inference Contract (JSON)

### Request
```json
{
  "symbol": "BTCUSDT",
  "ts_ms": 1676543210000,
  "mode": "PAPER",
  "decision_interval_ms": 1000,
  "obs": [1.0, 0.5, ...], // Exactly 12 floats
  "risk": {
    "max_pos_frac": 0.5,
    "effective_leverage": 10.0
  },
  "portfolio": {
    "is_long": 1.0,
    "is_short": 0.0,
    "is_flat": 0.0,
    "position_frac": 0.3,
    "upnl_frac": 0.01,
    "leverage_used": 3.0,
    "equity": 1500.0,
    "cash": 1200.0
  },
  "meta": {}
}
```

### Response
```json
{
  "action": "HOLD",
  "confidence": 0.95,
  "reason": "spread_too_high",
  "policy_version": "heuristic_v1",
  "latency_ms": 0.5
}
```

## Observation Vector (12 floats)
1. `mid_price`
2. `best_bid`
3. `best_ask`
4. `bid_qty`
5. `ask_qty`
6. `spread_bps`
7. `volatility_20`
8. `volume_20`
9. `rsi_14`
10. `log_return_1`
11. `equity` (normalized)
12. `leverage` (current effective)

## Policy Types
- `hold`: Always returns `HOLD`.
- `heuristic`: Rule-based logic for spread, volume, and returns.
- `sb3_ppo`: Loads a Stable Baselines 3 PPO model (.zip).
