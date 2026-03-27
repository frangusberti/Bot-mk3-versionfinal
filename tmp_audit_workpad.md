# rl.rs vs agent.rs Audit Workpad

## Skipped Blocks in `rl.rs` (Training) that exist in `agent.rs` (Runtime)

1. **RiskGate (Pre-Inference Veto)**
   - **What it does:** Checks consecutive failures, recent drawdown, and applies cool-downs (e.g. `Recovery`, `Halted`). If failed, inference isn't even called.
   - **In rl.rs:** Eliminated. PPO always infers and always acts.
   - **Classification:** Optional / Runtime-only. RL should learn the raw market edge. If the RL policy knows it's doing poorly, it should learn to HOLD organically.

2. **ExecutionQualityBlock**
   - **What it does:** Rejects trades when spread is too high or volume is too low.
   - **In rl.rs:** Ignored. PPO assumes perfect fill capability (sans fixed slippage depending on the matching engine).
   - **Classification:** Essential. PPO will learn "fake" edge in illiquid moments if it thinks it can cross massive spreads without penalty.

3. **CostModelBlock (V(s) Edge Veto)**
   - **What it does:** Compares the predicted alpha vs expected fees and slippage. If net edge is negative, it vetoes the trade.
   - **In rl.rs:** Ignored completely. PPO just learns from the reward function.
   - **Classification:** Should remain runtime-only. PPO's reward function inherently penalizes bad trades via fees in `RewardCalculator`. The `CostModelBlock` is a deterministic overlay. If we veto inside PPO, PPO never gets the negative reward feedback for *trying* to make a bad trade, breaking the learning loop. Or, rather, it becomes an environment wall. 

4. **DynamicSizingBlock**
   - **What it does:** Multiplies max fraction by regime scores, execution quality, and risk score.
   - **In rl.rs:** Hardcoded to `max_pos_frac * equity`.
   - **Classification:** Essential / Optional. Variable sizing changes the reward magnitude. If PPO trains on flat sizing but trades on dynamic sizing, its Q-values will be miscalibrated.

5. **StopPolicyBlock & CommissionPolicy**
   - **What it does:** Computes dynamic SL/TP and Maker/Taker urgency.
   - **In rl.rs:** RL submits raw Market orders and has no SL tracking natively in `run_step`.
   - **Classification:** Essential. If the bot relies on SL logic to cap losses in live, PPO must train with the same SL logic, or it will hallucinate hold durations.


## Tradeoffs
1. **Realism vs Stability:** Adding the CostModel veto inside RL means the agent never experiences the *loss* of crossing the spread when edge is low, because the environment blocks it and it just holds. It's better for RL to take the action, incur the cost, and learn the negative reward naturally.
2. **Gates-inside-training vs outside:** If gates are inside, RL learns to "not bother" because it gets blocked. If gates are outside (runtime only), RL acts as an "Alpha signal", and the gates protect the system from RL's false positives.

## Recommendations
We should use a hybrid approach. The PPO should act as a pure "Alpha" signal (`LegacyRaw` or `Value_Only`), meaning it predicts raw values. The runtime `agent.rs` translates that into sized executions safely. We SHOULD synchronize the `StopLgoss` and `Commission` assumptions into the RL reward envelope if possible.
