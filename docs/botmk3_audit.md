# Audit Checklist - BOTMK3 Architecture

## 1️⃣ WebSocket Gateway
**Rol**: Conectarse al exchange (Binance), suscribirse a streams, manejar reconexión y heartbeats.
- [x] ¿Existe un módulo/struct dedicado para la conexión WebSocket? (`crates/bot-data/src/binance/client.rs`)
- [x] ¿Soporta reconexión automática si se cae el WS? (Loop with sleep on error)
- [~] ¿Hay manejo de ping/pong o heartbeat? (Implicit via `tungstenite`, no explicit handler in client wrapper)
- [x] ¿La configuración de símbolos está en config? (Passed from `recorder` config)
- [x] ¿Logs claros cuando se conecta / desconecta / reintenta? (Yes)

## 2️⃣ Event Normalizer
**Rol**: Tomar los JSON crudos y transformarlos en structs internos.
- [x] ¿Hay structs bien definidos? (`crates/bot-data/src/binance/model.rs`)
- [x] ¿Toda la conversión JSON → struct está encapsulada? (`serde_json` usage)
- [x] ¿Se manejan errores de parseo? (Yes, logs warning)
- [x] ¿Timestamps se normalizan? (Assigned `local_ts` in `engine.rs`)
- [ ] ¿Hay tests unitarios que alimenten JSON de ejemplo? (Codebase has tests, need specific coverage check)

## 3️⃣ Market State Builder
**Rol**: Mantener el estado del mercado en memoria (orderbook).
- [x] ¿Existe un componente que mantenga el orderbook actual? (`crates/bot-data/src/orderbook/engine.rs`)
- [x] ¿Se aplican correctamente lastUpdateId, U, u? (Gap detection and overlap logic present)
- [~] ¿Hay lógica para re-sincronizar el orderbook? (Detects gap, sets status, but auto-resync logic in `engine` not explicitly seen)
- [~] ¿Se guarda algún buffer de últimos trades? (Stored in `FeatureEngine` history, not `OrderBook`)
- [x] ¿Este módulo no depende de RL ni de ejecución? (Clean separation)

## 4️⃣ Recorder Engine
**Rol**: Guardar eventos en disco (Parquet).
- [x] ¿Se escriben archivos en runs/<run_id>/events/? (Yes)
- [x] ¿Se usa Parquet con compresión? (Yes)
- [x] ¿Se particiona por symbol y/o date? (Yes)
- [x] ¿El stripping del payload JSON está implementado? (Yes)
- [x] ¿Configuración de payload está en config? (Yes)
- [x] ¿Hay mecanismo de flush explícito? (Yes)

## 5️⃣ Retention Manager
**Rol**: Limpiar datos viejos.
- [x] ¿Existe algún script/servicio que borre archivos viejos? (`ControlService`)
- [x] ¿Hay configuración de ventana caliente? (`RetentionConfig`)
- [x] ¿El cleanup se asegura de no borrar archivos dentro de la ventana? (Yes)
- [x] ¿Hay logs que indican qué se borró? (Structured JSON logs)

## 6️⃣ Feature Engine
**Rol**: Convertir estado de mercado → features.
- [x] ¿Hay un módulo claramente identificado? (`FeatureEngine`)
- [x] ¿Tiene una función tipo build_features? (`compute_vector`)
- [x] ¿Existe un método tipo is_ready()? (Yes, `is_ready`)
- [x] ¿Las features están documentadas? (Signature hash, standard vector def)
- [ ] ¿Tests que verifiquen features? (Need verification)

## 7️⃣ Regime Detector
**Rol**: Identificar régimen de mercado.
- [ ] ¿Existe algún código que clasifique el régimen de mercado? (No)
- [ ] ¿Ese régimen se usa como feature? (No)
- [ ] ¿Hay thresholds claros? (No)
- [ ] ¿Se loguea el régimen actual? (No)

## 8️⃣ Policy Adapter (Rust ↔ Python bridge)
**Rol**: Conectar Rust con modelo Python.
- [x] ¿Hay un adapter específico? (`PythonPolicyAdapter`)
- [x] ¿La ruta del modelo se toma de config? (Yes)
- [x] ¿Existe lógica para recargar el modelo? (Yes, `ReloadPolicy`)
- [~] ¿Hay una manera de hacer rollback? (API supports it, manual implementation)
- [x] ¿Se maneja timeout / errores de gRPC? (Yes)

## 9️⃣ RL Core (Inference en Python)
**Rol**: Modelo PPO.
- [x] ¿Hay una clase tipo PpoPolicy? (SB3 Usage)
- [x] ¿Se carga el modelo desde un archivo? (Yes)
- [x] ¿El código de inferencia está separado? (`policy_server.py`)
- [~] ¿Se registra log-prob / value estimado? (Not explicitly seen in `policy_server` response struct)
- [x] ¿Se controla el “modo eval”? (Yes)

## 🔟 Experience Builder
**Rol**: Construir tuplas para entrenamiento.
- [~] ¿Hay un módulo dedicado? (Offline: Yes. Live: `SymbolAgent` constructs rows, sends to `ExperienceWriter`)
- [x] ¿Se está registrando correctamente funding/fees? (`ExperienceRow` has fields, agent populates them)
- [x] ¿La experiencia se guarda en runs/? (Writer handles this)
- [x] ¿Se llama explícitamente a flush? (On stop)

## 1️⃣1️⃣ Execution Engine
**Rol**: Mandar órdenes reales.
- [x] ¿Hay un módulo claramente diferenciado? (`ExecutionInterface` trait, distinct from `Policy`)
- [x] ¿Separás “querer abrir posición X” de “cómo se manda la orden real”? (`RiskManager` intervenes between intent and execution)
- [x] ¿Se trackean posición actual, PnL? (`ExecutionInterface` provides `get_position`, `get_equity`)
- [x] ¿Está implementado el tracking de funding? (`PositionInfo` has `realized_funding`)
- [ ] ¿Hay retry si un request al exchange falla? (Depends on `BinanceClient` impl, likely simple error propagation)

## 1️⃣2️⃣ Risk Manager
**Rol**: Capa de seguridad.
- [x] ¿Existe un módulo que aplique reglas de riesgo DURAS? (`RiskManager` in `orchestrator/risk.rs`)
- [x] ¿Hay límites para tamaño max posición, max leverage? (Exposure limits, Drawdown limits)
- [x] ¿Puede activar un “kill switch”? (Yes, `check_kill_switch`)
- [x] ¿Las condiciones de riesgo están en config? (Yes, `OrchestratorConfig`)
- [x] ¿Loguea claramente cuando bloquea? (Yes, `RiskDecision::Blocked` returns reason)

## 1️⃣3️⃣ Offline Trainer
**Rol**: Tomar experiencia grabada y entrenar.
- [x] ¿Existe un script offline_train.py? (Yes)
- [x] ¿Carga Parquets de experience/? (Yes)
- [x] ¿Hace training con PPO? (Yes)
- [x] ¿Guarda el modelo en un path claro? (Yes)
- [x] ¿Tiene parámetros para LR, KL? (Yes)
- [x] ¿Genera algún tipo de reporte? (Yes)

## 1️⃣4️⃣ Model Governance
**Rol**: Registrar modelos.
- [x] ¿Hay algún sistema que registre metadatos? (Yes)
- [x] ¿Se comparan métricas de modelo viejo vs nuevo? (Yes)
- [x] ¿Hay umbrales de aceptación/rechazo? (Yes)
- [x] ¿Se guarda un parent_model_id? (Yes)
- [x] ¿Se loguea explícitamente? (Yes)
