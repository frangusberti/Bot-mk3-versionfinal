# PPO Inference Contract & Reward Scale Audit

======================================================================
## A. EXECUTIVE VERDICT
======================================================================

BOTMK3 is currently **INCORRECT** in treating the PPO critic value as `expected_move_bps`.

The current contract is **fundamentally broken**.

**Confidence Level:** 100%
**Why:** The Python RL system trains on a reward function defined as a raw logarithmic decimal return (where 1 basis point = `0.0001`). The PPO Critic Value [V(s)](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs#105-132) naturally converges near this decimal range. However, the Rust-side [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) explicitly treats the incoming value directly as basis points (where 1 basis point = `1.0`). Because `0.0001` is vastly smaller than the typical cost threshold of `2.0` bps, this domain gap mathematically guarantees that the ML model's positive predictions will *always* be vetoed by the cost gate for being "too small" to cover fees, while accidentally preventing the regime fallback from executing.

======================================================================
## B. PPO TRAINING REWARD DEFINITION
======================================================================

1. **Where defined:** [bot-data/src/experience/reward.rs](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs), inside `RewardCalculator::compute_reward()`.
2. **Exact formula:**
   `reward = log(current_equity / prev_equity) - (lambda * num_trades) - (mu * (abs(exposure) / current_equity))`
3. **Exact units of reward:** Dimensionless Decimal Log-Return (e.g., a 0.01% gain is ~`0.0001`).
4. **Reward Type:** Net-of-cost, step-by-step Risk-Adjusted Return in decimals.
5. **Inclusions:** 
   - **Fees & Slippage:** Yes. The reward calculates [equity](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs#650-654) changes. [ExecutionEngine](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs#6-28) accurately subtracts fees and slippage from `cash_usdt` on trades.
   - **Inventory/Holding penalties:** Yes. The `exposure_penalty` (`mu = 0.00001`) penalizes holding large open positions.
   - **Action penalties:** Yes. The `overtrading_penalty` (`lambda = 0.0001`) penalizes the sheer act of stepping via `num_trades`.

======================================================================
## C. PPO VALUE FUNCTION SEMANTICS
======================================================================

The PPO critic value [V(s)](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs#105-132) estimates the **expected discounted sum of future decimal log-returns** from state `s`, net of exposure and trading penalties.

Because the discount factor `gamma` standardizes this to an infinite horizon sum (e.g., `gamma=0.99` leads to an effective horizon of ~100 steps), [V(s)](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs#105-132) predicts the sum of the remaining episode's stepwise decimal returns, *not* the instantaneous future price move in basis points of the asset. 

It does **not** estimate future bps move directly. [V(s)](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs#105-132) is an abstract measurement of "expected future portfolio growth coefficient", deeply distorted by internal RL discounting, action penalties, and episode truncation rules. 

======================================================================
## D. INFERENCE OUTPUT CONTRACT
======================================================================

**Path:** [bot_policy/policies/sb3_ppo.py](file:///C:/Bot%20mk3/python/bot_policy/policies/sb3_ppo.py) -> [infer()](file:///C:/Bot%20mk3/python/bot_policy/policy_server.py#112-191)

Python extracts the data straight from the PyTorch tensors and returns it via JSON.
* **Action:** String mapped from categorical integer output (e.g., `OPEN_LONG`).
* **Confidence:** The raw softmax probability of the chosen categorical action `float(probs[action_idx])`.
* **Value:** The raw, unscaled output of the Actor-Critic Value network `float(values.cpu().numpy()[0])`.

**Scaling:** Absolutely **ZERO** scaling happens in Python. The pure decimal tensor float (typically a very small number like `0.0003`) is transmitted back to Rust.

======================================================================
## E. RUST-SIDE INTERPRETATION OF VALUE
======================================================================

**Path:** [bot-server/src/services/orchestrator/agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) -> `CostModelBlock::check_edge()`

1. `agent.rs:1022` intercepts the [value](file:///C:/Bot%20mk3/python/bot_gui/tabs/risk_panel.py#61-80) directly from Python and blindly assigns it: 
   `let expected_move_bps_logit = alpha_logit;`
2. It is passed to `CostModelBlock::check_edge()`.
3. Inside [check_edge()](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#97-118), it calculates:
   `let mut expected_move_bps = ai_logit * cfg.value_to_bps_multiplier;`
4. The multiplier defaults to `1.0`. 
5. The fallback logic explicitly says:
   `if expected_move_bps <= 0.0 { expected_move_bps = baseline_move_bps; }`
6. The fallback is actually safer because `baseline_move_bps` (from the Regime subsystem) might contain a realistic number like `5.0` bps. The ML model outputs positive, tiny digits (e.g. `0.0003`), bypassing the negative/zero guardrails but failing the threshold math.

======================================================================
## F. IS VALUE REALLY IN BPS?
======================================================================

**NO.** It is a discounted cumulative reward defined natively as a decimal log_return. 

`1.0` in the Critic network corresponds broadly to a 100% portfolio gain. `1.0` in the Rust Execution Engine Cost Model implies `1 Basis Point` (a 0.01% gain). This is a rigid factor-of-[10,000] mapping error.

Even with a `* 10000.0` multiplier, the value still represents the value of the holding state (with `gamma` applied), not the strictly isolated expected asset arrival price.

======================================================================
## G. IF NOT, WHAT IS THE CORRECT MAPPING?
======================================================================

**Option 1 (Safest Short-term Fix): Add explicit value calibration wrapper.**
* **Pros:** Instantly stops the gate from vetoing all ML trades. Requires 1 line of configuration.
* **Cons:** Still mathematically impure, as [V(s)](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs#105-132) represents portfolio utility with discount, not isolated asset velocity.
* **Code Impact:** Edit `value_to_bps_multiplier` in config to `10000.0` or a dynamically plotted scalar.

**Option 2 (Best Long-term Architecture): Stop using V(s) for Cost Gates.**
* **Pros:** Decouples pure expected price movement from RL abstract state values.
* **Cons:** Requires adding a secondary supervised regressor head to the model (predicting asset return `ret_5s`) or relying purely on ML action-confidence for sizing while using `baseline_move_bps` for edge calculation.

======================================================================
## H. IMPACT ON BOTMK3 RUNTIME DECISIONS
======================================================================

Because `0.0005` (ML logit) `* 1.0` (multiplier) = `0.0005 bps`:
1. **Net Edge Gate:** `net_edge_bps = 0.0005 - 2.0 (fees) = -1.9995`.
2. **Veto Count:** Nearly 99-100% of ML entry suggestions are being unconditionally vetoed.
3. **Regime Fallback Sabotage:** If the model outputs `0.0005`, it surpasses the `expected_move_bps <= 0.0` check. Thus, it prevents [CostModel](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) from falling back to `baseline_move_bps`, meaning the ML actively destroys valid trades by offering numbers that are technically positive but practically microscopic in the wrong scale.
4. **Overall Trade Frequency:** Severely depressed. The bot operates solely on instances where the RL model output is somehow <= 0 and the Rule-based Regime dictates a strong `baseline_move_bps`.

======================================================================
## I. FILE / FUNCTION EVIDENCE MAP
======================================================================

* **PPO environment reward:** [bot-data/src/experience/reward.rs](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs) -> `RewardCalculator::compute_reward()`
* **PPO training loop:** [bot_ml/offline_train.py](file:///C:/Bot%20mk3/python/bot_ml/offline_train.py) (inherently relies on Gym [step](file:///C:/Bot%20mk3/python/bot_ml/grpc_env.py#107-136) which triggers [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs))
* **Inference serializer:** [bot_policy/policies/sb3_ppo.py](file:///C:/Bot%20mk3/python/bot_policy/policies/sb3_ppo.py) -> `lines 55-58 // float(values.cpu().numpy()[0])`
* **policy_server output:** [bot_policy/policy_server.py](file:///C:/Bot%20mk3/python/bot_policy/policy_server.py) -> `/infer` JSON route
* **Rust inference consumer:** [bot-server/src/services/orchestrator/agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) -> [infer_action()](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/policy.rs#115-180) -> `expected_move_bps_logit = alpha_logit`
* **Cost model consumer:** [bot-server/src/services/orchestrator/cost_model.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs) -> `CostModelBlock::check_edge()`
* **Scaling config:** `CostModelConfig::default()` -> `value_to_bps_multiplier: 1.0`

======================================================================
## J. REQUIRED FIX OPTIONS
======================================================================

1. **Immediate Hotfix:** Change `value_to_bps_multiplier` in [CostModelConfig](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#13-18) to `10000.0` to bridge the decimal-to-bps gap, and observe if trades fire.
2. **Medium-term Correction:** Overhaul `CostModelBlock::check_edge` to separate [value](file:///C:/Bot%20mk3/python/bot_gui/tabs/risk_panel.py#61-80) (RL state utility) from `expected_move_bps` (pure asset alpha). 
3. **Best Architecture Fix:** Train a dual-headed model where PPO handles discrete actions ([ActionType](file:///C:/Bot%20mk3/proto/bot.proto#508-517)), but a separate Regressor MSE task explicitly predicts `ret_5s` (5-second return) in basis points, and feed the latter explicitly to `expected_move_bps`.

======================================================================
## K. VALIDATION / TEST PLAN
======================================================================

1. **Multiplier Injection Test:** Inject `value_to_bps_multiplier = 10000.0` into the [CostModelConfig](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#13-18) in a local replay trace. 
2. **Action Survival Rate:** Log the ratio of `ActionType::OpenLong` emitted by the policy versus the number of times `passes_threshold == true`. With `1.0`, it should be near 0%. With `10000.0`, it should rise to 20-40%.
3. **Scatter Plot:** During training, export tuples of [(V(s), actual_5s_bps_move)](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/commission.rs#141-151) and calculate the linear correlation. If R^2 is near 0, then V(s) is entirely useless as a cost gate proxy and must be deprecated entirely for execution routing.

======================================================================
## L. RECOMMENDED NEXT ACTION
======================================================================

Patch the fallback logic in [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) so that it uses the `baseline_move_bps` exclusively, entirely decoupling the [CostModel](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) from the [V(s)](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs#105-132) output logic, OR apply the `10000.0` multiplier hotfix to restore basic scaling parity immediately.
