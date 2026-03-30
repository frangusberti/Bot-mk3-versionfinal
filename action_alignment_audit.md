# Auditoría de Alineación: Action Enum (10-Action)

Verificación cruzada de índices de acción en todas las capas del sistema.

## 1. Orden de Acciones Sincronizado

Se confirma que las siguientes capas utilizan el orden definido en [bot.proto](file:///C:/Bot%20mk3/proto/bot.proto):

| ID | Enum Variant | Dataset Target | Runtime (Rust) | Telemetry Key |
| :--- | :--- | :--- | :--- | :--- |
| **0** | `HOLD` | HOLD | `ActionType::Hold` | "HOLD" |
| **1** | `OPEN_LONG` | OPEN_LONG (if Flat) | `ActionType::OpenLong` | "OPEN_LONG" |
| **2** | `ADD_LONG` | ADD_LONG (if Pos) | `ActionType::AddLong` | "ADD_LONG" |
| **3** | `REDUCE_LONG`| - | `ActionType::ReduceLong` | "REDUCE_LONG" |
| **4** | `CLOSE_LONG` | CLOSE_LONG | `ActionType::CloseLong` | "CLOSE_LONG" |
| **5** | `OPEN_SHORT` | OPEN_SHORT (if Flat) | `ActionType::OpenShort` | "OPEN_SHORT" |
| **6** | `ADD_SHORT` | ADD_SHORT (if Pos) | `ActionType::AddShort` | "ADD_SHORT" |
| **7** | `REDUCE_SHORT`| - | `ActionType::ReduceShort` | "REDUCE_SHORT" |
| **8** | `CLOSE_SHORT` | CLOSE_SHORT | `ActionType::CloseShort` | "CLOSE_SHORT" |
| **9** | `REPRICE` | - | `ActionType::Reprice` | "REPRICE" |

## 2. Verificación de ADD_LONG (ID: 2)

El flujo de esta acción es inequívoco:
1.  **Dataset**: [teacher_vnext_prep.py](file:///C:/Bot%20mk3/python/teacher_vnext_prep.py) asigna `action = 2` cuando el teacher quiere comprar y ya existe una posición.
2.  **PPO**: La red neuronal emite un entero `2` desde su cabeza `Discrete(10)`.
3.  **gRPC**: [grpc_env.py](file:///C:/Bot%20mk3/python/bot_ml/grpc_env.py) envía [Action(type=2)](file:///C:/Bot%20mk3/proto/bot.proto#196-202).
4.  **Runtime**: [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs) recibe el enum y ejecuta el bloque de código `ActionType::AddLong`.

## 3. Hallazgos de Desalineación

**Ninguna desalineación detectada.**

*   **Proto**: Coincide con el contrato gRPC.
*   **Python**: [grpc_env.py](file:///C:/Bot%20mk3/python/bot_ml/grpc_env.py) pasa el entero crudo de PPO al mensaje de Protobuf, preservando el ID.
*   **Rust**: El compilador garantiza que los nombres de los variantes coincidan con los IDs numéricos del proto.
*   **Dataset**: La lógica de "Re-mapping" en el generador de targets (Phase 3.5) respeta rigurosamente esta secuencia.

---

**Conclusión**: El sistema es íntegro. El índice `2` siempre significa `ADD_LONG` en todas las etapas, desde el target del experto hasta la ejecución en el core de Rust.
