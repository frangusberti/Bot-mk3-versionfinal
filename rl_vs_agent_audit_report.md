# PPO Architectural Audit: [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs) vs [agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs)

This targeted audit deconstructs the structural divergence between how the Gymnasium RL training environment ([rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs)) executes actions versus how the production Orchestrator ([agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs)) executes the identical policy outputs.

## 1. Trace: Training-Time Decision Flow ([rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs))
Inside [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs) (`EpisodeHandle::apply_action`, lines 208-319), PPO decisions follow a highly simplified "God-mode" path:
1. **Action Decoding:** Parses the integer action (e.g., `OPEN_LONG`, `REDUCE_50`).
2. **Static Sizing:** Computes `target_notional = max_pos_frac * equity`. Sizing is static and oblivious to market regimes or execution quality.
3. **Raw Execution:** Instantly flips the position by emitting `OrderType::Market` to the simulation engine.
4. **Reward Calculation:** Computes log-return based strictly on the instantaneous fill of a Taker order, minus generic fixed penalties.

## 2. Trace: Runtime Decision Flow ([agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs))
Inside [agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) (lines 600-1300), the environment is heavily guarded and regulated:
1. **Pre-Inference Gate (`RiskGate`):** Checks for consecutive losses and drawdowns. If triggered, *bypasses the neural network entirely* and forces a `HOLD`.
2. **Inference & Alpha Logit:** Calls Python [infer_action()](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/policy.rs#115-180), decoding the continuous value (V(s)) and log_prob.
3. **Execution Quality Block:** Analyzes the OrderBook spread and microprice volatility. Un-tradeable conditions veto the action immediately.
4. **Cost Model Gate:** Computes [ExpectedTradeCost](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#44-52) and ensures the predicted `alpha_logit` offsets the spread/fees. (Previously broken by scaling, now fixed). Vetoes if purely negative expected value.
5. **Dynamic Sizing Block:** Multiplies the base position fraction by `Regime Multiplier` (trend/range detection) and `Quality Multiplier`. A position might be downsized by 90% in bad regimes.
6. **Contrafactual Logging:** Records what *would* have happened as a Taker.
7. **Commission/Urgency Policy:** Translates the action into [Maker](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/structs.rs#66-70), `Taker`, or `Hybrid` execution logic.
8. **Stop Policy Block:** Calculates dynamic Stop Losses (SL) and Take Profits (TP).
9. **Final Execution:** Submits the heavily modified multi-leg or passive order.

---

## 3. Discrepancy Classification

| Skipped Module in [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs) | Function | Classification |
| :--- | :--- | :--- |
| **`RiskGate`** (Cooldowns) | Halts trading on consecutive losses | **Should Remain Runtime-Only.** If RL gets blocked by a cooldown inside the MDP (Markov Decision Process), it interprets the "HOLD" as the optimal action, truncating its exploration. RL must learn to back off organically from bad market features. |
| **`ExecutionQualityBlock`** | Vetoes trading when spread is massive | **Essential to Include (Implicitly).** PPO currently assumes it can trade at mid-price minus a generic fee. It *must* be penalized by the true OrderBook spread in [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs) so it organically learns to avoid illiquid moments. |
| **[CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#66-67)** | Vetoes marginal trades based on V(s) | **Should Remain Runtime-Only.** PPO value estimators are continuously updating. If we hard-veto actions inside training, the critic V(s) mathematically diverges because it never experiences the negative penalty of being wrong. |
| **`DynamicSizingBlock`** | Scales size based on volatility/regime | **Essential to Include.** If RL assumes 100% position size (high PnL variance) but the runtime dynamically shrinks it to 10% (low PnL variance), the Q-values / Critic expectations will be disastrously miscalibrated between train and live. |
| **`StopPolicyBlock`** | Caps extreme losses dynamically | **Essential to Include.** The training simulator must process Stops identically to live. Without it, PPO hallucinations can lead to holding 10% liquidations that live agents would have cut at 1%. |
| **[CommissionPolicy](file:///C:/Bot%20mk3/proto/bot.proto#789-806)** | Upgrades Market to Maker orders | **Optional / Tradeoff.** Simulating Maker limits in RL is notoriously hard (asynchronous fills break step logic). |

---

## 4. Tradeoffs

### Realism vs Training Stability
If we force [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs) to perfectly mirror [agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs) (including Maker passive orders, time-in-force limits, and hard vetoes), the RL environment becomes non-deterministic and highly sparse. The RL agent will repeatedly "try" to act, get vetoed, and learn a completely flat policy where `HOLD = 100%`. Keeping PPO decoupled as an "Oracle" ensures stable gradients.

### Gates-Inside-Training vs Gates-Outside-Training
- **Gates Inside:** The agent explores less. The MDP transitions are interrupted by deterministic code.
- **Gates Outside (Current Architecture):** The agent explores the pure mathematical edge of the asset. The Rust runtime acts as the "adult in the room", taking the pure signal and gating it based on account health. *This is standard practice in Quant RL.*

---

## 5. Architectural Recommendation

**Verdict: Keep PPO as an Alpha-Only Signal, but Align the Sizing and Slippage.**

Do not port the veto gates (`RiskGate` or [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#66-67)) into [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs). Instead, implement a **Minimal Synchronization Design**:

1. **Synchronize Sizing (`DynamicSizingBlock`):**
   Copy the `DynamicSizingBlock::compute_size()` logic into [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs) immediately before `target_notional` calculation. If the runtime is going to shrink a trade due to a dead regime, the RL agent *must* experience the shrank PnL reward so it learns to ignore dead regimes.
2. **Synchronize True Slippage (`ExecutionQualityBlock`):**
   Instead of vetoing trades in [rl.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs), inject the real `spread_bps` from the [FeatureRow](file:///C:/Bot%20mk3/proto/bot.proto#338-342) directly into [RewardCalculator](file:///C:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#33-34). Let PPO take the action, hit the wide spread, take a massive PnL hit, and learn to naturally output `HOLD`.
3. **Synchronize Stop Losses (`StopPolicyBlock`):**
   The core simulation engine in `bot-data` needs to honor the SL distances inside RL mode.

### Safest Next Step Before Retraining
Before deleting old data and spinning up massive GPU cycles, the exact next step must be:
**Copy `DynamicSizingBlock` into `rl.rs: apply_action()` so that the reinforcement learning environment accurately scales the PnL impacts based on Regime Multipliers.**
