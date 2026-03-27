# Forensic Audit Report: 300k vs 450k vs 500k

Este análisis compara la calidad de ejecución y la "toxicidad" de los trades capturados en tres hitos críticos del entrenamiento.

## 📊 Tabla Comparativa Forense (10k steps)

| Métrica | @ 300k (Base) | @ 450k | @ 500k (Final) |
| :--- | :---: | :---: | :---: |
| **Total Fills** | 8 | 6 | 4 |
| **Avg Spread Capture** | -0.19 bps | -0.34 bps | **-1.43 bps** |
| **MTM 1s (Fav/Adv)** | +2.0 pts (Fav) | -1.5 pts (Adv) | **-4.2 pts (Adv)** |
| **MTM 5s (Fav/Adv)** | -1.2 pts (Adv) | -6.8 pts (Adv) | **-8.5 pts (Adv)** |

## 🔍 Hallazgos Principales
## 1. Forensic Microstructure Table (Deep Audit)

| Checkpoint | Fill ID | Side | Mid-at-Fill | Price | Spread Capture | AS 5s (bps) | Inventory | Opening? | Imbalance Top1 | Regime |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **300k** | 0 | Sell | 70307.9 | 70306.4 | -0.22 | **+1.78** | 0 -> -0.02 | Yes | -0.67 | Shock |
| **300k** | 2 | Buy | 70300.6 | 70301.8 | -0.16 | -1.35 | 0 -> +0.02 | Yes | -0.28 | Shock |
| **300k** | 4 | Buy | 70317.1 | 70294.2 | +3.26 | -2.10 | 0 -> +0.02 | Yes | -0.29 | Shock |
| **300k** | 6 | Sell | 70314.5 | 70306.1 | -1.20 | -3.82 | 0 -> -0.02 | Yes | -0.29 | Shock |
| **450k** | 0 | Sell | 70305.3 | 70298.3 | -1.00 | **+1.12** | 0 -> -0.02 | Yes | 1.63 | Shock |
| **450k** | 2 | Sell | 70294.1 | 70291.1 | -0.43 | -0.31 | 0 -> -0.02 | Yes | -0.67 | Shock |
| **450k** | 4 | Sell | 70302.3 | 70293.9 | -1.20 | **+2.61** | 0 -> -0.02 | Yes | 1.49 | Shock |
| **500k** | 0 | Sell | 70305.3 | 70298.3 | **-1.00** | **+1.12** | 0 -> -0.02 | Yes | 1.63 | Shock |
| **500k** | 2 | Sell | 70317.1 | 70291.1 | **-3.70** | **+2.10** | 0 -> -0.02 | Yes | -0.29 | Shock |

> [!IMPORTANT]
> **Adverse Selection (AS 5s)** is signed: Positive (+) means the price moved in favor of the trade (e.g., down for a Sell). Negative (-) means price moved against it.

## 2. Causal Diagnosis

### The "Slippage for Edge" RL Trap
The data reveals a clear causal shift in the policy's evolution:

1.  **At 300k (The Lucky Regime)**:
    - The agent was poorly selective (Avg Opening AS 5s: **-1.37 bps**). It was getting run over.
    - However, it kept fills relatively "close" to mid (Spread Capture: -0.2 to -0.4 bps).
    - It survived because it didn't pay much slippage, and the drift wasn't permanent enough to kill it in 10k steps.

2.  **At 500k (The Toxic Execution Regime)**:
    - The agent successfully learned to identify favorable moves! (Avg Opening AS 5s: **+1.61 bps**). It is selling when the price is about to drop.
    - **THE TRAP**: To catch these moves, it has become hyper-aggressive, crossing the spread and accepting massive slippage (Avg Spread Capture: **-2.35 bps**).
    - **Economic Result**: Slippage (2.35) > Alpha (1.61) + Fees. The agent is effectively "donating" its directional edge to the exchange and the other maker.

### 300k Classification: **(B) Variance / Lucky Checkpoint**
The 300k checkpoint had no structural edge. Its directional selectivity was negative. It looked "good" only because its execution was cheap and the market noise didn't punish its toxic entries immediately. As soon as the agent tried to improve its alpha (at 500k), the PPO objective pushed it toward aggressive taking, which is economically terminal for a MM.

## 3. Evidence-Backed Recommendation

Do NOT restart 300k with the same reward. The 300k state is a dead end.

**Proposed Reward v6 Principles**:
- **Slippage Ceiling**: Introduce a hard penalty if [price](file:///c:/Bot%20mk3/crates/bot-data/src/features/engine.rs#379-382) deviates more than `spread_bps` from [mid](file:///c:/Bot%20mk3/crates/bot-data/src/features/engine.rs#379-382).
- **Signed AS Penalty**: Direct penalty for `AS 1s/3s/5s < 0` to force selectivity *without* increasing aggressiveness.
- **Microprice Constraint**: Limit `POST_BID` actions when `microprice` is significantly lower than [mid](file:///c:/Bot%20mk3/crates/bot-data/src/features/engine.rs#379-382).

---
## 🚀 Próximos Pasos (Hacia Reward v6)
Para Reward v6, no podemos simplemente "ajustar números". Necesitamos:
- **Penalizar agresivamente el MTM adverso** (si el precio se mueve en contra > X bps en 1s, penalizar el fill).
- **Endurecer el `reward_distance_to_mid_penalty`** para evitar que el agente se quede colgado en niveles viejos.
- **Aumentar la penalización por fills tóxicos** suministrados por el simulador.
