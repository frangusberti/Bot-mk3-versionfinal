# PPO Maker-Alpha 100k Intermediate Audit Report

## 1. Executive Summary
The 100k PPO run using the restored BC warm-start pipeline is a **technical success** but an **economic non-event**. The "HOLD-collapse" has been definitively resolved, with the agent transitioning to an active liquidity provision posture. However, no fills were captured during the evaluation window.

## 2. PPO Outcome Metrics (Deterministic Eval)
| Metric | Value |
| :--- | :--- |
| **Net PnL** | **0.00%** |
| **Profit Factor** | **0.00** |
| **Win Rate** | **N/A** |
| **Avg PnL per trade** | **0.00 USDT** |
| **Avg Hold Time** | **0 steps** |
| **Total Trades** | **0** |

## 3. Maker Alpha Metrics
| Metric | Value |
| :--- | :--- |
| **Maker Ratio** | **0.00%** |
| **Toxic Fill Rate** | **0.00%** |
| **Passive Fills** | **0** |
| **Taker Fills** | **0** |
| **Cancel Count** | **0 (Step-level)** |
| **HOLD Rate** | **0.0%** |
| **POST_BID / POST_ASK** | **100% / 0.0%** |
| **TAKE_BUY / TAKE_SELL** | **0.0% / 0.0%** |
| **CLOSE_POSITION** | **0.0%** |

## 4. Behavioral Diagnosis
**Verdict: B) Still needs reward / execution tuning**
- **Activity**: The agent is no longer "scared" of the market (HOLD rate 0%). It is consistently attempting to `POST_BID` (23.6% in training, 100% in eval).
- **Execution Barrier**: The 100% `POST_BID` without fills indicates the agent is submitting orders that are never hit. This happens if the market is trending away or if the order queue is too long (Conservative Fill Model).
- **Next-Step Hypothesis**: The `maker_fill_bonus` (0.2) might be insufficient to overcome the exploration entropy, or the agent needs more training steps to discover the specific micro-structures (e.g., OBI shifts) that lead to fills.

## 5. Baseline Comparison
| Feature | BC v3 Alpha | PPO 100k Pilot |
| :--- | :--- | :--- |
| **Trade Count**| **0** | **0** |
| **HOLD Rate** | **76.4%** | **0.0%** |
| **POST_BID** | **23.6%** | **100.0%** |
| **Status** | **Passive Supervised** | **Aggressive Explore** |

## 6. Final Verdict
**Verdict: B) Still needs reward / execution tuning**
- **Rationale**: We have restored life to the agent, but it is currently "swinging at air". We should increase the `maker_fill_bonus` (e.g., from 0.2 to 1.0) and potentially the `taker_fill_penalty` to force the agent to find fills that don't rely on crossing the spread.
- **Recommendation**: DO NOT proceed to 500k yet. Perform one more 100k run with **aggressive reward shaping** to force a fill event.

---
*Audit conducted on 2026-03-16 following 100k PPO restoration run.*
