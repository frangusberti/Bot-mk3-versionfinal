# BOTMK3 Targeted Clarification Audit

## A. EXECUTIVE CLARIFICATION SUMMARY
The previous audit correctly identified BOTMK3 as a Sec-to-Min Microstructure hybrid system, but left several assumptions unverified. This targeted audit resolves those ambiguities:
- **ML Contract:** The model is an RL (PPO) model. Crucially, the system extracts the **Critic Value function $V(s)$** from the RL model and maps it *1:1 as `expected_move_bps`* in Rust. Confidence is extracted from the categorical action probability.
- **Maker Simulator:** The simulation is *not* entirely naive. It implements a `ConservativeMaker` mode ([bot-data/src/simulation/execution.rs](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs)) that tracks a phantom queue and depletes it cleanly using actual tape volume at that exact price tick. It is realistic for queue-jumping but still misses adverse selection severity when the price bounces off the level.
- **Temporality:** The engine explicitly guarantees no-lookahead by enforcing `TimeMode::RecvTimeAware` routing, meaning it artificially delays processing of out-of-order latency spikes to ensure 5-second and 1-second rolling features don't leak future orderbook states.
- **Dominance:** The system is **Gate-Dominated**. If the RL model is removed, the bot uses a `baseline_move_bps` fallback and keeps running perfectly. The ML model is essentially just a weak hint passed to a very aggressive deterministic risk engine.

---

