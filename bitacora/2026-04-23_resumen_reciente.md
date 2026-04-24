# Bitacora 2026-04-23

## Resumen

En esta tanda se trabajaron tres frentes principales:

1. estabilizar el paper trading live;
2. corregir por que el bot no estaba operando;
3. mejorar la GUI para que muestre mejor lo que esta pasando.

## Cambios hechos

### 1. Limpieza y visibilidad en la GUI

- se corrigio la visualizacion de precios para `ADAUSDT` y `DOGEUSDT` con 4 decimales;
- se agregaron todos los pares en la seccion de precios en vivo;
- se separo `Margen USDT`, `Notional USDT` y `Apal.` por posicion;
- se agrego `Riesgo total` en tiempo real;
- se agrego `Win Rate` en tiempo real;
- se agrego hora y fecha de cierre en trades cerrados;
- se reparo la reconstruccion de trades cerrados desde fills historicos;
- se limpio el historial de trades cerrados cuando se pidio arrancar de cero.

Archivo principal tocado:

- `scripts/paper_dashboard.py`

### 2. Ajustes de paper trading

- se reinicio el paper run para sacar posiciones heredadas chicas;
- se dejo el sizing objetivo en `15%` del capital por trade;
- se mantuvo `1x` para validar comportamiento real del bot antes de subir leverage;
- se dejaron los 6 pares activos:
  - `BTCUSDT`
  - `ETHUSDT`
  - `SOLUSDT`
  - `ADAUSDT`
  - `DOGEUSDT`
  - `XRPUSDT`

### 3. Cambio de estilo de trading

- la policy heuristica fue pasada a una logica mas paciente;
- se aumento el `cooldown`;
- se agrego `min_hold`;
- se paso a confirmacion mas lenta de salida;
- se subio el piso de profit y el stop de software para evitar cierres demasiado chicos por ruido.

Objetivo de este cambio:

- menos micro-scalping;
- menos trades nerviosos;
- dejar respirar mas el precio;
- acercar el bot a un estilo intradia mas tranquilo.

### 4. Arreglo del bloqueo que impedia operar

Se encontro que el bot no estaba simplemente esperando una oportunidad: estaba siendo bloqueado por el gate de salud.

Problema detectado:

- los simbolos quedaban en `health_state = DEGRADED`;
- eso disparaba `HealthDegradedTimeout`;
- el resultado era veto de entradas aunque la GUI pareciera sana.

Arreglos hechos:

- se actualizo la frescura del feed apenas entra cada evento en `FeatureEngineV2`;
- se ajusto la logica de salud para no degradar por trades inexistentes o no inicializados;
- se aflojo la compuerta para que `DEGRADED` no bloquee si la observacion usable sigue buena;
- se expuso `health_state` en la GUI para no ocultar este problema.

Archivos principales tocados:

- `crates/bot-data/src/features_v2/mod.rs`
- `crates/bot-data/src/features_v2/health.rs`
- `crates/bot-server/src/services/orchestrator/gate.rs`
- `scripts/paper_dashboard.py`

### 5. Vumetro por par

Se agrego un medidor visual por simbolo en la GUI:

- rojo a la izquierda: sesgo short;
- amarillo en el centro: neutral o todavia no entra;
- verde a la derecha: sesgo long.

El vumetro usa datos reales del bot:

- ultimo snapshot de policy por simbolo;
- ultimo candidate por simbolo;
- edge neto esperado;
- razon de la decision actual.

No es decorativo: si el bot esta neutral, queda cerca del centro; si esta acercandose a una entrada, se desplaza.

## Estado al cierre de esta tanda

- el bloqueo tecnico fuerte quedo resuelto;
- el bot ya no esta vetado por `HealthDegradedTimeout`;
- el sistema esta corriendo en `PAPER`;
- la GUI esta mostrando mas informacion real;
- ahora, si no entra, la causa principal es la señal/edge y no un freeze de infraestructura.
