# Plan de implementación — Pivot supervisado

Fecha: 2026-04-24
Rama: `pivot/supervised-lightgbm`

## Principios del plan

- **Incremental**: cada fase produce un artefacto medible y verificable.
- **Comparable**: cada modelo nuevo se mide contra un baseline concreto (heurística actual, PPO piloto).
- **Honesto**: walk-forward estricto, sin leakage, con costos realistas.
- **Reutilizable**: el feature engine v8 y el dataset existente no se reescriben.

## Fases

### Fase 0 — Baseline y congelamiento (½ día)

- Dejar el piloto PPO de 20 gen completar (en background) para tener número de referencia.
- Congelar el estado actual en la rama `pivot/supervised-lightgbm`.
- Documentar métricas del baseline heurístico actual y de PPO piloto cuando termine.

**Deliverable**: documento de baselines con Sharpe, DD, hit rate, profit factor sobre val y test.

---

### Fase 1 — Dataset tabular para ML supervisado (1 día)

Necesitamos transformar el `normalized_events.parquet` de cada mes en una matriz `(features, label)` lista para LightGBM.

Tareas:

1. Script `python/bot_ml/build_supervised_dataset.py`:
   - Input: lista de `dataset_id` del índice (`index/datasets_index.json`).
   - Output: un `supervised.parquet` unificado con columnas `[ts, symbol, f_0..f_199, close, future_ret_3, future_ret_12, future_ret_48, label_cls]`.
   - Resample a barras de 5m (ya vienen así por el dataset v8).
   - Calcular retorno forward a 3, 12 y 48 barras (15min / 1h / 4h).

2. Labeling — dos modos soportados:
   - **Regresión**: target = retorno forward en barras de 5m (por defecto 12 barras = 1h).
   - **Clasificación triple-barrier**: etiqueta `{-1, 0, +1}` según si precio toca barrera superior, inferior o ninguna en horizonte máximo. Barrera ajustada por volatilidad (k × ATR).

3. Split temporal estricto:
   - train: 2023-11 → 2024-01
   - val: 2024-02
   - test: 2024-03
   - **Gap** entre splits (purging) de al menos `max_horizon` barras para evitar leakage por solapamiento.

**Deliverable**: `C:\Bot mk3\python\runs_train\supervised_btc_v1\dataset\supervised.parquet` + `manifest.json`.

---

### Fase 2 — LightGBM baseline (1 día)

Script `python/bot_ml/train_lightgbm_baseline.py`:

1. Cargar dataset supervisado.
2. Entrenar:
   - Modelo A: regresión sobre `future_ret_12` (LightGBM regressor).
   - Modelo B: clasificación triple-barrier `{-1, 0, +1}` (LightGBM multiclass).
3. Hiperparámetros iniciales conservadores:
   - `num_leaves`: 31
   - `min_data_in_leaf`: 200 (regularización fuerte para evitar overfit)
   - `feature_fraction`: 0.7
   - `bagging_fraction`: 0.7
   - `bagging_freq`: 5
   - `learning_rate`: 0.02
   - `num_boost_round`: hasta 2000 con early stopping (100 rounds) sobre val
4. Guardar modelo, feature importance, predicciones sobre val y test.

**Deliverable**: modelo entrenado + `metrics.json` con IC (information coefficient), MAE, accuracy por clase, y curva de equity simulada sobre val y test.

---

### Fase 3 — Evaluación financiera honesta (½ día)

Script `python/bot_ml/eval_supervised.py`:

1. Convertir predicciones → señal de trading con regla simple:
   - Si `|pred| > threshold`, entrar en dirección del signo.
   - `threshold` calibrado sobre val (no test).
   - Position sizing fijo inicial: 15% del equity por trade, leverage 3×.
2. Simular con costos reales:
   - Fee taker Binance: 0.05% por lado = 0.10% round-trip.
   - Slippage: 1 bp por lado (conservador para BTC).
   - Funding rate aplicado si hold >8h.
3. Métricas:
   - Sharpe anualizado
   - Sortino
   - Max drawdown
   - Profit factor
   - Hit rate
   - Trade count
   - Avg trade duration
   - Turnover
4. Comparar contra:
   - Heurística actual (`regime_router` fallback)
   - PPO piloto (cuando esté)
   - Buy & hold BTC

**Deliverable**: `eval_report.md` con tabla comparativa.

---

### Fase 4 — Meta-labeling (1 día, condicional)

Sólo si Fase 3 muestra edge positivo pero noisy.

Script `python/bot_ml/train_meta_label.py`:

1. Usar predicciones del modelo primario como features adicionales.
2. Entrenar clasificador binario: "¿este trade va a ser ganador?".
3. Filtrar trades con probabilidad < umbral.
4. Re-evaluar con Fase 3.

**Deliverable**: modelo meta + comparación "primario solo" vs "primario + meta".

---

### Fase 5 — Integración al policy_server (1 día)

1. Nueva policy pluggable: `python/bot_policy/policies/supervised_lightgbm.py`.
2. Cargar modelo LightGBM en arranque.
3. Inferencia en tiempo real: recibir features v8 → pred → decisión.
4. Integrar al `regime_router` como una de las opciones (coexistiendo con heurística y futuro PPO).
5. Flag de config para elegir policy activa.

**Deliverable**: policy_server sirve predicciones del modelo LightGBM en paper.

---

### Fase 6 — Paper-live shadow (continuo)

1. Activar la nueva policy en PAPER sobre BTC solamente.
2. Correr en paralelo con heurística actual.
3. Comparar decisiones y P&L día a día.
4. Si después de N días (ej. 14) el modelo supervisado supera a la heurística en paper-live, promover.

**Deliverable**: logs de paper-live + dashboard comparativo.

---

## Prioridades inmediatas (próximas horas)

En este mismo commit arrancamos con:

1. **Fase 1**: script de construcción de dataset supervisado.
2. **Fase 2**: primer entrenamiento LightGBM baseline.
3. **Fase 3**: evaluación básica sobre val y test.

Meta-labeling, integración al policy_server y paper-live quedan para commits siguientes una vez validado que la señal base existe.

## Riesgos y mitigaciones

| Riesgo | Mitigación |
|---|---|
| Leakage por feature que incluye info del futuro | Auditar feature engine v8 antes de entrenar; purging entre splits |
| Overfitting por sobre-tuning de hiperparámetros | Fijar hiperparámetros conservadores primero; tuning sólo si baseline muestra edge |
| Edge desaparece al agregar costos | Costos aplicados desde Fase 3, no después |
| Modelo funciona en backtest y falla en paper-live | Paper-live shadow obligatorio antes de LIVE |
| Dataset demasiado chico (5 meses) | Ampliar a más meses una vez validado el pipeline |

## Qué NO vamos a hacer en esta etapa

- No vamos a tunear hiperparámetros antes de tener baseline.
- No vamos a agregar features nuevos antes de medir los existentes.
- No vamos a tocar el feature engine Rust v8.
- No vamos a retirar PPO del código — queda congelado como opción.
- No vamos a entrenar sobre múltiples pares hasta que BTC funcione.
- No vamos a pagar por datos premium hasta que lo gratis esté exprimido.
