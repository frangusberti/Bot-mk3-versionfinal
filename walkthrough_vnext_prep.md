# Walkthrough: Preparación Fase 3.5 (vNext Bottleneck Resolution)

He completado la re-arquitectura de los scripts de entrenamiento para alinearlos con la rama **vNext** y resolver el cuello de botella de inactividad detectado en la Fase 3.

## 1. Calibración de Gates (Diagnóstico)

Ejecuté un test de sensibilidad sobre el dataset "stage2_train" para determinar el impacto del `min_post_offset_bps`.

| Threshold | Pass Rate (Teacher) | Veredicto |
|-----------|----------------------|-----------|
| 0.30 bps | 88.1% | Demasiado restrictivo para exploración RL inicial |
| 0.25 bps | 90.4% | Aceptable |
| **0.20 bps** | **100.0%** | **Óptimo para BC Pre-training** |
| 0.10 bps | 100.0% | Demasiado permisivo (riesgo de v15) |

**Decisión**: Hemos fijado el gate en **0.20 bps**. Esto asegura que el Teacher pueda "enseñar" todas sus intenciones sin bloqueos, mientras mantiene una selectividad superior a la rama fallida v15.

## 2. Infraestructura Lifecycle (10 Acciones)

He refactorizado los siguientes componentes para soportar la distribución de 10 acciones (`OPEN`, `ADD`, `REDUCE`, `CLOSE`):

- **[teacher_vnext_prep.py](file:///C:/Bot%20mk3/python/teacher_vnext_prep.py)**: Ahora mapea correctamente las intenciones del experto (7 acciones) al espacio de lifecycle (10 acciones). Sin este cambio, el BC habría aprendido acciones aleatorias.
- **[behavior_cloning_train.py](file:///C:/Bot%20mk3/python/behavior_cloning_train.py)**: Actualizado para 10 salidas y normalización compatible con `VecNormalize`.
- **[vnext_scorecard.py](file:///C:/Bot%20mk3/python/vnext_scorecard.py)**: Nuevo evaluador con reglas "Fail-Fast" (ej. abortar si hay 0 fills a los 50k pasos).

## Behavior Cloning & Lifecycle Audit (Alpha Phase)

The 100,000-step Alpha dataset was generated with zero gate-related filtering, providing a high-fidelity semantic base.

### Training Results
- **Accuracy**: 99.82% (10 epochs)
- **Diversity**: All 10 actions represented in the distribution.

### 10,000-Step Semantic Audit
| Metric | Value | Status |
| :--- | :--- | :--- |
| **Total Steps** | 10,000 | - |
| **HOLD Actions** | 37.3% | Active |
| **REDUCE Actions**| 0.81% | **VALIDATED** |
| **CLOSE Actions** | 0.06% | **VALIDATED** |
| **OPEN Actions**  | 6.31% | Healthy |
| **ADD Actions**   | 55.4% | Robust |
| **Gate Compatibility** | 100% | Pass (0.20 bps) |

### Observation: Surgical Preservation
Live logs confirm that `RL_EXIT_BLOCKED` is successfully preventing low-quality exits (uPnL < 5 bps and > -30 bps), forcing the agent to stay in high-quality trades.

```text
[INFO] RL_EXIT_BLOCKED: uPnL=-19.6bps (Floor=5.0, SL=-30.0)
```

## Next Steps: RL Fine-tuning
The policy is now semantically ready for reinforcement learning. The next phase will execute [ppo_vnext_p3_5.py](file:///C:/Bot%20mk3/python/ppo_vnext_p3_5.py) with:
1. **Low Learning Rate** ($1 \times 10^{-5}$) to preserve BC knowledge.
2. **Target KL** ($0.015$) to prevent policy collapse.
3. **Fail-Fast Scorecard** to monitor real-time inactivity.

## 3. Estado de Generación de Datos

- **Archivo**: [data/teacher_vnext_200k.parquet](file:///C:/Bot%20mk3/data/teacher_vnext_200k.parquet)
- **Progreso**: ~8% completado (est. 20-30 min restantes).
- **Calidad**: Gate-aligned (0.20 bps).

Una vez finalizado el dataset, procederé automáticamente al **BC Pre-training** para generar el modelo base (`vnext_bc_p3_5.zip`).
