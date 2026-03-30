# Auditoría de Observación: Estado Posicional (BOTMK3)

Análisis de la visibilidad del inventario en el vector de entrada (148-dim).

## 1. Features de Estado Posicional

El agente recibe el estado de cuenta en el bloque **G (Account State)** del vector de observación:

*   **Índice 48: `position_flag`**
    *   `1.0` -> LONG
    *   `0.0` -> FLAT
    *   `-1.0` -> SHORT
*   **Índice 49: `latent_pnl_pct`**
    *   Representa el PnL no realizado relativo al equity (normalizado).
*   **Índices 122 y 123 (Masks)**:
    *   Indican si el dato es válido (`1.0`) o si debe ignorarse (`0.0`).

## 2. Presencia en Train vs Runtime

*   **Runtime ([rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs))**: Se inyecta en cada paso desde el `exec_engine`. Está siempre presente.
*   **Training (`Parquet`)**: Las columnas están presentes en el schema y se graban durante la generación del experto.
*   **Dataset `golden_l2`**: Al ser un replay del libro de órdenes, las columnas de cuenta vienen originalmente vacías, pero el pipeline de entrenamiento las **enriquece** dinámicamente durante el loop de simulación.

## 3. Integridad y Normalización

*   **Mascarado**: Los datos de cuenta **no se mascan** en runtime (Mask = 1.0). Si el agente ve un 0.0 en el índice 48 con máscara 1.0, tiene la certeza de que está FLAT.
*   **Normalización/Clamping**:
    *   `position_flag` es discreto y exacto {-1, 0, 1}.
    *   `latent_pnl_pct` se calcula como [(uPnL / Equity) * 100](file:///C:/Bot%20mk3/python/teacher_policy.py#199-205) y se pasa por un `clamp_pct` de `[-1.0, 1.0]`. 
    *   **Nota crítica**: Debido al factor `* 100` y al clamp de `1.0`, el agente **pierde resolución** para ganancias/pérdidas mayores al 1% (ve un "techo" de saturación). Sin embargo, para la escala de trading de alta frecuencia (targets de 2-10 bps), la resolución es adecuada.

## 4. Ejemplos de Observación Real (v4 Dataset)

| Estado | Idx 48 (Pos) | Idx 49 (PnL) | Idx 122 (Mask Pos) | Idx 123 (Mask PnL) |
| :--- | :--- | :--- | :--- | :--- |
| **FLAT** | `0.0` | `0.0` | `1.0` | `1.0` |
| **LONG** | `1.0` | `-0.0759` | `1.0` | `1.0` |
| **SHORT** | `-1.0` | `-0.5185` | `1.0` | `1.0` |

---

**Conclusión**: El agente tiene visibilidad **inequívoca** de su estado de inventario. No hay ambigüedad entre FLAT, LONG y SHORT en el vector de entrada.
