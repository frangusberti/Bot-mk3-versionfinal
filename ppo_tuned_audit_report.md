# Maker Alpha Audit: 100k Tuned Run

## Executive Summary
The "Order Shining" bug (queue reset) has been **FIXED**. The agent now successfully maintains its position in the market queue, enabling passive fills under simulation. The reward shaping (maker bonus + idle penalty) is **ACTIVE** and has moved the policy from a passive "HOLD-only" state to a "POST-BID" discovery state.

> [!IMPORTANT]
> **VERDICT: B) still needs reward / execution tuning**
> The agent has learned "How to get filled" but not yet "How to be profitable". It is currently a naive liquidity provider.

## Technical Validation

### 1. Training Stability
- **Status**: SUCCESS. The 100k run completed without gRPC failures or reset loops.
- **Stability**: No NaNs or infinities detected.
- **Episode Conclusion**: Many episodes now end via `DAILY_DD_LIMIT` rather than timing out, confirming active trading.

### 2. Execution Alignment (The "Shining" Fix)
- **Problem**: Orders were reset every step, preventing fills in `Conservative` / `SemiOptimistic` models.
- **Fix**: Implemented price/qty stable order preservation in [rl.rs](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs).
- **Result**: `Optimistic` fill model now shows a **1.79% fill rate** (179 fills in 10,000 steps), compared to 0.00% previously.

### 3. PPO Outcome Metrics (10k Scorecard @ Optimistic)
| Metric | Value |
| :--- | :--- |
| **Total Steps** | 10,000 |
| **Maker Fills** | 179 |
| **Taker Fills** | 0 |
| **Maker Ratio** | 100% |
| **Net PnL** | -78.19 USDT |
| **Return Pct** | -0.78% |
| **Max Drawdown** | ~3.0% (triggered reset) |

### 4. Behavioral Diagnosis
The policy has transitioned to **Category B (active maker discovery)**.
- **Observation**: In deterministic evaluation, the agent chooses `POST_BID` 100% of the time.
- **Reasoning**: The `maker_fill_bonus` is so attractive that the agent would rather sit on the bid than do anything else.
- **The Gap**: It has not yet learned that being filled in a trending down market is toxic. The `toxic_fill_penalty` needs to be stronger, or the agent needs more steps to differentiate between "good" and "bad" fills.

## Next Steps
1. **Increase Sample Efficiency**: Now that fills are happening, we can justify moving to a **300k-500k run** to let the agent learn adverse selection.
2. **Reward Refinement**: 
    - Slightly reduce `maker_fill_bonus` to prevent "over-posting" at bad prices.
    - Increase `toxic_fill_penalty` to sharpen price sensitivity.
3. **Transition to Semi-Optimistic**: Once the agent is profitable under `Optimistic`, we should return to `Semi-Optimistic` for final validation.

---
*Audit performed by Antigravity.*
