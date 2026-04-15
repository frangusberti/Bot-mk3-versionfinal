# Audit de Edge Predictivo y Features

He completado el análisis de 5,000 pasos de datos reales de mercado (HOLD policy) usando el dataset `stage2_eval`.

## 1) Relación con Retorno Futuro (Edge)

| Feature | Corr (Spearman) 5s | Edge Condicional (Avg Ret 5s) |
| :--- | :--- | :--- |
| **microprice_minus_mid_bps** | 0.068 | ~0.04 bps (cuando MP > 0.5) |
| **trade_imbalance_5s** | **NaN** | **Feature colapsada en 1.0** |
| **rv_5s (volatilidad)** | -0.001 | Negativo / Inexistente |
| **spread_bps** | -0.016 | Negativo |

## ITR Smoke Pilot Results (25k Steps)

We executed a short training burst using **MaskablePPO** and **Schema v7** (166-dim) to baseline the behavior.

### Behavioral Scorecard
| Metric | Result | Note |
| :--- | :--- | :--- |
| **Invalid Rate** | **0.00%** | Action masking is 100% effective. |
| **Total Trades** | 74 | Low frequency (expected for 25k steps). |
| **Action Distribution** | 56% HOLD, 20% REDUCE_SHORT | Strong bias towards short side management. |
| **Net PnL (Eval)** | Abnormal | Very high values, likely simulation scale noise. |
| **Avg Win/Loss Hold** | 0 ms | Indicates immediate exits or lack of telemetry capture. |

### Feature Validity Audit (CRITICAL)
The audit revealed that most new features are **NOT yet active** during live gRPC sessions:
*   **Validity at 0m**: 3.6% (~3 features)
*   **Validity at 15m**: 7.2% (~6 features)

> [!CAUTION]
> **CONCLUSION**: The agent is effectively blind to the new technical indicators (RSI, BB, Slopes). Only ~6 features are being emitted with a valid mask. This suggests a mismatch between the `FeatureEngineV2` logic and the runtime `FeatureRow` population in the RL service, or the dataset lacks context for warmups.

---

## Next Steps
1.  **Debug Feature Emission**: Investigate why `rsi_1m`, `bb_pos_5m`, etc., are returning `mask=0.0` even after 15 minutes of warmup.
2.  **PnL Calibration**: Address the abnormal PnL scaling before long-term training.
3.  **Reward Tuning**: Once features are valid, pivot to the "Terminal Reward" structure.
 **Microprice Edge:** Aunque existe una pequeña correlación (0.06), el retorno promedio de solo **0.04 bps** es insuficiente para mover el gross PnL hacia positivo de forma consistente.

## 3) Capacidad de vencer ~4 bps Roundtrip
- **Probabilidad de movimiento > 4 bps en 5s:** Solo **4.9%**.
- En el régimen actual, el "edge" promedio detectado (0.04 bps) representa apenas el **1% del costo transaccional (4 bps)**.
- **Veredicto:** Con las features actuales y su calibración presente, es **matemáticamente imposible** ser rentable. El ruido del mercado y los costos de ejecución superan la señal en un ratio de 100:1.

## 4) Veredicto Causal
1. **Falla de Features:** El `trade_imbalance_5s` constante distorsiona la policy hacia el lado Long y elimina el principal filtro de adverse selection (flow).
2. **Falta de Señal:** Incluso las features que "funcionan" (microprice) tienen un impacto tan pequeño que no justifican el costo de la fee Maker (2 bps x 2).

## 5) Propuesta de Intervención (Siguiente Paso)

> [!IMPORTANT]
> No escalar training. El agente no puede aprender lo que no puede ver.

1.  **Fix Imbalance:** Investigar por qué `FeatureEngineV2` satura el imbalance en 1.0 (posible división por cero o inicialización incorrecta).
2.  **Aumentar horizonte de features:** Las micro-features a 1s-5s son demasiado volátiles para el `decision_interval` de 1s.
3.  **Filtrado Estricto (Entry Veto):** Subir el `entry_veto_threshold_bps` de 0.2 a 0.5 para forzar al agente a entrar solo con señales mucho más fuertes, intentando capturar un spread mayor que compense la fee.

---
**¿Cómo procedemos? ¿Querés que investigue el fix del imbalance primero?**
