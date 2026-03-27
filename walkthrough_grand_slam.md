# Walkthrough: Fase 21 - The 500k Grand Slam (Audit Final)

La extensión del entrenamiento de 300k a 500k ha sido un éxito **técnico** (estabilidad de run y activación), pero un fracaso **económico**. El modelo no ha logrado consolidar su Profit Factor y, de hecho, ha retrocedido hacia una zona de pérdidas consistentes.

## Resultados Consolidados (v15)

| Milestone | Net PnL | Profit Factor | Maker Fills | Spread Capture | HOLD % | Status |
|-----------|---------|---------------|-------------|----------------|--------|--------|
| **300k (Start)** | +0.007% | 3.62 | 8 | -0.18 bps | 12.7% | PASS |
| **350k** | -0.018% | 0.39 | 8 | -0.31 bps | 12.2% | **FAIL** |
| **400k** | -0.008% | 0.18 | 4 | -0.58 bps | 30.6% | **FAIL** |
| **450k** | -0.012% | 0.88 | 6 | -0.34 bps | 12.6% | **FAIL** |
| **500k (End)** | **-0.012%** | **0.00** | 4 | **-1.35 bps** | 9.9% | **CRITICAL FAIL** |

## Análisis Forense

1. **Colapso del Profit Factor**: El PF de 3.62 observado a los 300k resultó ser un artefacto de baja varianza o una "burbuja" de suerte en ese slice de validación. A medida que escalamos, el PF descendió por debajo de 1.0 en todos los checkpoints posteriores. 
2. **Degradación del Spread Capture**: El agente está pagando por entrar. Al llegar a los 500k, el spread capture es de **-1.35 bps**. Esto significa que sus órdenes maker están siendo ejecutadas en momentos de movimiento adverso tal que, al cerrarse, la pérdida por slippage/movimiento supera el market-making bonus.
3. **Churn Ineficiente**: El agente mantiene un uso elevado de `CLEAR_QUOTES` (21.6% @ 500k). Está cancelando órdenes activamente pero sin obtener fills de calidad a cambio.
4. **Ratio Maker**: El ratio se mantiene al 100% (siempre entra como maker), pero la toxicidad de los fills es el problema real.

## Veredicto de Fase 21

Según la **Regla de Decisión A/B/C**:
- **Opción C**: El comportamiento económico ha degradado (PF < 1, Net < 0). 
- **Acción**: **STOP and REASSESS**. No podemos avanzar a datasets volátiles con un agente que pierde dinero en el dataset "Golden" (limpio).

## Siguientes Pasos Sugeridos

- **Revisión de Recompensas**: El `maker_fill_bonus = 0.0060` (60 bps) parece ser devorado totalmente por el movimiento adverso. Necesitamos incentivar spreads más anchos o una gestión de inventario más estricta.
- **Auditoría de Latencia/Execution**: Verificar si el simulador está siendo demasiado generoso con los fills o si el agente está explotando una micro-ineficiencia que desaparece bajo optimización estricta.
- **Hyperparameter Triage**: El LR de 1e-4 y el Entropy Coef de 0.03 podrían haber causado una convergencia prematura a una política defensiva inútil.
