# Auditoría: Cobertura y Colapso PPO

Resumen técnico de la cobertura conductual del BC y la tasa de invalidez en PPO.

## 1. Cobertura Conductual BC (Dataset v4)

Análisis de 10,000 muestras del experto:

| ID | Acción | Proporción | Status |
| :--- | :--- | :--- | :--- |
| **0** | **HOLD** | 28.99% | Cobertura media. |
| **1** | **OPEN_LONG** | 17.05% | Cobertura media. |
| **5** | **OPEN_SHORT** | 10.09% | Cobertura baja. |
| **8** | **CLOSE_SHORT**| 43.78% | **Sobre-cobertura** (sesgo por bloqueos). |
| **4** | **CLOSE_LONG** | 0.09% | Cobertura mínima (ruido). |
| **2, 3, 6, 7, 9** | **Otros** | **0.00%** | **Cobertura Cero**. |

**Conclusión Logits**: El actor BC deja logits de ~0.0058 (masas uniformes y bajas) para acciones nunca vistas (ADD, REDUCE, REPRICE). Esto significa que **el warm start es ciego** a la mitad del espacio de acciones, dejándolo al arbitrio de la inicialización de la red o la exploración ruidosa de PPO.

## 2. Auditoría de Invalid Actions (PPO Baseline)

Basado en el reporte de colapso de 50k pasos ([report_50k_baseline.json](file:///C:/Bot%20mk3/python/runs_train/vnext_p3_5/report_50k_baseline.json)):

*   **Tasa de Invalid Actions por Estado**:
    *   **FLAT**: **100%** (`ADD_LONG` seleccionado sistemáticamente sin posición).
    *   **LONG / SHORT**: **0%** (Nunca entró en posición, por lo que no hubo oportunidad de invalidar transiciones de salida).
*   **Top Invalid Action**: `ADD_LONG` (ID 2).
*   **Momento de Dominancia**: La transición ocurre en los primeros 10k-20k pasos, donde PPO abandona el sesgo de `CLOSE_SHORT` del BC (que daba 0 reward en FLAT) y converge a `ADD_LONG` (que también daba 0 reward antes del patch de penalidad).
*   **Telemetry**: El campo `action_counts` en el reporte gRPC es suficiente para detectar esto: un conteo alto de `ADD` con `total_trades = 0` es la firma inequívoca del colapso.

---

**Conclusión General**: El warm start actual **no es compatible** con un action space de 10 acciones para estados de mantenimiento de posición (ADD/REDUCE), ya que el experto no las utiliza. PPO comienza a ciegas en estas acciones y, ante la falta de penalidad inicial, el sistema "flotó" hacia `ADD_LONG` por deriva estocástica.
