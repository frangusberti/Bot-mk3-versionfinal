# Supervised pipeline (pivot 2026-04-24)

LightGBM-based replacement for the PPO generation loop. See
`bitacora/2026-04-24_pivot_supervisado_lightgbm.md` for rationale.

## Módulos

- `build_dataset.py` — lee zips de Binance Futures UM (`aggTrades`,
  `fundingRate`, `metrics`) → agrega a barras de 5m → features
  multi-timeframe + order-flow + funding + OI → parquet.
- `labeling.py` — forward returns y etiquetas triple-barrier.
- `train.py` — entrena LightGBM (regresión y clasificación) con split
  temporal estricto + purging.
- `evaluate.py` — convierte predicciones en señal → simula P&L con fees
  (5 bps) + slippage (1 bp) → Sharpe, Sortino, DD, PF, hit rate.
- `run_pipeline.py` — end-to-end: build → train → evaluate.

## Requisitos

```
py -m pip install lightgbm pandas pyarrow numpy
```

## Uso

```bash
py -m bot_ml.supervised.run_pipeline \
  --symbol BTCUSDT \
  --run-name supervised_btc_v1 \
  --train-months 2023-11 2023-12 2024-01 \
  --val-months   2024-02 \
  --test-months  2024-03 \
  --horizon 12
```

Outputs quedan en `C:\Bot mk3\python\runs_train\supervised_btc_v1\`:

- `dataset/bars_features.parquet`
- `models/lgbm_regression.txt`
- `models/lgbm_classification.txt`
- `models/feature_importance.csv`
- `models/preds_{reg,cls}_{train,val,test}.parquet`
- `eval/metrics_{reg,cls}_{val,test}.json`

## Notas

- El split incluye un **purge** de `horizon` barras entre tramos para
  evitar leakage por etiquetas que miran al futuro.
- Los features vienen todos de precio + volumen + flow + funding + OI.
  No se usa el feature engine Rust v8 — pipeline deliberadamente
  independiente para iterar rápido.
- Costos por defecto: fee taker 5 bps + slippage 1 bp por lado
  (round-trip ≈ 12 bps). Ajustables por CLI de `evaluate.py`.
- Baseline actual de posición: apertura/cierre por barra — no hay
  compounding intra-trade. Es un proxy honesto, no un backtester de
  ejecución realista. Fase siguiente del plan: backtester con
  stops/takes y funding aplicado en holds >8h.
