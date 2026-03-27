# Protocolo de Fine-Tuning RL: Transición Post-BC

## 1. Objetivo y Pregunta Central
El objetivo de esta fase es transicionar de una política pre-entrenada por Behavior Cloning (BC) a una política optimizada por RL (PPO) de forma controlada.

**Pregunta Cuantitativa Central:**
*¿PPO refina el prior BC (mejora PnL y ratio de fills manteniendo disciplina) o lo destruye (colapso entrópico, degeneración de acciones, spam de red)?*

## 2. Hipótesis Operativa
El pre-entrenamiento BC provee un estado base donde el bot sobrevive primariamente a través de alta selectividad (`HOLD` determinístico ~100%, estocástico ~58%). El RL fine-tuning debe aprender a romper el bloqueo determinístico para tomar entradas (POST_BID/POST_ASK) cuando el value network identifique edge asimétrico, sin caer en hiperactividad (churn).

## 3. Topología de Checkpoints
El run no será continuo hasta el final. Se pausará obligatoriamente en:
- **50k steps**: Verificación de supervivencia temprana. PPO ya alteró la distribución original.
- **100k steps**: Verificación de asimilación del value function.
- **250k steps**: Verificación de convergencia a corto plazo y estabilidad de la política.

## 4. Scorecard y Reglas de Decisión

### 4.1. PASS (Continuar)
Para obtener PASS, un checkpoint RL debe superar simultáneamente:
1. **Ruptura del bloqueo determinístico**: `HOLD_det < 98%` (el bot intenta cosas).
2. **Preservación del perfil Maker**: `Maker_Fills > 0` y `(POST_BID + POST_ASK + JOIN_BID + JOIN_ASK) > 5%`.
3. **Microestructura Sana**: `Toxic_Fills / (Maker_Fills + 1e-5) < 0.5`.
4. **Viabilidad Económica**: `PnL >= BC_PnL` o Drawdown controlado (`< 2%`).

### 4.2. WARN (Ajustar o Observar)
- Fills y PnL han mejorado, pero la tasa de Cancel (`CANCEL_ALL`) supera el 10%.
- El bot obtiene Peor PnL que BC pero la política no degeneró (exploración en curso).
*Acción sugerida: Continuar al siguiente checkpoint, pero emitir flag.*

### 4.3. FAIL (Abortar Inmediatamente)
Si se activa alguna patología:
- **Colapso Conservador**: `HOLD_det >= 99%` Y `HOLD_stoch >= 90%` Y `Entropy < 0.05`.
- **Hiperactividad Inútil**: `(POST_BID + POST_ASK) > 50%` PERO `Maker_Fills == 0`.
- **Degradación Microestructural**: `Toxic_Fills >= Maker_Fills` (selección adversa severa).
- **Degeneración de Acción**: Una acción irrelevante (ej. `CANCEL_ALL` o `TAKER_EXIT`) representa > 40% de la distribución.

## 5. Parámetros PPO "Safe Adaptation"
Para no barrer drásticamente los pesos del BC:
- `learning_rate`: Conservador (ej. `5e-5` a `1e-5`).
- `ent_coef`: Bajo-moderado (`0.01` a `0.02`) para permitir que la estructura BC lidere, sin forzar exploración excesiva que rompa el maker constraint.
- `clip_range`: Ajustado (`0.15` a `0.1`) para evitar grandes zancadas en updating.
- `target_kl`: `0.01` a `0.02`, forzando early stopping en la época si PPO se desvía demasiado.
