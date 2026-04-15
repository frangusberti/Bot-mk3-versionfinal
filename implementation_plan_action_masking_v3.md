# Action Masking and Memory Stabilization (Refined)

Address the OOM issue and provide granular invalid action breakdown without brute-force pruning.

## User Review Required

> [!IMPORTANT]
> **Memory Pruning**: Episodes will be removed from the server's memory map as soon as they reach the `done` state. This prevents RAM accumulation during long training or evaluation runs where many unique episode IDs are generated.

## Proposed Changes

### [Component] bot-server (RL Service)

#### [MODIFY] [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs)
- **`apply_action()`**: 
  - Update the `MASKED_ACTION` block:
    - Distinguish between `invalid_pos_side_mismatch_count` (wrong side) and `invalid_close_flat_count` (closing while flat).
    - Ensure `hard_invalid_count_in_step` is only incremented once.
- **`step()`**:
  - **Telemetry**: Map `StepInfo.exit_distribution` to `episode.exit_distribution.clone()`.
  - **Memory Stabilization**: 
    - Check `episode.done` at the end of the `step` logic.
    - If true, remove the specific `episode_id` from the `self.episodes` HashMap using a write lock.

## Verification Plan

### Automated Verification
- **Pilot Run (25k steps)**: Execute `python/ppo_vnext_viability.py`.
- **Memory Check**: Monitor the `bot-server.exe` process to ensure RAM usage remains stable across multiple episodic resets.
- **Scorecard Review**: Verify the new breakdown counters (`side_mismatch` vs `close_flat`) are reporting correctly.

### Manual Verification
- Review server logs for `[MASKED_ACTION]` markers to confirm granular reasons are accurate.
