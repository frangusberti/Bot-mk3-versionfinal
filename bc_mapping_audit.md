# Auditoría de Mapping: Dataset BC (v4)

Resumen de la validación del pipeline de generación de targets y alineación con el runtime.

## 1. Definición Exacta de Targets

El mapeo en [teacher_vnext_prep.py](file:///C:/Bot%20mk3/python/teacher_vnext_prep.py) utiliza el estado del entorno (`position`) para bifurcar la intención del teacher (`raw_action`):

| Intención Teacher | Estado (Pos) | Target Dataset | Acción gRPC (ID) |
| :--- | :--- | :--- | :--- |
| **POST_BID (1)** | 0 (FLAT) | **OPEN_LONG** | 1 |
| **POST_BID (1)** | 1 (LONG) | **ADD_LONG** | 2 |
| **POST_ASK (2)** | 0 (FLAT) | **OPEN_SHORT** | 5 |
| **POST_ASK (2)** | -1 (SHORT) | **ADD_SHORT** | 6 |
| **CLOSE (6)** | > 0 | **CLOSE_LONG** | 4 |
| **CLOSE (6)** | < 0 | **CLOSE_SHORT** | 8 |

## 2. Validación de Estado FLAT -> ADD

**Confirmado**: No existen casos de acciones `ADD` (2 o 6) registradas con la posición en 0.
El audit sobre 10,000 muestras del dataset `v4` arrojó:
- `ADD_LONG (2)` con Posición 0: **0**
- `ADD_SHORT (6)` con Posición 0: **0**

## 3. Alineación Action Enum / Lifecycle

El dataset está **100% alineado** con el [ActionType](file:///C:/Bot%20mk3/proto/bot.proto#569-581) definido en [bot.proto](file:///C:/Bot%20mk3/proto/bot.proto) (HOLD=0, OPEN_LONG=1, ADD_LONG=2, etc.). Los IDs grabados en el parquet corresponden exactamente a los índices que espera el `Discrete(10)` del agente RL.

## 4. Conteo por Clase y Diagnóstico

| Clase | Conteo (10k steps) | Hallazgo |
| :--- | :--- | :--- |
| **Action 0 (HOLD)** | 2,899 | Línea base de espera. |
| **Action 1 (OPEN_LONG)** | 1,705 | Entradas válidas. |
| **Action 5 (OPEN_SHORT)** | 1,009 | Entradas válidas. |
| **Action 2/6 (ADD)** | **0** | El experto actual no realiza promedios (Single-shot entry). |
| **Action 8 (CLOSE_SHORT)** | 4,378 | **Congestión**: El teacher emite CLOSE pero el Profit Floor lo bloquea en Rust, repitiendo la intención. |
| **Action 4 (CLOSE_LONG)** | 9 | Salidas exitosas por target/SL. |

### Ejemplos ADD_LONG (Action 2):
*   **Muestras encontradas**: 0. 
*   **Nota**: Dado que el dataset v4 se generó con un spread muy ajustado y un target de salida de 4 bps, el experto alcanza el estado de "intentar salir" muy rápido, y rara vez busca re-entrar en la misma dirección antes de cerrar.

---

**Conclusión**: El mapping es técnicamente correcto y está alineado. El dataset no está "sucio" con ADDs en FLAT, pero está "sesgado" por la falta de muestras de ADD y la alta repetición de intentos de salida bloqueados.
