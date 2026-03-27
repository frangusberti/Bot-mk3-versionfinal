# Phase 3 Final Walkthrough: High-Volatility Hardening

We have successfully completed **Phase 3: Real-World Hardening** for the BOTMK3 agent.
By hitting the 150k aggregate step milestone on the volatile `stage2_train` dataset, we have performed the most rigorous causal audit to date.

## 1. Metric Forensics (150k Aggregate)

| Metric | Baseline (`golden_l2_v1_val`) | Adversarial (`stage2_eval`) |
| :--- | :--- | :--- |
| **Total Trades** | 1 | 143 |
| **Net PnL** | -0.10 | **-1.30** |
| **Avg Spread Capture** | -0.39 bps | **-3.21 bps** |
| **AS-Fav (5s)** | 100% | 100% |
| **Throughput** | N/A | **200 FPS (Peak)** |

### Causal Diagnosis: "The High-Quality Entry Problem"
The 150k report reveals a striking paradox:
The agent has **100% AS-Favorable (5s)** in adversarial windows. This means the price moves in the agent's favor after entry.
HOWEVER, **Spread Capture is -3.21 bps**.

> [!IMPORTANT]
> **Discovery**: The agent is correctly predicting price direction, but it is paying an **unsustainable premium** (spread) to enter during `Trend` and `Shock` regimes. It is being "run over" by market makers who widen spreads faster than the agent's alpha can compensate.

---

## 2. Infrastructure Optimization
We resolved the 16 FPS bottleneck by:
1.  **Concurrent Locking**: Refactored `RLServiceImpl` to use granular per-episode locks.
2.  **Unique Episode IDs**: Implemented UTC-nanosecond + UUID naming to allow safe parallel `DummyVecEnv` restarts.
3.  **Logging Optimization**: Reduced I/O overhead by silencing step-level traces.

---

## 3. Phase 4 Roadmap: Informed-Flow Gating
Based on the 150k forensics, the next bottleneck is **entry price quality**. We recommend:
- **Microprice Alpha Gating**: Block entries when `MidPrice` and `Microprice` (weighted by imbalance) diverge beyond 0.5 bps.
- **Volatility-Adjusted Gating**: Dynamically scale the 0.3 bps offset gate based on `rv_5s`.
- **Regime-Specific Execution**: Disable entries during identified `Shock` regimes.

## 4. Verification Results
- All bot processes (`bot-server`, `python training`) have been terminated.
- Checkpoints saved: [model_100k.zip](file:///c:/Bot%20mk3/python/runs_train/vnext_p3/model_100k.zip) (150k aggregate).
- Scorecards available: [report_100k_adversarial.json](file:///c:/Bot%20mk3/python/runs_train/vnext_p3/report_100k_adversarial.json).

---
*End of Phase 3.*
