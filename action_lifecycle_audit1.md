# Causal Audit: Action Lifecycle (BOTMK3)

Auditoría técnica condensada sobre la semántica de acciones y estados posicionales.

## 1. Matriz de Estado x Acción

| Acción \ Estado | FLAT | LONG | SHORT |
| :--- | :--- | :--- | :--- |
| **OPEN_LONG** | Legal | **Ilegal** (Invalid) | **Ilegal** (Invalid) |
| **ADD_LONG** | **Ilegal** (Invalid) | Legal | **Ilegal** (Invalid) |
| **REDUCE_LONG** | **Ilegal** (Invalid) | Legal (Exit Gate) | **Ilegal** (Invalid) |
| **CLOSE_LONG** | **Ilegal** (Invalid) | Legal (Exit Gate) | **Ilegal** (Invalid) |
| **OPEN_SHORT** | Legal | **Ilegal** (Invalid) | **Ilegal** (Invalid) |
| **ADD_SHORT** | **Ilegal** (Invalid) | **Ilegal** (Invalid) | Legal |
| **REDUCE_SHORT**| **Ilegal** (Invalid) | **Ilegal** (Invalid) | Legal (Exit Gate) |
| **CLOSE_SHORT** | **Ilegal** (Invalid) | **Ilegal** (Invalid) | Legal (Exit Gate) |
| **REPRICE** | Legal (Cancel-all) | Legal | Legal |
| **HOLD** | Legal (No-op) | Legal | Legal |

*   **Legal**: La acción es coherente con el estado y se intenta ejecutar.
*   **Ilegal (Invalid)**: Retorna `is_invalid=true`, bloquea ejecución y genera penalidad RL.

---

## 2. Caso Específico: ADD_LONG estando FLAT

*   **Ruta**: [crates/bot-server/src/services/rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs) -> `ActionType::AddLong` branch (Line 439).
*   **Decisión**: Verifica `!has_pos` y retorna [(0, true)](file:///C:/Bot%20mk3/python/teacher_policy.py#199-205).
*   **Orden/Posición**: **No se crea nada**. El sistema ignora la intención de orden.
*   **Remapeo**: **No se remapea**. No intenta convertirlo en `OPEN`.
*   **Penalidad**: Sí. [reward.rs](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs) recibe `is_invalid_action=true` y aplica `invalid_action_penalty` (actualmente -0.1).
*   **Paso**: Se consume el paso del agente (se envía [Obs](file:///C:/Bot%20mk3/proto/bot.proto#564-568) -> recibe [Action](file:///C:/Bot%20mk3/proto/bot.proto#196-202) -> devuelve [Reward](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#10-16)).
*   **Telemetry**: Incrementa `action_counts["ADD_LONG"]` (Line 440). No hay log de error explícito para evitar spam, pero el flag viaja al buffer RL.

---

## 3. No-Op Silenciosos (Soft Blocks)

Existen casos donde la acción es **Legal**, pero el sistema la veta silenciosamente sin penalidad `is_invalid`:

1.  **Selective Entry Veto**: Bloquea `OPEN`/`ADD` si el `microprice` indica adversidad inminente (`rl.rs:431`).
2.  **Min Offset Veto**: Bloquea órdenes pasivas si el spread es tan bajo que la orden quedaría en el mid o cruzaría el spread (`rl.rs:540`).
3.  **Imbalance Block**: Bloquea órdenes pasivas si el flujo del libro es agresivamente contrario (`rl.rs:530`).
4.  **Exit Profit Floor**: Bloquea `REDUCE`/`CLOSE` si la posición no tiene Profit >= `profit_floor_bps` ni es una emergencia de Stop Loss (`rl.rs:564`).

---

## 4. Referencias de Semántica

*   **Estado y Despacho**: [rl.rs:385](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L385) ([apply_action](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs#384-513))
*   **Cálculo de Penalidad**: [reward.rs:202](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L202) ([compute_reward](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#110-225))
*   **Configuración**: `RewardConfig.invalid_action_penalty`
