# Manual de Uso GUI (Bot Mk3)

## 1) Objetivo
Esta guía explica, en lenguaje simple, cómo usar la GUI y qué hace cada bloque principal.

## 2) Flujo recomendado (rápido)
1. Abrir servidor (`bot-server`) y GUI.
2. Ir a **Orchestrator**.
3. Elegir **PAPER** para pruebas iniciales.
4. Configurar símbolos/policy/leverage.
5. Presionar **START ORCHESTRATOR**.
6. Verificar estado, fills y logs.
7. Ajustar parámetros desde paneles dedicados (Leverage, Risk).
8. Cuando PAPER esté estable, pasar a LIVE con checklist.

---

## 3) Pestaña Orchestrator (núcleo operativo)

### Botones principales
- **START ORCHESTRATOR**: inicia motor de operación.
- **STOP**: detiene operación.
- **KILL SWITCH (CLOSE ALL)**: botón de emergencia para cerrar exposición.
- **RESET PAPER**: limpia estado de paper.
- **IMPORT LAST SESSION**: intenta recuperar última sesión guardada.

### Modo
- **PAPER**: práctica con dinero ficticio.
- **LIVE**: trading real (requiere confirmar LIVE).

### Configuración global
- **Max Daily DD**: drawdown diario máximo (fracción 0..1).
  - PAPER: por defecto **1.0** (100%).
  - LIVE: sugerido **0.05** (5%).
- **Max Total Exp**: exposición total máxima.

### Configuración por símbolo
- **Symbol**: instrumento (ej. BTCUSDT).
- **Policy**: policy/modelo activo.
- **Leverage**: declarado (entero recomendado).

### Leverage Control
- **Mode**: MANUAL / AUTO / FIXED.
- **Manual Value / Fixed Value / Auto Min / Auto Max**: ahora se tratan como enteros.
- **Cooldown / Rate Limit**: velocidad de cambios en modo AUTO.
- **Apply to Exchange**: sólo para LIVE.

### Logs y tablas
- **Execution Log**: fills crudos.
- **Trade History**: operaciones cerradas.
- **Orchestrator Log**: eventos operativos.

---

## 4) Pestaña Risk Panel (riesgo duro)
- Ajusta límites de DD diario/mensual/total.
- Permite **reset** de estado de riesgo.
- Tiene botón de **Paper Mode (100% DD)** para facilitar pruebas.

> Recomendación: usar este panel como fuente principal de límites de riesgo duros.

---

## 5) Pestañas de Entrenamiento / RL
- Entrenamiento offline y evaluación de modelos.
- Si falla entrenamiento, revisar:
  1. que el dataset exista,
  2. que el policy server esté accesible,
  3. que rutas/model_path sean válidas.

---

## 6) Comunicación entre partes (resumen)
- GUI -> gRPC -> bot-server (orchestrator/risk/services).
- Orchestrator -> policy server HTTP (infer/reload/profile).
- Orchestrator -> exchange adapter (paper/live execution).

Si una parte se cae, el sistema intenta fallback seguro (por ejemplo HOLD en inferencia no disponible).

---

## 7) Buenas prácticas operativas
1. Primero PAPER (no LIVE directo).
2. Confirmar que no haya desync prolongado de orderbook.
3. Verificar logs de riesgo/rechazos.
4. Antes de LIVE, pasar checklist de `docs/GO_LIVE_CHECKLIST.md`.
