# Informe de Auditoría y Rediseño del GUI (Bot Mk3)

## 1. Análisis del GUI Actual (Estado: "Frankenstein Funcional")

El GUI actual, construido en **Python (PySide6)**, ha servido como herramienta de prototipado rápido, pero sufre de una acumulación de lógica de diferentes épocas del proyecto.

### Hallazgos por Componente:
*   **Recorder Tab ([recorder.py](file:///c:/Bot%20mk3/python/bot_gui/tabs/recorder.py))**:
    *   **Puntos Fuertes**: Conexión gRPC sólida.
    *   **Puntos Débiles**: Interfaz desactualizada. Los labels de compresión dicen "SNAPPY" (ahora usamos ZSTD). No muestra el progreso de rotación de archivos ni la salud del [OrderBook](file:///c:/Bot%20mk3/crates/bot-data/src/orderbook/engine.rs#38-63) en tiempo real (vital para L2).
*   **Orchestrator Tab ([orchestrator.py](file:///c:/Bot%20mk3/python/bot_gui/tabs/orchestrator.py))**:
    *   **El Problema Principal**: Es una mezcla confusa de Operación (Phase 1) y Entrenamiento (Phase 2).
    *   **Basura Técnica**: Los botones de "Auto-Pilot" y "Wizard" usan rutas de archivos y lógicas de entrenamiento viejas que ya no coinciden con nuestro nuevo pipeline de alta resolución. El panel de riesgo es demasiado "ruidoso".
*   **Paper/Replay Tab ([paper.py](file:///c:/Bot%20mk3/python/bot_gui/test_paper.py))**:
    *   **Estado**: Útil pero peligroso. El motor de simulación interno que utiliza el GUI es menos preciso que el nuevo [ExecutionEngine](file:///c:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs#6-31) de Rust. Esto crea una falsa sensación de seguridad al usuario.
*   **Audit Tab ([audit.py](file:///tmp/forensic_audit.py))**:
    *   **Estado**: Es el componente más rescatable. Su lógica de consulta a [AnalyticsService](file:///c:/Bot%20mk3/proto/bot.proto#912-919) es robusta.
*   **Tauri Skeleton ([ui/](file:///c:/Bot%20mk3/python/bot_gui/tabs/audit.py#30-99) folder)**:
    *   Existe un inicio de migración a Tauri (React/TypeScript) que ha quedado como un cascarón vacío.

---

## 2. Propuesta: El Nuevo "Mission Control V3"

El objetivo es reemplazar completamente el GUI actual por una interfaz **limpia, basada en flujos de trabajo y transparente**.

### A) Nueva Arquitectura Técnica
*   **Frontend**: React + TailwindCSS + Lucide Icons (dentro del proyecto Tauri existente).
*   **Backend**: Tauri (Rust). El GUI hablará directamente con los servicios gRPC de `bot-server`.
*   **Ventaja**: El GUI será extremadamente ligero (sin necesidad de un entorno Python pesado) y visualmente "Premium".

### B) Replanteo de Pestañas (Workflow-Driven)

#### 1. Dashboard: "The Heartbeat" (Monitoreo Real)
*   **Vista**: Un gráfico de velas de alta frecuencia con overlays de nuestras órdenes Limit.
*   **Metrics Central**: EPS (Eventos Por Segundo), InSync % (Salud del libro), Latencia de Red, y PnL de la sesión actual.
*   **Kill Switch**: Un botón gigante y accesible siempre presente.

#### 2. The Golden Factory (El Pipeline L2)
*   Esta pestaña reemplaza al "Recorder" y al "Wizard" del Orchestrator. Unifica el flujo:
    *   **Captura**: Selección de símbolo, duración y botón de inicio con timer real.
    *   **Validación**: Un checklist automático que se marca en verde conforme el dataset pasa las pruebas de (1) Integridad y (2) Paridad.
    *   **Promoción**: Botón para marcar un dataset como "GOLDEN" y habilitarlo para entrenamiento.

#### 3. Forensic Lab (Antiguo Audit)
*   Búsqueda profunda de trades.
*   **Novedad**: Un visualizador de "Libro de Órdenes Histórico". Poder elegir un trade pasado y ver exactamente cómo estaba el libro de órdenes en ese milisegundo.

#### 4. Brain Hub (Entrenamiento)
*   Visualización de curvas de aprendizaje (Reward, Loss, Explained Variance).
*   Gestión de versiones del cerebro (`.pt`, `.onnx`).
*   Comparador: "¿Cómo se comporta la versión A vs la versión B frente al mismo dataset Golden?".

---

## 3. Estética y User Experience (UX)

*   **Dark Mode**: Inspirado en terminales Bloomberg y TradingView (Slate 950 / Blue 500).
*   **Fidelidad Visual**: Uso de micro-animaciones para indicar la entrada de datos en tiempo real (heartbeat).
*   **Simplicidad**: Menos inputs manuales, más "Presets" (ej. Perfil de Riesgo Conservador / Agresivo / YOLO).

---

## 4. Plan de Acción Recomendado

1.  **POST-CAPTURA**: Terminar las 48h actuales y validar los datos.
2.  **LIMPIEZA**: Eliminar la carpeta [ui/](file:///c:/Bot%20mk3/python/bot_gui/tabs/audit.py#30-99) actual (esqueleto) y `python/bot_gui` para consolidar.
3.  **CONSTRUCCIÓN**: Implementar primero el **"Dashboard de Salud"** en Tauri para tener visibilidad total del bot de Rust.
4.  **REEMPLAZO**: Ir migrando las funciones de Auditoría y Entrenamiento una por una hasta que el GUI de Python ya no sea necesario.

**Nota Social**: Este cambio sacará al bot de la etapa de "Experimento Estudiantil" y lo elevará a una "Herramienta de Grado Profesional".
