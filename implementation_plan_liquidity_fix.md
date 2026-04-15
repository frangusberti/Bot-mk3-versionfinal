# Robust Liquidity Classification Fix

Implement a state-based liquidity classification system in the `ExecutionSimulator` to accurately distinguish between Maker and Taker fills, regardless of asynchronous BBO updates.

## User Review Required

> [!IMPORTANT]
> The classification will now rely strictly on the order state captured at the moment of submission (`was_marketable_on_arrival` and `resting_since_ts`). This eliminates "blind" deduction based on price comparisons.

## Proposed Changes

### [Component] bot-data (Simulation)

#### [MODIFY] [structs.rs](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/structs.rs)
- Ensure `resting_since_ts` is a consistently populated `Option<i64>`.

#### [MODIFY] [execution.rs](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs)
- **`submit_order`**:
  - Capture `was_marketable_on_arrival` by checking BBO at the moment of submission.
  - Set `resting_since_ts = current_time + latency_ms` if the order is accepted as passive.
- **`process_order_matching`**:
  - Replace the comparison of `order.price` vs `trade_price` with state-based checks:
    - **Maker**: `accepted_as_passive` AND `event.time_canonical >= resting_since_ts`.
    - **Taker**: `was_marketable_on_arrival` OR `immediate_execution_before_resting`.
    - **Unknown**: Default fallback if state is ambiguous.

---

### [Component] bot-server (RL Integration)

#### [MODIFY] [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs)
- Integrate the 5 requested telemetry counters:
  - `accepted_as_passive_count`
  - `accepted_as_marketable_count`
  - `resting_fill_count`
  - `immediate_fill_count`
  - `liquidity_flag_unknown_count`
- Increment arrival counters at submission (including market exits).
- Increment fill counters during the step's fill event processing.

---

### [Component] Telemetry & Pilot

#### [MODIFY] [bot.proto](file:///C:/Bot%20mk3/proto/bot.proto)
- Ensure all 5 telemetry fields are present in `StepInfo`.

#### [MODIFY] [grpc_env.py](file:///C:/Bot%20mk3/python/bot_ml/grpc_env.py)
- Map the new `StepInfo` fields to the Python environment metadata.

#### [NEW] [ppo_vnext_viability.py](file:///C:/Bot%20mk3/python/ppo_vnext_viability.py)
- Execute a 25,000-step training diagnostic with **Variant B** (Consolidated Economic Reward) and `fill_model=2`.

## Verification Plan

### Automated Verification
- **Diagnostic Scorecard**: Run `python/ppo_vnext_viability.py` and verify:
  - `Resting Fills` > 0 (if the agent learns to be passive).
  - `Immediate Fills` matches marketable order intent.
  - `Unknown Fills` remains near zero.
  - `Invalid Rate` is audited against `accepted_as_marketable_count` if enforced.

### Manual Verification
- Review server logs for `[FILL_MATERIALIZED]` and `LIQUIDITY_DEBUG` markers to ensure classification matches intent.
