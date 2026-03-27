# PPO Maker-Alpha Pilot Audit Report (100k steps)

## 1. Training Stability
- **Completion**: **SUCCESSFUL**. The run completed 100,000 steps and 584 episodes without gRPC timeout or process crashes.
- **Errors**: **NONE**. No NaNs, infs, or Rust panics detected in [server_log.txt](file:///c:/Bot%20mk3/server_log.txt) after the stabilization patches.
- **Pricing Health**: The [mid_price](file:///c:/Bot%20mk3/crates/bot-data/src/strategy/mod.rs#115-116) fallback logic effectively prevented "Inf" sizing errors that previously invalidated runs.
- **Infrastructure Verdict**: **PASS**. The backend is now robust enough for large-scale training.

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
| **Cancel Count** | **0** |
| **POST_BID / ASK usage**| **0.0%** |
| **TAKE_BUY / SELL usage**| **0.0%** |
| **CLOSE_POSITION usage**| **0.0%** |

## 4. Behavioral Diagnosis
**Verdict: C) Collapsing to HOLD**
- During training, the agent explored all 7 actions (e.g., `TAKE_SELL` at 17.9%).
- However, as training progressed, the policy weights shifted towards `HOLD` as the "safe" mode. In deterministic evaluation, the agent chooses `HOLD` 100% of the time.
- **Root Cause**: The run was started without the `--pretrained_model` flag (BC warm-start). A random agent in a high-fee, conservative fill environment quickly learns that "doing nothing" is the optimal way to avoid negative rewards.

## 5. Baseline Comparison
| Feature | Teacher V2.2 | BC-v2 (Current File) | PPO 100k Pilot |
| :--- | :--- | :--- | :--- |
| **Trade Count**| **High (27% entry signals)** | **0 (Collapsed)** | **0 (Collapsed)** |
| **Maker Ratio** | **Expected High** | **0.00%** | **0.00%** |
| **PnL (Eval)** | **Positive (Sim)** | **0.00%** | **0.00%** |
| **Stability** | **Healthy** | **Stale/Broken** | **Robust Infrastructure** |

> [!IMPORTANT]
> The current BC model ([bc_model_flow_v2.zip](file:///c:/Bot%20mk3/python/models/bc_model_flow_v2.zip)) also shows 100% HOLD behavior in the new 7-action infrastructure. This indicates that **previous behavior cloning weights are stale** and incompatible with the newly stabilized action-space indexing.

## 6. Final Verdict
**Verdict: B) Tune reward / execution first**
- **Action Required**: 
  1. **Regenerate BC dataset** using the now-aligned [teacher_dataset_generator.py](file:///c:/Bot%20mk3/python/teacher_dataset_generator.py).
  2. **Retrain BC model** to obtain a valid 7-action starting policy.
  3. **Restart PPO** with `--pretrained_model` pointing to the new BC weights.
- **Rationale**: The infrastructure is finally solid, but we are trying to teach a baby to compete in high-frequency market making without first giving it the "Teacher's" memory. PPO cannot discover the sparse reward of a maker fill from random noise alone with the current punitive reward shaping.

---
*Audit conducted on 2026-03-16 following 100k stabilization run.*
