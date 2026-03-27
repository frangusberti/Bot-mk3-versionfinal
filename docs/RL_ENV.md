# RL Environment Documentation

This document describes the Gym-like environment provided by `RLService` and consumed by `GrpcTradingEnv`.

## Architecture
- **Server**: Rust `bot-server` running `RLService`. Owns the `ExecutionEngine` and `ReplayEngine`.
- **Client**: Python `bot_ml` running `GrpcTradingEnv` (Gym wrapper) + Stable-Baselines3 (PPO).
- **Communication**: gRPC over localhost:50051.

## Action Space
Discrete(7):

| Index | Name | Description |
|---|---|---|
| 0 | HOLD | Do nothing. |
| 1 | OPEN_LONG | Market Buy to target size (`max_pos_frac`). Closes short if exists. Refills if under target by > $10. |
| 2 | OPEN_SHORT | Market Sell to target size (`max_pos_frac`). Closes long if exists. Refills if under target by > $10. |
| 3 | CLOSE_ALL | Market Close entire position. |
| 4 | REDUCE_25 | Close 25% of current position. |
| 5 | REDUCE_50 | Close 50% of current position. |
| 6 | REDUCE_100 | Alias for CLOSE_ALL. |

*Note: OPEN actions are idempotent. If already at target size, they do nothing.*

## Observation Space
Box(12,) float32. Deterministic order:

| Index | Name | Source | Description |
|---|---|---|---|
| 0 | mid_price | FeatureVector | Normalized mid price |
| 1 | log_return_1 | FeatureVector | 1-step log return |
| 2 | log_return_5 | FeatureVector | 5-step log return |
| 3 | realized_vol_10 | FeatureVector | 10-step horizon volatility |
| 4 | bid_ask_spread | FeatureVector | Best bid/ask spread |
| 5 | relative_spread | FeatureVector | Spread / Mid Price |
| 6 | is_long | Portfolio | 1.0 if position > 0, else 0.0 |
| 7 | is_short | Portfolio | 1.0 if position < 0, else 0.0 |
| 8 | is_flat | Portfolio | 1.0 if position == 0, else 0.0 |
| 9 | position_frac | Portfolio | `abs(notional) / equity` |
| 10 | upnl_frac | Portfolio | `unrealized_pnl / equity` |
| 11 | leverage_used | Portfolio | `notional / equity` |

## Reward Function
Rewards are dense and calculated at every step:

```python
reward = (equity_current - equity_prev) / initial_equity 
       - 0.5 * max(0, drawdown_fraction)
```

- **PnL**: Normalized by initial equity. Fees are naturally captured in equity change.
- **Drawdown**: Penalized 0.5x.

## Constraints
- **Max Hold Time**: Configurable `max_hold_ms`. If a position is held longer than this, the episode terminates with reason `MAX_HOLD_TIME`.
- **Disaster Stop**: If equity drops by `hard_disaster_drawdown` (default 6%), the episode terminates.
- **Daily Drawdown**: If equity drops by `max_daily_drawdown` (default 3%) from daily peak, the episode terminates.

## Determinism
The environment is strictly deterministic given:
1. Same `dataset_id`
2. Same `seed` (controls random fills or latency if enabled, though training usually has latency=0)
3. Same `config`

The `test_rl_determinism.py` script verifies this by hashing the stream of observations, rewards, and done signals.

## Running Training
```bash
# 1. Start Server
target/debug/bot-server

# 2. Run Trainer (in separate terminal)
python python/bot_ml/rl_train.py --symbol BTCUSDT --dataset synthetic_test --steps 100000
```

logs are saved to `python/runs_train/<run_name>/`.
