# Diagnóstico: Colapso ADD_LONG (BOTMK3)

Análisis de probabilidades y señales económicas en el inicio de PPO.

## 1. Distribución de Probabilidades (Logits)

Auditado sobre una observación `FLAT` estándar:

| Modelo | HOLD (0) | OPEN_L (1) | ADD_L (2) | CLOSE_S (8) | Hallazgo |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BC Warm Start** | 0.00% | 0.02% | 0.58% | **96.59%** | **Sesgo Crítico**: El experto v4 etiqueta CLOSE por defecto si la intención es cerrar pero no hay posición. |
| **PPO (vnext_p3_5)**| 10.02% | 9.81% | 10.10% | 10.45% | **Uniformidad**: El checkpoint de 50k parece haber explorado uniformemente hacia afuera del sesgo BC. |

**Conclusión Logits**: El agente no nace con masa anómala en `ADD_LONG`. El sesgo inicial era hacia `CLOSE_SHORT`. La convergencia a `ADD_LONG` ocurrió **durante** el entrenamiento PPO.

## 2. Auditoría de Señales Económicas (Estado FLAT)

Asumiendo configuración `vnext_reward`:

| Caso | Reward Inmediato | Frecuencia | Tipo | Causa de Preferencia |
| :--- | :--- | :--- | :--- | :--- |
| **ADD_LONG (Inválida)** | **-0.1000** | Alta | Hard Block | **Ninguna**. Es la peor opción post-fix. |
| **HOLD** | **0.0000** | Alta | Legal | Neutral. |
| **OPEN_L (Veto SEG)** | **0.0000** | Media | Soft Block | Neutral. No genera órdenes. |
| **OPEN_L (Veto Offset)** | **0.0000** | Alta | Soft Block | Neutral. No genera órdenes. |
| **OPEN_L (Legal/No Fill)**| **+0.0010*** | Baja | Success | **Preferencia Real**: Recibe `quote_presence_bonus`. |

*\*Asumiendo `reward_quote_presence_bonus = 0.001`.*

## 3. Por qué el colapso a ADD_LONG? (Análisis Causal)

Si `ADD_LONG` (inválida) y `HOLD` (legal) daban ambas `0.0000` (antes de mi fix del penalty), el agente entró en una **zona de indiferencia masiva**. 

1.  **Bloqueo de Entradas**: Si el 99% de los intentos de `OPEN_LONG` son bloqueados por SEG o MinOffset, el agente percibe que "Entrar de forma legal" es igual de inútil (0 reward) que "Hacer nada" o "Hacer una acción inválida".
2.  **Deriva Estocástica**: Sin penalidad por invalidez, la red neuronal deriva hacia cualquier acción que no tenga varianza negativa. `ADD_LONG` se convirtió en ese "atractor de seguridad" simplemente por azar o por algún gradiente ruidoso en las features de cuenta.

---

**Veredicto**: El agente prefirió `ADD_LONG` porque **no había diferencia económica** entre fallar legalmente (vetos) y fallar ilegalmente (acción inválida). La solución de agregar el `-0.1` de penalidad es la correcta para romper esta simetría.
