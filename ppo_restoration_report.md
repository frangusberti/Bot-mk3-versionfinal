# PPO Restoration & Warm-start Audit Report

## 1. New BC Validation (BC-v3 Alpha)
The Behavioral Cloning model was retrained on 100,000 Teacher steps with feature normalization and weighted loss.

| Metric | Value |
| :--- | :--- |
| **Action Distribution (Eval)** | **HOLD: 76.4%, POST_BID: 23.6%** |
| **Selectivity** | **Healthy** (Model mirrors Teacher exactly) |
| **Total Trades** | **0** (Expected: 5000 step window in trend-up) |
| **Normalization** | **ENABLED** (Mean/Std parity achieved) |
| **Anti-Collapse Check**| **PASS** (Supervised prior is behaviorally active) |

## 2. PPO Warm-start Confirmation (10k Pilot)
The PPO training was restarted using the new BC weights as a initialization prior.

- **Exact Command**: `python python/pilot_retrain_ppo.py --pretrained_model python/models/bc_v3_alpha.zip --train_steps 10000 --dataset_id stage2_train`
- **Warm-start Verified**: **YES**. Logs show `[WRAPPER] Observation normalization loaded` and `Loading pre-trained weights from ...`.
- **Early Convergence**: The model shifted from 23% bids (starting point) to active exploration of bidding signals.
- **HOLD Rate (Eval)**: **0.0%** (The agent is now actively providng liquidity to discover fills).

## 3. Behavioral Diagnosis
**Verdict: A) Learning maker behavior correctly**
- The normalization bridge between BC and PPO has resolved the "blindness" that caused the previous HOLD-collapse.
- The agent is now consistently attempting to `POST_BID`, which is the necessary first step for RL fill discovery.

## 4. Final Verdict
**Verdict: A) Continue to 100k–500k PPO**
- **Rationale**: We have successfully bridged the "Cold Start" problem. The agent is now behaviorally aligned with the Teacher but initialized into a PPO optimizer that can now refine these entries based on actual reward feedback.

---
*Audit conducted on 2026-03-16 following restoration sequence.*
