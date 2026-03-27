# Contrato Temporal de Features (Temporal Feature Contract)

Este documento define la política estricta de "Freshness" y "Causality" para la capa de observación de BotMK3. Si un feature excede estos umbrales generará flags visibles y `None` / `Masking` para el modelo RL, previniendo el aprendizaje sobre datos fantasma/viejos. 

## 1. Definición de Bases Temporales
- **Event Time (`event_ts`)**: El timestamp del exchange indicando cuándo ocurrió físicamente el evento en el matching engine.
- **Receive Time (`recv_ts`)**: El timestamp local en el que el websocket del bot procesó y encoló el evento.
- **Decision Time (`decision_local_ts`)**: El instante exacto en que el bot invoca `get_state()` para calcular el vector de features y decidir una acción.

> **Regla de Oro (Golden Causal Rule):**
> Un snapshot SOLO puede contener datos donde `recv_ts <= decision_local_ts`. Nunca se debe usar información que no había llegado a la máquina al momento de la decisión, sin importar su `event_ts`.

## 2. Contratos por Familia de Features

### 2.1 Microestructura Inmediata (Orderbook - BBO, Imbalance, Depth)
- **Source Stream**: `depth@100ms`
- **Clock Basis**: `event_ts` para age, `recv_ts` para causación.
- **Expected Update Rate**: 100ms.
- **Max Acceptable Age (`max_age_ms`)**: `500 ms`.
- **Stale Policy**: `FLAG_STALE`. Si `age > 1000 ms`, aplicar `FLAG_MISSING` e inyectar *Masking* (0.0 bid/ask, 0.0 diff).
- **Fallback Policy**: Ninguna. El bot no debe operar o generar señales maker direccionales a ciegas. Orderbook es crítico.

### 2.2 Flujo de Ejecución (Trades, Tape, VPVR)
- **Source Stream**: `aggTrade`
- **Clock Basis**: `event_ts` para age, `recv_ts` para acumulación discreta.
- **Expected Update Rate**: Variable (bursty).
- **Max Acceptable Age (`max_age_ms`)**: `10,000 ms` (en mercados muertos es normal no tener trades).
- **Stale Policy**: No aplicar invalidación estricta por age, porque el flujo mercantil puede detenerse genuinamente. Sí emitir `WARN_QUIET_MARKET` si `age > 30,000 ms`.
- **Fallback Policy**: Mantener el decay normal de los buffers de volumen/buy_ratio (convergiendo a 0).

### 2.3 Régimen Lento (Klines / funding / vol de largo plazo)
- **Source Stream**: `kline_1m` / REST Funding
- **Clock Basis**: `event_ts` al cierre de vela.
- **Expected Update Rate**: 60,000 ms (Klines).
- **Max Acceptable Age (`max_age_ms`)**: `120,000 ms`.
- **Stale Policy**: Si la kline falta por más de 2 minutos, `FLAG_STALE`.
- **Fallback Policy**: Repetir la última vela conocida temporalmente con `stale_flag=True`.

### 2.4 Features Sintéticos del Entorno (Posición, Equity, Margen)
- **Source Stream**: Local Execution Engine / Async Account WS.
- **Clock Basis**: `decision_local_ts`.
- **Expected Update Rate**: Instantáneo local / <2000 ms remoto.
- **Max Acceptable Age (`max_age_ms`)**: `2000 ms` para remoto.
- **Stale Policy**: Si el sync de usuario falla y desvincula la equity, levantar `FATAL_DESYNC` y forzar `DISASTER_STOP`. Equidad asincrónica es causal de aborto.

## 3. Matriz de Banderas (Audit Flags)
Cada observación generada emitirá el diccionario:
```json
{
  "is_valid": true,
  "is_stale_ob": false,
  "is_stale_kline": false,
  "last_ob_age_ms": 110,
  "last_trade_age_ms": 2500,
  "masked_micro": false
}
```
Si `masked_micro == true`, todos los features de profundidad y LOB se normalizan al valor nulo del espacio vectorial para apagar el tensor local en la red PPO.
