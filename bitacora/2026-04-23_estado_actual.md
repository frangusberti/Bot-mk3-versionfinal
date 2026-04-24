# Estado actual 2026-04-23 / 2026-04-24

## Estado general

- modo: `PAPER`
- estado: `RUNNING`
- pares activos: 6
- leverage actual: `1x`
- sizing objetivo base: `15%` del capital por trade
- run de referencia: `run_20260424_024921`

## Que ya quedo bien

- backend arriba;
- policy arriba;
- dashboard arriba;
- feed visible para los 6 pares;
- `health_state` visible en GUI;
- trades cerrados reconstruidos y visibles;
- win rate y riesgo total visibles;
- vumetro por par funcionando;
- `ADAUSDT` y `DOGEUSDT` con precision mas util;
- warmup visible por `5m`, `15m` y `1h`;
- switch para usar `Solo BTC` o `Todos`.

## Que problema grande se resolvio

Se resolvio el bloqueo por salud degradada.

Antes:

- el bot recibia mercado;
- evaluaba;
- pero el gate vetaba entradas por `HealthDegradedTimeout`.

Ahora:

- los simbolos quedan en `NORMAL`;
- el veto tecnico desaparecio;
- las decisiones vuelven a pasar por la capa normal de señal.

## Situacion actual del bot

Al momento de este registro:

- el bot puede operar;
- pero muchas veces decide `HOLD`;
- la razon mas comun es `no_signal`;
- el `expected_net_edge_bps` reciente suele seguir negativo.

Interpretacion:

- el sistema ya no esta roto;
- la selectividad actual viene mas por modelo/heuristica/costos que por infraestructura.

## Pendiente mas importante

Lo principal a seguir mejorando es la calidad de entrada:

- que el bot detecte setups con edge real;
- que no quede demasiado neutral;
- y que la parte de señal acompañe mejor al estilo de trades mas pacientes que se busco.

## Nota nueva

Desde 2026-04-24 el bot ya cuenta con una capa multi-timeframe real y la siguiente etapa pasa a ser reentrenar sobre ese schema nuevo.

Se dejo preparado el camino para:

- validar primero sobre `BTCUSDT`;
- usar el switch de GUI para no mezclar todos los pares antes de tiempo;
- y arrancar una nueva tanda de generaciones sobre la base actualizada.

## Archivos clave de esta etapa

- `scripts/paper_dashboard.py`
- `python/bot_policy/policies/heuristic.py`
- `python/bot_policy/config/policy_config.json`
- `crates/bot-data/src/features_v2/mod.rs`
- `crates/bot-data/src/features_v2/health.rs`
- `crates/bot-server/src/services/orchestrator/gate.rs`
