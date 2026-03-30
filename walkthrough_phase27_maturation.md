# Walkthrough: Phase 27 Scaling & Maturation

Auditoría técnica del escalado controlado de la política BOTMK3 (10k -> 50k steps).

## 1. Scorecard de Maduración (50k steps)

| Métrica | 10k steps | 25k steps | 50k steps | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Invalid Action Rate** | 0.0% | 0.0% | **0.0%** | **CLEAN** |
| **HOLD (Legal)** | 96.0% | 94.1% | **80.9%** | **EXPLORING** |
| **OPEN Attempt (Legal)**| 3.5% | 5.7% | **18.4%** | **ACTIVATING** |
| **Blocks (Offset Gate)** | 12 | 128 | **308** | **CONGESTED** |
| **Total Trades** | 0 | 0 | 0 | - |

## 2. Hallazgos del Escalado

1.  **Integridad Sostenida**: El atractor de `ADD_LONG` o acciones inválidas no ha reaparecido. La penalidad de -0.1 es suficiente para mantener a raya la deriva estocástica.
2.  **Activación de Política**: La intención de apertura (`OPEN`) ha crecido de un residual 3% a un significativo **18.4%**. El agente está "despertando" y buscando activamente entrar al mercado.
3.  **Cuello de Botella (Gates)**: El 100% de los intentos terminan en bloqueos de `offset` (308 eventos). Esto confirma que la lógica de decisión es legal pero los parámetros del backend (0.2 bps) o el `microprice` del experto son demasiado conservadores para este dataset.

## 4. Reorientación Thesis-Driven (Microestructura)

Se ha implementado una reorientación del sistema de recompensa para priorizar la economía real y microestructura sobre reglas rígidas.

### Cambios en el Backend (Rust)
- **Thesis Decay**: Nueva penalidad en [reward.rs](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs) basada en el drift del `microprice` contra la posición, escalada por el flujo tóxico (`imbalance`).
- **Relajación de Gates**: Se ha reducido el `profit_floor_bps` de 5.0 a **0.5 bps** por defecto, permitiendo al agente aprender a salir cuando la tesis se deteriora, no solo cuando alcanza un profit arbitrario.
- **Configurabilidad**: Nuevo campo `reward_thesis_decay_weight` en el proto [RLConfig](file:///C:/Bot%20mk3/proto/bot.proto#501-564) para ajuste dinámico.

### Scorecard de Validación (Python)
Se ha actualizado [ppo_eval_checkpoint.py](file:///C:/Bot%20mk3/python/ppo_eval_checkpoint.py) para incluir la métrica de **Thesis Decay Total**, permitiendo auditar cuánto "castigo por tesis fallida" está acumulando el agente.

## 5. Próximos Pasos de Entrenamiento

1. **Lanzar Entrenamiento**: Con `reward_thesis_decay_weight = 0.0001` y `profit_floor_bps = 0.5`.
2. **Audit de 50k pasos**: Monitorear `thesis_decay_total` vs `net_pnl`. Un agente exitoso debería reducir el decay a medida que aprende a cerrar posiciones ante señales adversas.

## 6. Refugia Cleanup (Cierre de Grietas Semánticas)

Se ha procedido a eliminar los "refugios" legales de no-op para forzar al agente a tomar decisiones con sentido económico:

- **REPRICE Condicional**: Ahora la acción `REPRICE` es marcada como **INVALID** si el agente está en `FLAT` y no tiene órdenes activas que cancelar.
- **Veto como Invalidez**: Si un intento de `OPEN_*` o `ADD_*` es bloqueado por el `entry_veto` (microprecio desfavorable), ahora se devuelve como **INVALID**. Esto penaliza la "mala intención" de entrar contra la tesis del experto.
- **Validación de CLOSE_SHORT**: Se confirma que spamear `CLOSE_SHORT` en `FLAT` ya era ilegal y penalizado con -0.1, por lo que el agente ya está recibiendo el desincentivo correcto.
dicando que el agente todavía tiene un amplio margen de exploración para encontrar el spread exacto de ejecución.

## 3. Diagnóstico Quirúrgico de Ejecución

Tras detectar 0 trades en el audit A/B pese al incremento de intentos legales, se realizó un rastreo forense ([debug_execution.py](file:///C:/Bot%20mk3/python/debug_execution.py)) que arroja los siguientes resultados:

| Prueba | Resultado | Conclusión |
| :--- | :--- | :--- |
| **Vitalidad de Motor (Forced Action 5)** | **SUCCESS (1 Fill @ Step 0)** | El Engine y el bridge gRPC están operativos. |
| **Bypass de Gate (0.1 bps)** | **Offset Blocks = 0** | El umbral de 0.1 bps es físicamente permisivo. |
| **Comportamiento PPO (Model 50k)** | **Action 8 (CLOSE_SHORT) @ FLAT** | La política está estancada en un "no-op" legal. |

### El "Frenado" de la Política
El agente ha convergido a una estrategia de "superviviencia conductual": para evitar la penalidad por acciones inválidas, spamea la Acción 8 (`CLOSE_SHORT`) mientras está FLAT. Como esta acción no es ilegal (simplemente no hace nada), el agente se mantiene en un bucle seguro pero inoperante.

## 4. Conclusión Final

La parálisis de ejecución **no es un bug de infraestructura ni de gates**, sino un artefacto conductual de la fase de restauración. El agente aprendió a ser "legal" antes que "rentable".

---

**Recomendación**: No relajar más los gates. El sistema necesita un **Incentive Boost** (Incentivo positivo por `OPEN_*` o reducción de penalidad por `HOLD`) para romper el atractor de inactividad de la Acción 8 y forzar la exploración del ciclo de vida del trade.
