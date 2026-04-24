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

**Nota importante (corrección):** los `normalized_events.parquet` producidos por `BuildDataset` contienen **eventos normalizados crudos** (`stream_name`, `event_type`, `price`, `best_bid`, `best_ask`, …), **no** una tabla `f_0..f_199`. No existe hoy en el repo una materialización del feature set v8 como matriz por barra lista para ML. Por eso el pipeline supervisado **no parte de normalized_events** y **tampoco reconstruye v8**; construye su propio set de features desde los raw zips de Binance Futures UM.

Tareas:

1. Script `python/bot_ml/supervised/build_dataset.py`:
   - **Input real**: zips ya descargados en `data/raw/binance/futures/um/` (`aggTrades`, `fundingRate`, `metrics`). Opcionalmente `bookTicker` en fases siguientes.
   - **Agregación a barras de 5m**: para cada bar con timestamp `t` (múltiplo de 5 min UTC), se agrupan todos los `aggTrades` con `transact_time ∈ [t, t+5m)`. Timestamp que manda en la fila = `t` (inicio de barra, convención left-closed).
   - **Order flow per barra**: `buy_vol/sell_vol/buy_notional/sell_notional` derivados de `is_buyer_maker` (False = market buy, True = market sell).
   - **Barras sin actividad**: se reindexan y rellenan con `close` ffill + volúmenes 0, así la grilla temporal queda continua.
   - **Funding y OI**: merge `as-of backward` contra la barra 5m (funding se publica cada 8h, OI cada 5 min).
   - **Features calculadas** (no `f_0..f_199` — set propio, documentado en `build_manifest.json`): retornos log a 1/3/6/12/24/48/96 barras, RV rolling, RSI, ATR, MACD-like, posición en rango rolling, OFI por barra y rolling, intensidad de trades, z-score de volumen, contexto multi-timeframe 15m/1h/4h (return, RV, slope).
   - **Output**: `bars_features.parquet` con una fila por barra 5m y un `build_manifest.json` que lista las columnas reales producidas.

2. Labeling — dos modos soportados (módulo `labeling.py`, aplicado dentro de `train.py`, no en `build_dataset.py` — así podemos retocar horizontes sin rebuild):
   - **Regresión**: `fwd_ret_H` = `log(close[t+H]/close[t])`. Default H=12 (1h forward).
   - **Clasificación triple-barrier**: etiqueta `{-1, 0, +1}` según qué barrera toca primero el precio en los próximos `H` bars. Barrera = `k × atr_pct_48` aplicada sobre `high/low` intra-bar, con una regla de desempate por retorno al final del horizonte.

3. Split temporal estricto:
   - train: 2023-11 → 2024-01
   - val: 2024-02
   - test: 2024-03
   - **Purge**: se descartan las últimas `H` barras de train y las primeras `H` barras de val/test. Esto asegura que ninguna etiqueta forward-looking de un split cruza al siguiente. (Hoy implementado como `purge_bars = horizon`.)
   - **Embargo explícito**: fuera de esto no hay embargo adicional — si en fases siguientes usamos CV con bloques internos, habrá que agregarlo.

**Deliverable**: `C:\Bot mk3\python\runs_train\supervised_btc_v1\dataset\bars_features.parquet` + `build_manifest.json`.

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

**Regla de uso de splits (corrección importante):**

- `train`: entrenamiento de pesos del modelo.
- `val`: (a) early stopping, (b) calibración del `threshold` de señal y cualquier otro hiperparámetro de decisión. Todo lo que toque `val` es **in-sample para decisión**.
- `test`: **reporte final únicamente**. No se toca para ninguna decisión de modelo, umbral, ni regla. Se evalúa **una vez** con los hiperparámetros ya congelados en val.

Consecuencia: las métricas sobre `val` **no son** métricas de generalización; son métricas de ajuste. El número que se reporta como "performance esperada" es siempre el de `test`. Cuando aparezcan ambas en tablas, tienen que estar etiquetadas explícitamente como "val (in-sample decisión)" vs "test (out-of-sample)".

Si en el futuro queremos tunear más cosas y `test` no alcanza para robustez, pasamos a walk-forward rolling con múltiples bloques `(train, val, test)` en vez de un único split.

Script `python/bot_ml/supervised/evaluate.py`:

1. Convertir predicciones → señal de trading con regla simple:
   - Si `|pred| > threshold`, entrar en dirección del signo.
   - `threshold` calibrado **únicamente sobre val**.
   - Position sizing fijo inicial: 15% del equity por trade, leverage 3×.
2. Simular con costos reales:
   - Fee taker Binance: 0.05% por lado = 0.10% round-trip.
   - Slippage: 1 bp por lado (conservador para BTC).
   - Funding rate aplicado si hold >8h.
3. Métricas (reportadas por separado para val y test, con la etiqueta de arriba):
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

**Deliverable**: `eval_report.md` con tabla comparativa, con la separación val/test explícita.

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
