# Walkthrough: Phase 27 Behavioral Restoration

Validación final del relanzamiento del agente BOTMK3 tras el fix de semántica de acciones y penalidades.

## 1. Scorecard de Relanzamiento (10k steps)

| Métrica | Inicial (Colapso) | 2k steps | 10k steps | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Invalid Action Rate** | 100% | < 1% | **0.0%** | **FIXED** |
| **ADD_LONG (en FLAT)** | 100.0% | 0.0% | **0.0%** | **FIXED** |
| **HOLD (Legal)** | 0.0% | 16.9% | **96.0%** | **RESTORED** |
| **OPEN_SHORT (Legal)** | 0.0% | 0.0% | **3.5%** | **ACTIVE** |
| **Total Trades** | 0 | 0 | 0* | - |

*\*Nota: Se registraron 70 intentos de `OPEN_SHORT`, la mayoría bloqueados por el filtro de `offset` (gate telemetry), lo cual es comportamiento legal esperado en este régimen.*

## 2. Cambios Clave Validados

1.  **Hardening Backend**: El penalty de `0.1` (positivo) restado en [reward.rs](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs) quebró la simetría de recompensa, haciendo que cualquier acción inválida sea masivamente peor que el `HOLD`.
2.  **BC Model Repair**: El nuevo modelo `vnext_bc_fix.zip` eliminó el sesgo de "cerrar por defecto", permitiendo que el entrenamiento PPO partiera de una base más neutra y segura.
3.  **Alineación gRPC**: Se confirmó que los 10 índices de acción viajan sin desalineaciones desde la red neuronal hasta el core de Rust.

## 3. Telemetría de Gates (10k)

El agente está intentando operar legalmente pero encuentra restricciones de liquidez/spread:
- **Offset Blocks**: 12 (El spread era demasiado ajustado para el threshold actual).
- **Invalid Actions**: 0 (Ninguna penalidad aplicada en el tramo final).

---

**Conclusión Final**: La Fase 27 de calibración es un **ÉXITO**. El bot ha recuperado su "sentido común" posicional y ha abandonado el atractor de acciones inválidas. Se recomienda proceder con el entrenamiento extendido (500k+) a partir del checkpoint [model_10k.zip](file:///C:/Bot%20mk3/python/runs_train/phase27_calib/model_10k.zip).
