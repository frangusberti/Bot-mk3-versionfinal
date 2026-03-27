# Walkthrough: Exit Exploration Curriculum Implementation

I have implemented and launched the **Exit Exploration Curriculum** as requested. This plan targets post-BC inactivity by moderately increasing exploration and relaxing the profit floor for exit actions, while introducing granular telemetry to monitor the trade lifecycle.

## Changes Made

### 1. Core Logic & Telemetry (Rust)
- **Profit Floor Relaxation**: The `profit_floor_bps` is now configurable via [RLConfig](file:///c:/Bot%20mk3/proto/bot.proto#501-563) and is currently set to **2.0 bps** for the training run.
- **Enhanced Blocked-Exit Telemetry**:
  - `exit_blocked_1_to_4_count`: Tracks how many exit attempts were blocked in the "almost profitable" zone (1.0 to 4.0 bps).
  - `opportunity_lost_count`: Tracks trades that eventually closed with a realized PnL lower than the peak uPnL seen during a blocked exit attempt.
  - `max_blocked_upnl_bps`: Peak uPnL tracking per trade to identify missed exit windows.

### 2. Infrastructure (Proto & Python)
- **[proto/bot.proto](file:///c:/Bot%20mk3/proto/bot.proto)**: Added `exit_blocked_1_to_4_count` and `opportunity_lost_count` to the gRPC messages.
- **[python/bot_ml/grpc_env.py](file:///c:/Bot%20mk3/python/bot_ml/grpc_env.py)**: Exposed `profit_floor_bps` and updated step logic to report the new metrics.
- **Connection Reliability**: Centralized on `127.0.0.1` for all gRPC traffic to bypass Windows IPv6 resolution issues with `localhost`.

### 3. Training & Reporting
- **Exploration**: `ent_coef` has been adjusted to **0.03** (Previous baseline: 0.01).
- **Curriculum**: Both [ppo_v16_reward_v6.py](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py) and [ppo_vnext_p3.py](file:///c:/Bot%20mk3/python/ppo_vnext_p3.py) are now configured for the curriculum.
- **Explicit Scorecard**: Updated callbacks to explicitly report:
  - Entropy comparison (0.01 vs 0.03).
  - Realized vs Total PnL.
  - Granular Exit Block statistics.
  - Semantic action distribution (HOLD vs OPEN/REDUCE/CLOSE).

## Verification & Launch Status

- **Build**: `cargo build -p bot-server` confirmed the Rust logic and proto generation are correct.
- **Server**: Launched in background (`PID 4236`).
- **PPO Training**: [ppo_vnext_p3.py](file:///c:/Bot%20mk3/python/ppo_vnext_p3.py) is currently **RUNNING**. 
- **Verification**: Server logs confirm the **2.0 bps floor** is active and receiving environment interactions:
  `[INFO bot_server::services::rl] RL_EXIT_BLOCKED: uPnL=-0.4bps (Floor=2.0, SL=-30.0)`

The next major checkpoint (50k steps) will provide the first look at whether the relaxed gates are successfully encouraging trade completion.
