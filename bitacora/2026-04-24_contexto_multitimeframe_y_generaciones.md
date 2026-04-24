# Bitacora 2026-04-24

## Resumen de esta etapa

En esta tanda se hizo el salto importante del bot desde un enfoque demasiado corto a uno con contexto mayor.

Se implementaron tres capas nuevas:

1. contexto multi-timeframe real para `5m`, `15m` y `1h`;
2. lectura de regimen superior para saber si el mercado esta en tendencia, rango, shock o muerto;
3. decision jerarquica, donde el contexto mayor manda y la microestructura solo ajusta el timing.

Ademas se dejo la GUI mas explicita para no operar a ciegas y se preparo una nueva tanda de entrenamiento sobre BTC.

## Cambios tecnicos principales

### 1. Feature engine v8

Se agregaron features nuevas de contexto:

- `ret_5m`, `ret_15m`, `ret_1h`
- `rv_5m`, `rv_15m`, `rv_1h`
- pendiente de precio en horizontes mayores
- posicion relativa dentro del rango en `5m`, `15m` y `1h`
- sesgo y alineacion entre timeframes

Esto quedo integrado en el schema `v8` con `OBS_DIM = 200`.

Archivos principales:

- `crates/bot-data/src/features_v2/compute_price.rs`
- `crates/bot-data/src/features_v2/compute_regime.rs`
- `crates/bot-data/src/features_v2/mod.rs`
- `crates/bot-data/src/features_v2/schema.rs`
- `crates/bot-data/src/features_v2/health.rs`

### 2. Policy mas contextual

La policy ya no toma entradas solo por micro-momentum.

Ahora exige:

- que el contexto mayor no este roto o inutilizable;
- que la direccion superior apoye la idea del trade;
- que el breakout o el timing corto confirmen la entrada.

Tambien se ajusto el `regime_router` para que no etiquete falsamente `HIGH_VOL` cuando el contexto esta empatado.

Archivos principales:

- `python/bot_policy/policies/heuristic.py`
- `python/bot_policy/regime_router.py`
- `python/bot_policy/policy_server.py`
- `python/bot_policy/config/policy_config.json`

### 3. Dashboard y control operativo

En la GUI quedaron visibles dos cosas importantes:

- warmup por horizonte (`5m`, `15m`, `1h`);
- switch de universo para elegir `Solo BTC` o `Todos`.

El switch reinicia limpio el paper run y deja trazado claro si se esta validando la logica solo sobre BTC o sobre varios pares.

Archivo principal:

- `scripts/paper_dashboard.py`

## Estado operativo al cerrar esta tanda

- run activo de referencia: `run_20260424_024921`
- modo: `PAPER`
- estado: `RUNNING`
- simbolos: `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `ADAUSDT`, `DOGEUSDT`, `XRPUSDT`
- `health_state`: `NORMAL`
- switch de universo: disponible
- warmup visible: disponible

Lo importante es esto:

- si el bot no entra, ya no es por un bloqueo tecnico fuerte;
- ahora la razon principal pasa por `no_signal` o edge insuficiente;
- eso vuelve mucho mas honesta la etapa siguiente de entrenamiento.

## Entrenamiento nuevo

Se dejo preparado un runner de generaciones nuevas sobre BTC, pensado para:

- usar la base multi-timeframe nueva;
- bootstrapear desde el mejor modelo util disponible;
- registrar una auditoria por generacion;
- seguir alimentando el mejor modelo aceptado como base de la siguiente iteracion.

Durante la prueba de humo salio algo importante y sano:

- los modelos viejos locales estaban en `obs_dim = 148`;
- la stack nueva trabaja en `obs_dim = 200`;
- por eso el runner descarta bootstrap incompatible y, si hace falta, arranca limpio.

Decision operativa tomada:

- primero entrenar y validar sobre `BTCUSDT`;
- mantener el switch de GUI para usar solo BTC o todos los pares;
- dejar para una etapa posterior el entrenamiento especifico por par.

## Criterio actual

La idea ahora no es perseguir trades hiper rapidos.

La estrategia de evolucion apunta a:

- menos sensibilidad al ruido de segundos;
- mas lectura de fase de mercado;
- entradas que respeten `5m`, `15m` y `1h`;
- uso prudente de BTC como base comun hasta que haya datasets y entrenamiento por par.

## Lanzamiento real de la nueva tanda

Se lanzo una tanda nueva de `500` generaciones sobre `BTCUSDT` con estos criterios:

- `steps` por generacion: `25000`
- prioridad: baja
- threads por generacion: `4`
- leverage de entrenamiento: `3.0`
- `max_pos_frac`: `0.15`
- source de datasets: escaneo de filesystem
- index efectivo usado por el runner: `index/__scan_fallback__.json`

Run root del entrenamiento:

- `python/runs_train/gen_v8_btc_500_20260424_0012`

Archivos importantes de seguimiento:

- `python/runs_train/gen_v8_btc_500_20260424_0012/generation_audit.md`
- `python/runs_train/gen_v8_btc_500_20260424_0012/generation_audit.jsonl`
- `python/runs_train/gen_v8_btc_500_20260424_0012/logs/`

Nota util:

- el runner detecto que el index viejo no servia para episodios utiles y cayo correctamente al escaneo de datasets reales;
- tambien detecto y descarto bootstrap viejo incompatible (`148` vs `200`), por lo que la tanda arranca limpia sobre el schema actual.
