# Walkthrough: Phase 3.5 Exit Architecture Refactor

We have successfully transitioned **BOTMK3** from an inventory-accumulation model to a **strict trade-lifecycle architecture**. This refactor ensures every trade has a clear state (`LONG`, `SHORT`, `FLAT`) and is subject to economic viability checks before closing.

## 1. Key Implementation Results
- **10-Action Distribution**: The action space has been expanded and clarified.
  - `HOLD`, `OPEN_L/S`, `ADD_L/S`, `REDUCE_L/S`, `CLOSE_L/S`, `REPRICE`.
- **Lifecycle Logic**: [rl.rs](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs) now enforces state transitions.
  - `OPEN` is only allowed when `FLAT`.
  - `REDUCE`/`CLOSE` is only allowed when in a matching position.
  - Market exits are now explicitly gated.
- **Profit Floor (10 bps)**: Market-based reduction and closure are now BLOCKED unless `uPnL > 10 bps` (Profit-Taking) or `uPnL < -30 bps` (Stop-Loss).

## 2. Verification Results
### Gating Confirmation
The `bot-server` logs confirm that the Profit Floor is active and correctly blocking sub-optimal exits during training:
```log
[INFO bot_server::services::rl] RL_EXIT_BLOCKED: uPnL=-0.9bps (Floor=10.0, SL=-30.0)
[INFO bot_server::services::rl] RL_EXIT_BLOCKED: uPnL=2.4bps (Floor=10.0, SL=-30.0)
```

### Full-Loop Integration
- **gRPC Schema**: [bot.proto](file:///c:/Bot%20mk3/proto/bot.proto) updated and bindings regenerated.
- **Python Environment**: [grpc_env.py](file:///c:/Bot%20mk3/python/bot_ml/grpc_env.py) updated to support `ACTION_DIM=10`.
- **Audit Script**: [ppo_eval_checkpoint.py](file:///c:/Bot%20mk3/python/ppo_eval_checkpoint.py) now recognizes and labels the new 10 actions.
- **Training Script**: [ppo_vnext_p3_5.py](file:///c:/Bot%20mk3/python/ppo_vnext_p3_5.py) is actively training with the new workspace.

## 3. Current System State
- **bot-server**: Running (Release Build)
- **Training**: Active (`ppo_vnext_p3_5_test`)
- **Action Space**: 10-D
- **Verdict**: Phase 3.5 is stable. The "Profit Trap" is architecturally mitigated.

> [!NOTE]
> Phase 4 (Selective Entry Gating) is currently deferred until the agent learns to utilize the new `REDUCE` and `CLOSE` actions effectively in this 10-D space.
