# Plan de Reconstrucción: GUI V3 (Enfoque Operador)

Este documento detalla la estrategia para reemplazar el actual "Frankenstein Técnico" por una interfaz de grado profesional, priorizando la seguridad de la captura **Golden L2** en curso.

---

## 1. Entregable 1: Diagnóstico Estructurado del GUI Actual

| Componente | Función Original | Decisión Estratégica | Razón |
| :--- | :--- | :--- | :--- |
| **Recorder Tab** | Captura L2 | **Esconder en Laboratorio** | Proceso técnico. El operador solo debe ver un badge de "Data Healthy". |
| **Orchestrator** | Multi-función | **Desmantelar** | Mezcla riesgo, entrenamiento y operación. Es la principal fuente de confusión. |
| **Paper Tab** | Simulación | **Integrar en Operación** | No es un universo aparte, es un "Modo de Vuelo" del bot. |
| **Audit Tab** | Historial trades | **Conservar y Mejorar** | Es la base del Historial, pero necesita filtros más humanos. |
| **Wizard (Orch)** | Entrenamiento | **Mover a Laboratorio** | Operación != Aprendizaje. El operador usa el cerebro, no lo entrena. |
| **Risk Panel** | Configuración | **Separar: Básico vs Avanzado** | El 90% del tiempo solo se necesita ajustar el tamaño o parar todo. |

---

## 2. Entregable 2: Mapa Conceptual del Nuevo GUI

### A. Modo Operador (La superficie de vuelo)
1.  **Resumen (Dashboard)**: Estado del sistema (On/Off/Pausa), PnL diario, Equity actual, Conectividad.
2.  **Mercado (Contexto)**: Lectura humana de liquidez, volatilidad y presión (no números crudos).
3.  **Operaciones (Acción)**: Posiciones abiertas, órdenes pendientes, motivos de la última acción.
4.  **Historial (Memoria)**: Lista de trades cerrados, dinero ganado/perdido, duración.
5.  **Configuración (Ajustes)**: Solo parámetros críticos: Símbolo, Modo (Live/Paper), Risk Level.

### B. Modo Laboratorio / Avanzado (El taller)
1.  **Data Factory**: Gestión de la captura Golden L2, rotación de archivos, integridad.
2.  **Training Lab**: Curvas de aprendizaje, selección de versiones de "Cerebro" (Brain Registry).
3.  **Audit Deep-Dive**: Inspección milisegundo a milisegundo del libro de órdenes.
4.  **Expert Settings**: Thresholds, parámetros gRPC, configuraciones experimentales.

---

## 3. Entregable 3: Plan de Migración Segura (Aislamiento L2)

### Restricción de Seguridad Máxima
*   **Aislamiento de gRPC**: El nuevo GUI (Tauri) usará una conexión gRPC pasiva (solo lectura) para el Dashboard durante la captura L2.
*   **Prohibición de Comandos**: Las funciones de escritura/comando ([StartRecorder](file:///c:/Bot%20mk3/proto/bot.proto#11-12), [StopRecorder](file:///c:/Bot%20mk3/proto/bot.proto#12-13), [Reset](file:///c:/Bot%20mk3/proto/bot.proto#454-463)) estarán deshabilitadas o protegidas por doble confirmación en el nuevo shell hasta que termine la captura 48h.
*   **Ruta Paralela**: El desarrollo ocurrirá íntegramente en la carpeta [ui/](file:///c:/Bot%20mk3/python/bot_gui/tabs/audit.py#30-99), sin modificar `python/bot_gui/` ni `bot-server.exe`, garantizando que el sistema actual siga grabando sin interrupciones.

---

## 4. Entregable 4: Lista de Implementación (Sprints)

*   **Sprint 1: The Shell**: Crear navegación en Tauri con las 4 pantallas de Operador y el acceso al Laboratorio.
*   **Sprint 2: The Heartbeat**: Implementar la pantalla de "Resumen" con datos en tiempo real (solo lectura).
*   **Sprint 3: The Cargo**: Migrar la lógica de "Historial" (Audit) al nuevo shell con diseño moderno.
*   **Sprint 4: The Cockpit**: Implementar controles de operación (Start/Stop/Kill) con protecciones de seguridad.
*   **Sprint 5: The Laboratory**: Crear la zona avanzada y mover ahí las herramientas técnicas.

---

## 5. Entregable 5: Propuesta de Navegación y Componentes

*   **Sidebar Izquierda**: Iconos minimalistas (Dashboard, Mercado, Operaciones, Historial, Config, Lab).
*   **Barra Superior (Status Bar)**:
    *   `Badge [LIVE / PAPER / REPLAY]`
    *   `Heartbeat [RECORDING L2 - 476 EPS]` (Indicador activo de que todo va bien).
    *   `Equity Display`.
*   **Componentes de Lenguaje Humano**:
    *   En lugar de "Feature Value: -0.45", mostrar **"Presión: Bajista Moderada"**.
    *   En lugar de "InSync: True", mostrar **"Mercado: Integridad OK"**.

---

### Próximo Paso Sugerido
**Sprint 1: Inicializar el Shell en Tauri.** Esto creará la estructura visual base sin tocar ni una sola línea del código que está grabando los datos ahora mismo.
