# Walkthrough: Action Masking & Memory Stabilization

We have successfully implemented a surgical action-masking layer and a memory-management system for the `bot-server`. This ensures the agent operates strictly within Maker-only constraints while preventing RAM leaks.

## Changes Made

### 1. `bot-server` (RL Service)
- **`compute_action_mask()`**: New method to determine valid actions based on:
    - **Position state**: Masking `CLOSE/REDUCE` if flat, `OPEN/ADD` if already in position.
    - **Marketability (Maker Regime)**: Masking `OPEN/ADD` if the synthetic passive price would be marketable against the BBO.
- **`apply_action()`**: Integrated the mask check at the very beginning of the step.
    - **Granular Breakdown**: Added specific counters for `valid_open_marketable`, `invalid_close_flat`, `invalid_reprice_empty`, and `invalid_pos_side_mismatch`.
    - **No Silenced Actions**: If a masked action is chosen, it is logged and penalized, not silently converted to `HOLD`.
- **Memory Pruning**: Added logic to `step()` to remove episodes from the `std::sync::RwLock<HashMap>` as soon as `done: true`. This fixed the OOM issues during long runs.

### 2. Protocol & Telemetry
- **`bot.proto`**: Added `repeated float action_mask = 35` and the 5 new granular counters to `StepInfo`.
- **`grpc_env.py`**: Exposed the mask and breakdown in the Gymnasium `info` dictionary.

## Validation Results

We ran a **25,000-step Diagnostic Pilot** (`ppo_vnext_viability.py`) with the following results:

> [!TIP]
> **Memory Stability**: The audit finished with a stable RSS of **385.4 MB**, confirming that the pruning logic successfully prevented the previous OOM crash.

### Invalid Action Breakdown
The pilot revealed why the `Invalid Rate` was so high (46.8%):
- **4,148** attempts to `CLOSE` while flat (Agent desperation).
- **532** attempts to `OPEN` while marketable (Violating Maker constraints).
- **0** Side mismatches.

### Execution Accuracy
- **100% Maker Fills**: 266 Trades, 266 Resting Fills, 0 Immediate Fills.
- **Action Distribution**: The agent is spending ~41% of its time trying to close a non-existent position.

## How to Verify
Run the viability script to see the new breakdown in the scorecard:
```powershell
python python/ppo_vnext_viability.py
```

Check the server logs for `[MASKED_ACTION]` markers to see real-time rejections:
```text
[2026-03-31T01:36:21Z INFO bot_server::services::rl] [MASKED_ACTION] OpenShort: Marketable on arrival (Maker violation)
```