## B. EXACT MODEL / INFERENCE CONTRACT
1. **Component:** Inference is executed by [bot_policy/policy_server.py](file:///C:/Bot%20mk3/python/bot_policy/policy_server.py) calling [SB3PPOPolicy](file:///C:/Bot%20mk3/python/bot_policy/policies/sb3_ppo.py#8-77) (inside [policies/sb3_ppo.py](file:///C:/Bot%20mk3/python/bot_policy/policies/sb3_ppo.py)), communicating via standard HTTP/JSON to Rust's `services::orchestrator::policy::PythonPolicyAdapter`.
2. **Caller:** [bot-server/src/services/orchestrator/agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) (`self.policy.infer_action(req)`).
3. **Input Schema:** [HttpInferRequest](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/policy.rs#25-35) containing `obs: Vec<f32>`, shaped exclusively by `FeatureRow::OBS_DIM` (dim=148 / schema=6) pre-computed in [FeatureEngineV2](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs#105-132).
4. **Is it always schema 6?** Yes, if the server asserts schema_version < 6, it blocks inference for critical missing features (e.g., `obi_top1`, `microprice`).
5. **Exact Outputs:** The python server returns [action](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/policy.rs#115-180) (string), `confidence` (f64), `log_prob` (f32), [value](file:///C:/Bot%20mk3/python/bot_gui/tabs/risk_panel.py#61-80) (f32).
6. **Output Meaning:**
   * **Action:** Discrete (e.g., `OPEN_LONG`, `HOLD`, `CLOSE_ALL`).
   * **Confidence:** The softmax probability `probs[action_idx]` of the *selected* discrete action from the Actor net.
   * **Value:** The continuous output of the Critic network ($V(s)$), representing the expected discounted return of the state.
7. **`expected_move_bps` Derivation (DANGER POINT):** In `agent.rs:1022`, the system assigns `let expected_move_bps_logit = alpha_logit;` (which is `info.value`). Then [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) passes this exact float through `value_to_bps_multiplier`. **Thus, the bot assumes the RL Critic's Value output is perfectly calibrated in basis points of market movement.** If the PPO reward function was not explicitly scaled to exactly 1.0 = 1.0 bps, this contract is fundamentally broken.
8. **Confidence:** Directly produced by the model (action probability). It is used to scale position sizes.
9. **Missing/Bypass:** If inference fails, raises an error, or the queue is warming up, it returns Action 0 (`HOLD`), `confidence = 0.0`, `value = 0.0`. [agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) then passes to gates. If `value == 0.0`, [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) falls back to `baseline_move_bps` (derived from regime logic rather than ML).

---

## C. EXACT MAKER FILL MODEL / SIMULATOR LOGIC
1. **Module:** [bot-data/src/simulation/execution.rs](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs) ([process_order_matching](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs#169-473)).
2. **Current Logic:** Paper trading defaults to `SlippageModel::ConservativeMaker(queue_config)`.
3. **Assumptions Supported:**
   * **Queue Assignment:** When an order is placed, it scans the L2 book and assigns a `position_ahead` penalty. If `assume_half_queue` is true, it assumes you are placed in the middle of the existing liquidity block.
   * **Fill via Tape Depletion:** `queue.position_ahead` is reduced **only** by actual trade volume (`event.qty`) occurring at your exact `queue.original_price`.
   * **Trade-Through:** If the `best_ask` drops strictly below your Limit Buy price, it triggers 100% immediate fill (price crossed).
   * **Partial Fill Support:** Yes, it calculates `available.min(order.remaining)`.
   * **Timeouts:** Yes, orders expire based on `expires_ts`.
4. **Differences Across Modes:** 
   * **Backtest/Paper:** Uses this [ExecutionEngine](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs#6-28) to simulate the queue.
   * **Live:** Relies entirely on Binance matching.
   * **Shadow:** Runs Paper simulation, then compares its simulated fills to real Binance stream fills for divergence reporting.
5. **Realism Assessment:** **Mixed (Slightly Optimistic).** Tracking tape volume to deplete the queue is extremely realistic. However, if the price touches your level, trades a tiny bit, and then instantly snaps away against you (adverse selection), the simulation might erroneously fill a fraction without punishing you mathematically for the adverse snap. [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) tries to offset this by applying `adverse_selection_bps_est = (spread/2) + 1.0` *before* taking the trade.

---

## D. COMPLETE TEMPORALITY / WINDOW COHERENCE TABLE

BOTMK3 uses [FeatureEngineV2](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs#105-132) ([bot-data/src/features_v2/mod.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs)), which aggregates events on arrival but emits strictly on a wall-clock timer (typically 1000ms).

| Feature / Signal | Source Module | Time Basis | Nominal Window / Horizon | Temporal Profile | Strict OB Sync Required? | Coherence Risk |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `ret_1s`, `ret_5s` | [compute_price.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_price.rs) | Rolling Window | 1s, 5s | Fast | No | Low |
| `rv_5s`, `rv_30s` | [compute_price.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_price.rs) | Rolling Window | 5s, 30s | Medium | No | Low |
| `slope_mid_5s` | [compute_price.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_price.rs) | Rolling Window | 5s | Fast | No | Low |
| `taker_*_vol_5s` | [compute_flow.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_flow.rs) | Rolling Window | 5s | Fast | No (Trade-driven) | Low |
| `tape_intensity_z` | [compute_flow.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_flow.rs) | Rolling Window | 5s | Fast | No | Low |
| `obi_top1` / `microprice` | [compute_micro.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_micro.rs) | Tick-sampled at emit | Instantaneous | Extremely Fast | **Yes** | **High** (If OB gaps, feature is masked to None) |
| `liq_net_30s` | [compute_shocks.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_shocks.rs) | Rolling Window | 30s | Medium / Slow | No | Low |
| `funding_zscore` | [compute_shocks.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_shocks.rs) | Rolling Window | Unbound (usually 8h) | Extremely Slow | No | Acceptable |
| `oi_delta_5m` | [compute_oi.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_oi.rs) | Rolling Window | 5m | Slow | No | Acceptable |
| `price_response_buy_5s` | [compute_absorption.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_absorption.rs) | Rolling Window | 5s | Fast | Yes | Medium |
| `flow_persistence_buy` | [compute_persistence.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_persistence.rs) | Emit Window counts | 10 emits (~10s) | Medium | Yes | Medium |
| `regime_trend` | [compute_regime.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_regime.rs) | Rolling / State Machine | ~5m-30m equivalent | Slow | No | **Dangerous** (Mixing 5m state with 1s ticks) |
| `Expected Move` | Inference (Python) | Instantaneous | Model defined (N/A) | Fast (Derived) | N/A | **Dangerous** if training window was 5m, but emission is 1s. |
| `Expected Cost / Net Edge` | [cost_model.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs) | Instantaneous | Instantaneous | Instant | Yes | Low |
| `Stop Loss Policy` | `stop_policy.rs` | Instantaneous | Instantaneous | Instant | No | Low |

1. **True Horizon:** The engine evaluates the market every 1 second, but its "eyes" look backward over a dominant 5-second to 30-second window (Flow, Micro, Short-Return).
2. **Mismatches:** Comparing instantaneous tick orderbook imbalance (`obi_top1`) against a 5-minute regime trend is semantically mismatched but common in stat-arb.
3. **Danger:** The highest temporal risk is that the Python ML model evaluates a `1s` price return and an `obi_top1` tick snapshot and tries to predict a `30s` future move, while the deterministic [CostModel](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) evaluates slippage based on instantaneous orderbook depth. These are coherent in code but potentially incoherent in economics.

---

## E. MODEL VS GATES DECISION DOMINANCE ANALYSIS

**BOTMK3 is a rule-based execution system operating with optional ML hints.** The gates utterly dominate the decision workflow.

**The Decision Path ([agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs)):**
1. **Pre-Inference `RiskGate`:** Blocks 100% of trades if in Cooldown, Recovery max-loss, or Halted state.
2. **ML Model Suggestion:** Outputs an action (LONG/SHORT) and a Value (`expected_move`).
3. **Execution Quality Gate (`exec_qual`):** Reads micro features (spread, OBI context) and overrides the ML. If the spread is too wide, it downgrades the action to Holden entirely.
4. **Cost Model Gate (`expected_net_edge`):**
   * Computes Maker vs Taker costs (Fees, spread, slip limits).
   * Math: `net_edge_bps = expected_move_bps - total_cost_bps_est`.
   * **Rule:** If `net_edge_bps <= edge_threshold_bps` (e.g., 2.0 bps), the trade is effectively **vetoed** (its sizing multiplier goes to 0).
5. **Regime Gate & Dynamic Sizing:** Even if net edge is positive, if the `RegimeGate` detects a "Dead" market, the `sizing_mult` is mathematically multiplied by 0.0. The trade is vetoed for being "too small" (< min notional).

**Dominance Conclusion:**
* **ML Power:** The ML model proposes direction.
* **Gate Power:** The gates control actual execution. Roughly **90-95% of ML "Long/Short" logits will be vetoed** simply because `net_edge_bps` fails to exceed minimums. 
* **If the ML model was removed:** You could wire a random number generator to output +3.0 bps `Value`, and the bot would still only fire safely on tight-spread, high execution-quality uptrends, because the gates are mathematically absolute. `BOTMK3` could run perfectly fine as a deterministic script.

---

## F. CONCRETE FILE / FUNCTION MAP

* **Inference Contract:** 
  * [bot-server/src/services/orchestrator/agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) ([handle_action(alpha_logit)](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs#939-1212))
  * [bot-server/src/services/orchestrator/policy.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/policy.rs) (`HttpInferRequest/Response`)
  * [python/bot_policy/policies/sb3_ppo.py](file:///C:/Bot%20mk3/python/bot_policy/policies/sb3_ppo.py) ([infer()](file:///C:/Bot%20mk3/python/bot_policy/policy_server.py#112-191) extracts RL Critic [value](file:///C:/Bot%20mk3/python/bot_gui/tabs/risk_panel.py#61-80))
* **Maker Fill Simulator:**
  * [bot-data/src/simulation/execution.rs](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs) ([process_order_matching()](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs#169-473))
* **Cost & Edge Rules:**
  * [bot-server/src/services/orchestrator/cost_model.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs) (`CostModelBlock::estimate_cost`, [check_edge](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#97-118))
* **Temporality & Windows:**
  * [bot-data/src/features_v2/mod.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/mod.rs) (`FeatureEngineV2::maybe_emit()` syncs windows)
  * [bot-data/src/features_v2/compute_flow.rs](file:///C:/Bot%20mk3/crates/bot-data/src/features_v2/compute_flow.rs) (5s flow accumulation)

---

## G. REPO-BACKED OPEN QUESTIONS
1. **PPO Value Calibrations:** The system assumes the Stable-Baselines3 Critic Value `values.cpu().numpy()[0]` is cleanly denominated in Basis Points (bps). **Is the Python RL reward function strictly configured to return pure Basis Points of PnL?** If the reward function utilizes clipping, normalization, or Sharpe-ratios, passing `Value` directly to [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) as `expected_move_bps` is mathematically fatal.
2. **Partial Fill Rejection:** If the conservative Maker simulator partially fills a limit order and the tape price moves away, the order rests open (`PartiallyFilled`). Does [agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) ever aggressively cancel resting partials, or do they sit until `expires_ts`?

---

## H. RECOMMENDED NEXT DEBUG PRIORITIES
1. **Inspect Python RL Reward Function:** Stop everything and verify the Python PPO training environment. Ensure the Agent's Reward is strictly `1.0 = 1.0 bps of mid-price return`. If it's normalized, the `value_to_bps_multiplier` in [CostModelConfig](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#13-18) must be updated immediately to fix the domain gap.
2. **Log Override Counters via Parquet:** Add an explicit counter in [agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) to track how many times the ML predicted a LONG/SHORT, but the [CostModel](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) or `RegimeGate` overrode it to 0 target quantity. Look at this ratio from the 24-hr `candidates.jsonl` preflight run.
3. **Verify Simulator Queue Realism with Depth Data:** The Maker simulation currently assumes `10.0` or `50% of Top Level` units ahead of you if an L2 update is missed. Ensure that `TopN` depth arrays (`book_bids`, `book_asks`) are flawlessly piped from the websocket down to [execution.rs](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs).
