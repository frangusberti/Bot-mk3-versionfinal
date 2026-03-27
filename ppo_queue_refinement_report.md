# Maker Alpha Audit Report — Queue Management Pass

## 1. Audit Parameters
*   **Run ID**: `pilot_stage2_train`
*   **Checkpoint**: Final (100k steps)
*   **Dataset**: `stage2_eval`
*   **Fill Model**: `SemiOptimistic`
*   **Sensitivity**: 0.05 bps threshold, 0.5 bps reprice penalty.

## 2. Behavioral Diagnosis: "The Stranded Maker"
The audit shows a technically stable but economically non-functional policy.

### Key Metrics
*   **Action Distribution (Eval)**: 100% `POST_BID`
*   **Trades Executed**: 0
*   **Maker Fills**: 0
*   **Reprice Count**: 0
*   **Maker Ratio**: 0.00%

### Findings
1.  **Order Anchoring**: The anti-chasing logic is successfully **preserving** orders. However, because the `stage2` dataset lacks L2 book data, the agent relies on a `mid - 0.1` fallback.
2.  **Sensitivity Deadzone**: Positive movement in the BTC mid-price is often smaller than the anti-chasing threshold (even at 0.05 bps). This causes the order to stay "anchored" at a stale price while the market moves away.
3.  **Queue Rejection**: In the `SemiOptimistic` model, if the agent *does* reprice, it resets queue priority. If it *doesn't* reprice (preserves), it gets left behind by the touch. This creates a "damned if you do, damned if you don't" loop for a deterministic policy that only picks `POST_BID`.
4.  **Learning Gap**: The agent has not yet learned that `POST_BID` without a fill is inferior to `HOLD`.

## 3. Final Verdict
**VERDICT: B) Still needs reward / execution tuning**

The bot is technically ready and the anti-chasing mechanics are functional. However, the policy has collapsed into a "Static Poster" mode. The next phase must introduce **stronger incentives for fills** (relative to idle time) and potentially an even more adaptive pricing logic if L2 data remains missing.

## 4. Proposed Next Steps
*   **Increase Reprice Penalty**: Force the agent to value its queue position more by making movement more expensive.
*   **Idle Posting Penalty Hike**: Discourage 100% `POST_BID` by penalizing non-filling active orders more severely.
*   **L2 Depth Simulation**: If original datasets lack L2, implement a synthetic "Depth Wrapper" that provides a realistic (scaled) book to allow the `POST_BID` logic to pick more diverse prices.
