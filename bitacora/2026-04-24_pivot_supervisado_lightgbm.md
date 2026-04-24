# Pivot estratégico: de PPO a supervisado + LightGBM

Fecha: 2026-04-24
Rama: `pivot/supervised-lightgbm`

## Qué vamos a hacer

Girar el timón del método de entrenamiento del bot.

Hasta hoy el cerebro del bot se entrena con **PPO (Reinforcement Learning on-policy)** en un loop de "generaciones" sobre datos históricos de BTC. A partir de este pivot, el método principal pasa a ser:

1. **Modelo supervisado (LightGBM / gradient boosting)** que predice retorno forward a N barras.
2. **Meta-labeling (López de Prado)** como segunda capa: un clasificador decide si tomar o no la señal del primario.
3. **Regime routing** sobre ambos modelos para adaptar la decisión al régimen de mercado (tendencia, rango, alta vol, muerto).
4. **Reglas explícitas de sizing y gestión de riesgo**, separadas del modelo de señal.
5. **RL queda como fase de refinamiento opcional**, no como método principal. Si se retoma, será offline RL (IQL / Decision Transformer), no PPO.

## Por qué lo hacemos

El informe técnico que motivó este pivot identificó problemas concretos con PPO para nuestro caso:

1. **Sample efficiency pésima**. PPO necesita millones de pasos. Tenemos ~43k barras de 5m en 5 meses de BTC. Relación datos/dimensionalidad (200 features) muy desfavorable → overfitting casi garantizado.

2. **Reward ruidoso y sparse**. El P&L por trade tiene más ruido que señal. PPO aprende del ruido tanto como de la señal.

3. **Credit assignment roto**. Entradas correctas que salen mal por shocks posteriores son castigadas. Entradas incorrectas que salen bien por suerte son premiadas.

4. **No modela non-stationarity**. Policy entrenada en 2023-11/2024-03 no necesariamente generaliza a mercados posteriores.

5. **Backtest ≠ vivo**. PPO entrenado sin modelo de ejecución realista suele colapsar al pasar a paper-live.

6. **PPO es peor opción para datos offline**. Asume rollouts on-policy. Para aprender de un dataset histórico fijo, los métodos correctos son CQL / IQL / Decision Transformer.

Gradient boosting supervisado, en cambio:

- Es **10-100× más sample efficient** que PPO.
- Entrena en **minutos, no horas**.
- Da **feature importance interpretable** (sabemos qué mira el modelo).
- **Es lo que hedge funds cuantitativos usan en producción**. RL es más moda académica que estándar industrial.
- Se presta naturalmente a **walk-forward honesto** y a **meta-labeling**.

## Objetivo final

Un bot de trading de futuros cripto que:

- Parta de capital modesto ($1500 USD).
- Use apalancamiento de futuros de forma inteligente (no para amplificar apuestas malas).
- Lea los mejores datos gratuitos disponibles (técnicos y cuantitativos).
- Logre retornos compuestos consistentes con drawdowns controlados.
- Demuestre rentabilidad en paper-live antes de arriesgar capital real.
- Sólo después de probar rentabilidad: evaluar datos premium (L2 order book, on-chain premium, sentiment feeds) y hardware.

### Métricas objetivo honestas

- **Sharpe anualizado**: 1.0-2.0 (todo lo arriba en backtest es sospechoso de overfit).
- **Drawdown máximo**: <25%.
- **Retorno anual**: 30-80%.
- **Hit rate**: >52% con profit factor >1.3, o hit rate menor con profit factor >2.
- **Costos reales**: fees Binance Futures (maker 0.02% / taker 0.05%) + funding + slippage modelado.

## Qué dejamos atrás (por ahora)

- **El loop de 500 generaciones PPO**: queda archivado. El piloto de 20 gen puede terminar de correr para tener un baseline de comparación, pero no es el camino principal.
- **El stack v8 OBS_DIM=200 para PPO**: el feature engine se mantiene (sirve para ambos métodos), pero el consumidor principal cambia.
- **La idea de "un cerebro único que decide todo"**: ahora explícitamente separamos señal (modelo) de sizing (reglas) de ejecución (código determinístico).

## Qué NO cambia

- El **pipeline de descarga de histórico Binance Futures UM** (`download_binance_history.py`, `convert_aggr_trades.py`). Los raw zips siguen siendo la fuente.
- La **infraestructura de servicios** (bot-server, policy_server, paper_dashboard).
- El **regime_router** como capa de decisión superior.
- La **GUI** y el monitoreo operativo.
- El **gate de walk-forward** como criterio de aceptación de modelos.

## Aclaración sobre el feature engine v8 (corrección)

Una versión previa de este documento decía que "el feature engine v8 y el dataset son correctos" sin matizar. Eso inducía a error. La realidad:

- El **feature engine v8** es reutilizable como base conceptual (multi-timeframe, órdenes de magnitud de features, schema versionado), pero **no** está materializado como tabla `f_0..f_199` en los `normalized_events.parquet` actuales — esos parquet contienen **eventos normalizados crudos** (`stream_name`, `event_type`, `price`, `best_bid`, `best_ask`, etc.), no observaciones listas para ML.
- El **dataset histórico público** de Binance cubre bien `aggTrades`, `bookTicker` (top-of-book), `markPrice`, `fundingRate` y `metrics` (OI). **No cubre** `depthUpdate` en fidelidad completa, por lo que varias features de microestructura profunda del v8 live estarían con máscara o degradadas si se reprodujeran tal cual.
- Por eso el pipeline supervisado nuevo **no intenta reconstruir las 200 dims v8**. Arma su propio set de features desde OHLCV 5m derivado de `aggTrades` + order flow agregado + funding + OI. Esto es deliberado y se justifica por honestidad de cobertura histórica: sólo entrenamos con lo que el histórico público realmente provee.

Implicación práctica: la pipeline supervisada es **independiente del engine v8 actual** para el entrenamiento offline. En una fase posterior podríamos portar algunos features del v8 al pipeline supervisado, pero sólo aquellos que sean reconstruibles con fidelidad desde `aggTrades + bookTicker`.

## Camino del pivote (alto nivel)

1. Congelar PPO como baseline.
2. Montar pipeline supervisado con LightGBM sobre el mismo feature set v8.
3. Entrenar predictor de retorno forward con walk-forward honesto.
4. Medir en paper offline contra el baseline heurístico actual y contra PPO.
5. Si gana: agregar meta-labeling.
6. Integrar al `policy_server` como nueva policy pluggable.
7. Paper-live en paralelo con la heurística actual.
8. Sólo si paper-live es rentable: promover a LIVE.

El detalle concreto del plan está en el siguiente documento de bitácora.
