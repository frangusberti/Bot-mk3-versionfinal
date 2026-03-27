# GUI Parameter Map (único vs repetido)

## Objetivo
Aclarar qué parámetros son únicos, cuáles están repetidos en la GUI y cuál debe ser el panel "dueño".

## Parámetros de leverage
- Aparecen en:
  - `Orchestrator` (Leverage Control)
  - estado resumido en tablas
- Dueño recomendado:
  - **Orchestrator > Leverage Control**
- Comentario:
  - mantener enteros (sin decimales) para evitar confusión operativa.

## Parámetros de drawdown/riesgo
- Aparecen en:
  - `Orchestrator` (Max Daily DD / Max Total Exp)
  - `Risk Panel` (DD diario/mensual/total)
- Dueño recomendado:
  - **Risk Panel** para límites duros de riesgo.
- Comentario:
  - evitar configurar DD en dos lugares con valores diferentes.

## Parámetros de policy/modelo
- Aparecen en:
  - `Orchestrator` (policy id, reload)
  - `Train/RL` (entrenamiento y evaluación)
- Dueño recomendado:
  - entrenamiento en tabs de Train/RL,
  - activación del modelo en Orchestrator.

## Parámetros potencialmente redundantes
1. Leverage simple (`Leverage` en Symbol Config) vs bloque completo de Leverage Control.
2. DD en Orchestrator vs DD en Risk Panel.

## Plan de simplificación sugerido
1. Marcar visualmente en GUI qué panel es "fuente de verdad".
2. Deshabilitar campos secundarios cuando haya panel dedicado activo.
3. Enviar tooltip de “este valor se sobreescribe por X panel”.
