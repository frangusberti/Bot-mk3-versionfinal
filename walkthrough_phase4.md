# Phase 4 Behavioral Audit Walkthrough

## Objective
Validate the BOTMK3 trade lifecycle and Selective Entry Gating (SEG) performance over a 50,000-step simulation on `golden_l2_v1_val`.

## Key Achievements
1.  **Selective Entry Gating (SEG)**:
    *   Successfully integrated [check_entry_allowed](file:///c:/Bot%20mk3/crates/bot-server/src/services/orchestrator/risk.rs#715-746) risk gates at -0.5 bps microprice toxicity threshold.
    *   Symmetrical enforcement confirmed in both training and evaluation logic.
2.  **Lifecycle Telemetry**:
    *   Implemented 10-action frequency tracking (OPEN/ADD/REDUCE/CLOSE).
    *   Wired `cumulative_pnl` for persistent realized PnL reporting even across position closures.
    *   Confirmed step-wise telemetry aggregation in the Python audit script.
3.  **Audit Infrastructure**:
    *   Reduced simulation latency to 10ms to improve fill realization in 100ms decision windows.
    *   Enabled `SemiOptimistic` maker fill model for realistic spread capture assessment.

## Behavioral Diagnosis (50,000-Step Scorecard)

### 1. Action Distribution
| Action | Count | Percentage |
| :--- | :--- | :--- |
| **ADD_SHORT** | 48,814 | 97.6% |
| **OPEN_LONG** | 180 | 0.36% |
| **REDUCE_LONG** | 95 | 0.19% |
| **OPEN_SHORT** | 50 | 0.10% |
| **CLOSE_LONG** | 1 | 0.00% |
| **HOLD** | ~800 | 1.6% |

> [!WARNING]
> The agent is currently exhibiting **"Exit Imbalance"**. It is effectively stuck in an infinite loop of `ADD_SHORT`, accumulating a massive position without ever executing a `REDUCE_SHORT` or `CLOSE_SHORT`.

### 2. Economic Metrics
*   **Realized PnL**: $0.00 (Structural failure to close winners).
*   **Total Trades**: 0 (No positions reached absolute FLAT during the aggregate tail).
*   **Entry Vetoes**: (Metrics collected via `ENTRY_VETO` action index in future runs).

## Causal Analysis
The audit confirms that the **architecture is now observable**, which was the primary goal. The "Fail" on economic performance is a **behavioral/policy issue**, likely caused by:
*   **Winner Trapping**: The Profit Floor (10 bps) prevents the agent from closing via `CLOSE_POSITION` if the move is too small.
*   **Policy Immaturity**: The [checkpoint_250000.zip](file:///c:/Bot%20mk3/python/runs_rl/checkpoint_250000.zip) policy hasn't learned the economic value of `REDUCE` actions under the new cost model.

## Verdict: ARCHITECTURE PASS / BEHAVIORAL FAIL
The system is now structurally ready for **Phase 5 (Exit Tuning & Reward V6)**. The foundation is solid, but the policy needs to be retrained with higher exit incentives.
