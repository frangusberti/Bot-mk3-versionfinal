# PPO Economic Refinement Report: Metric & Behavioral Audit

## Executive Summary
The PPO agent has completed a 100k step refinement run using the **SemiOptimistic** fill model and a newly implemented **Mark-to-Market (MtM) Penalty**. While the system is technically stable and the protocol synchronization issues have been resolved, the agent has transitioned from "HOLD-Collapse" to "Naive Touch-Chasing." In deterministic evaluation, the agent defaults to `POST_BID` 100% of the time, leading to zero fills in the realistic `SemiOptimistic` model as it constantly resets its queue priority.

## Performance Metrics (100k Audit)
| Metric | Value |
| :--- | :--- |
| **Net PnL** | 0.00 USDT (0 trades) |
| **Profit Factor** | 0.00 |
| **Win Rate** | 0.0% |
| **Avg PnL per trade** | 0.00 |
| **Maker Ratio** | 0.0% (Zero fills) |
| **Toxic Fill Rate** | 0.0% |
| **Passive Fills** | 0 |
| **Taker Fills** | 0 |
| **Cancel Count** | 0 (Preserved orders) |
| **HOLD Rate (Training)** | 44.2% |
| **Action Usage (Eval)** | POST_BID (100%), others (0%) |
| **Avg Hold Time** | N/A |

## Behavioral Analysis
1.  **Selectivity Learning:** The agent showed significant learning during training, with a `HOLD` rate of **44.2%**. This indicates the MtM penalty is discouraging indiscriminate posting during volatile periods.
2.  **Greedy Policy Divergence:** Despite learning to `HOLD` during exploration, the deterministic (greedy) policy has converged on 100% `POST_BID`. In a `SemiOptimistic` environment, this strategy is fatal: by updating the order every second to match the "touch," the agent restarts its queue priority, ensuring it never reaches the front for a fill.
3.  **Refinement Success:** The "Order Shining" bug was successfully neutralized via a 5% quantity tolerance and [SimOrderBook](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#45-49) synchronization. Orders are now preserved if the price remains steady, but the agent's policy forces a move if the price shifts even slightly.

## Implementation Details
-   **MtM Penalty:** Formula: `Penalty = abs(Mid_t+N - FillPrice) / Mid_t * Multiplier`. Used 1s window (N=1000ms) and 2.0x multiplier.
-   **Reward Rebalance:** `maker_fill_bonus` reduced to **6 bps**; `toxic_fill_penalty` held at **10 bps**.
-   **Fill Model:** Trained under **SemiOptimistic** (10% queue scale).

## Final Verdict
**Verdict: B) still needs reward / execution tuning**

**Reasoning:**
The agent has discovered "Engagement" but not "Front-running or Queue Management." It treats any touch as a potential win, unaware that its own decision frequency (1Hz) combined with price volatility resets its fill probability to near-zero. 

**Next Recommended Steps:**
1.  **Reduce Decision Frequency?** Or add a "Post Delta" threshold to discourage chasing 1-tick moves.
2.  **Increase Idle Penalty:** Currently **0.1 bps** per step is too low to discourage useless chasing.
3.  **Increase MtM Penalty:** Amplify the adverse selection signal to force the agent into `HOLD` more often when the price is trending through the bid.
4.  **Extend Training:** The 44.2% training `HOLD` rate suggests the agent *is* learning; 100k might simply be too short for the policy and value functions to fully align under the more difficult `SemiOptimistic` fill conditions.
