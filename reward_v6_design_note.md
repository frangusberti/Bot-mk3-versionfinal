# Reward v6: Complete Design Note & Formula Audit

---

## 1. Exact Reward Formula

The reward is computed per decision step (every `decision_interval_ms`, typically 1000ms) in [reward.rs:L84-232](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L84-L232).

### Master Equation

```
R(t) = log_return
     - trade_penalty
     - toxic_penalty
     - exposure_penalty
     + tib_reward
     + maker_reward
     - taker_penalty
     - idle_penalty
     + mtm_signal
     - reprice_penalty
     - cancel_penalty
     - distance_penalty
     + rpnl_reward
     - skew_penalty
     - inventory_change_penalty
     + two_sided_bonus
     - take_action_penalty
     + quote_presence_bonus
```

### Term-by-Term Definitions (with v6 weights)

| # | Term | Formula | v6 Weight | Sign |
|:--|:-----|:--------|:----------|:-----|
| 1 | `log_return` | `ln(E_t / E_{t-1})` | 1.0 (implicit) | + |
| 2 | `trade_penalty` | `λ_ot × num_trades` | 0.0001 (default) | − |
| 3 | `toxic_penalty` | `λ_toxic × num_toxic_fills` | **0.005** | − |
| 4 | `exposure_penalty` | `λ_exp × (\|exposure\| / equity)` | 0.00001 (default) | − |
| 5 | `tib_reward` | `λ_tib × tib_count` | 0.0 (disabled) | + |
| 6 | `maker_reward` | `λ_maker × len(maker_fills)` | **0.002** | + |
| 7 | `taker_penalty` | `λ_taker_fill × num_taker_fills` | **0.002** | − |
| 8 | `idle_penalty` | `λ_idle × active_orders` (only if no fills this step) | 0.00001 | − |
| 9 | `mtm_signal` | Deferred adverse selection — see §1.1 below | see §1.1 | ± |
| 10 | `reprice_penalty` | `λ_reprice × num_reprices` | **0.0001** | − |
| 11 | `cancel_penalty` | `λ_cancel` (if `is_cancel_all`) | 3e-7 | − |
| 12 | `distance_penalty` | `λ_dist × avg_distance_to_mid_bps` | **0.0001** | − |
| 13 | `rpnl_reward` | `realized_pnl × λ_rpnl` | 0.001 | + |
| 14 | `skew_penalty` | `λ_skew × skew × \|skew\|` where `skew = exposure / equity` | **0.0002** | − |
| 15 | `inventory_change_penalty` | `λ_inv × \|exposure_t − exposure_{t-1}\|` | **0.005** | − |
| 16 | `two_sided_bonus` | `λ_2s` (if both bid and ask orders active) | 0.001 | + |
| 17 | `take_action_penalty` | `λ_ta` (if action is CLOSE_POSITION) | **0.003** | − |
| 18 | `quote_presence_bonus` | `λ_qp × active_orders` (if `dist < 15 bps`, not taker, not cancel) | 0.00015 | + |

#### Validation gate
If `equity ≤ 0` or any equity value is non-finite → [R(t) = -1.0](file:///c:/Bot%20mk3/crates/bot-data/src/features_v2/schema.rs#13-119) ([reward.rs:L106-108](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L106-L108))

If [R(t)](file:///c:/Bot%20mk3/crates/bot-data/src/features_v2/schema.rs#13-119) is non-finite after combination → [R(t) = -1.0](file:///c:/Bot%20mk3/crates/bot-data/src/features_v2/schema.rs#13-119) ([reward.rs:L227-229](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L227-L229))

### 1.1 MTM / Adverse Selection Signal (Deferred)

This is the most complex component. Code: [reward.rs:L139-171](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L139-L171).

**Mechanism:**
1. When a maker fill occurs, a [PendingMtm](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#3-8) entry is created recording [(initial_mid, fill_side, remaining_ms=3000)](file:///c:/Bot%20mk3/crates/bot-data/src/features_v2/schema.rs#127-208).
2. Each subsequent step decrements `remaining_ms` by `elapsed_ms` (decision interval).
3. When `remaining_ms ≤ 0` (after 3000ms), the entry expires and the price movement is evaluated:

```
price_delta = (current_mid − initial_mid) × side_mult
move_bps = price_delta / initial_mid
```

4. **If `move_bps < 0`** (adverse — price moved against the fill):
   ```
   mtm_signal −= 0.5 × |move_bps|    // PENALTY
   ```
5. **If `move_bps ≥ 0`** (favorable — price moved in the direction of the fill):
   ```
   mtm_signal += 0.3 × move_bps      // BONUS (reduced)
   ```

**v6 changes**: Window shortened from 5000ms to **3000ms** (focuses on immediate informed flow). Penalty multiplier increased from 0.075 to **0.5** (7x). Bonus multiplier decreased from 0.8 to **0.3** (to stop the agent from gambling for favorable moves).

### 1.2 Post-Delta Threshold (Action-Level Gate)

Not a reward term — an **action-level hard gate** in [rl.rs:L497-498](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L497-L498) and [rl.rs:L535-536](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L535-L536).

When `REPRICE_BID` or `REPRICE_ASK` is emitted, if the price delta between the existing order and the new target is < `post_delta_threshold_bps`, the reprice is **silently rejected** and the existing order is kept. v6 value: **0.5 bps** (vs 0.05 bps in v5).

> [!IMPORTANT]
> This gate applies **only to REPRICE**, not to initial POST. Initial `POST_BID`/`POST_ASK` place orders unconditionally at the synthetic passive price or BBO. The threshold prevents micro-repricing jitter.

---

## 2. Component-by-Component Explanation

### A. Toxic Maker Fill Penalty (Term 3)

| Aspect | Detail |
|:---|:---|
| **Targets** | Fills where the market price crossed through the resting order — the order was "run over" by informed flow |
| **Type** | Soft penalty (proportional to count). No hard gate. |
| **Classification** | Set in [execution.rs:L319-334](file:///c:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs#L319-L334). A fill is `is_toxic = true` when `price_crossed = true`: for a Buy limit, `best_ask ≤ order_price`; for a Sell limit, `best_bid ≥ order_price`. |
| **Expected behavior change** | Agent learns to avoid posting quotes at price levels likely to be crossed. Should reduce the proportion of fills that are toxic. |
| **v6 weight** | 0.005 (5x v5). Each toxic fill now costs the equivalent of ~50 bps of log-return. |

### B. Adverse Selection / MTM Signal (Term 9)

| Aspect | Detail |
|:---|:---|
| **Targets** | Post-fill price movement that reveals the fill was against informed flow |
| **Type** | Soft penalty/bonus (deferred evaluation, magnitude-proportional) |
| **v6 tuning** | Penalty 0.5 (7x v5), bonus 0.3 (0.38x v5), window 3000ms (0.6x v5) |
| **Expected behavior change** | Agent becomes much more averse to fills that are followed by adverse price movement. The asymmetric penalty/bonus ratio (0.5 vs 0.3) means the downside of a bad fill is stronger than the upside of a good fill — discouraging gambling for directional bets. |

### C. CLOSE_POSITION Taker-Cost (Terms 7, 17)

| Aspect | Detail |
|:---|:---|
| **Targets** | CLOSE_POSITION (Action 6), which submits a Market Order (literal taker) |
| **Type** | Two-layer soft penalty: `taker_fill_penalty` per fill + `taker_action_penalty` per action |
| **Total cost** | Per CLOSE_POSITION: 0.003 (action) + 0.002 (fill) = **0.005 total** |
| **Expected behavior change** | Agent strongly prefers to exit positions via REPRICE (passive unwind) rather than market-order exit. Only uses CLOSE_POSITION in genuine emergency (large adverse move). |

### D. Inventory Control (Terms 4, 14, 15)

| Aspect | Detail |
|:---|:---|
| **Targets** | Three layers: (4) linear leverage penalty, (14) quadratic skew penalty, (15) inventory change penalty |
| **Type** | All soft penalties |
| **Skew formula** | `skew = exposure / equity; penalty = 0.0002 × skew × |skew|` — **superlinear** (cubic-shaped for signed skew). Penalizes large inventory more than proportionally. |
| **Inventory change** | `0.005 × |Δexposure|` — penalizes rapid swings regardless of direction. Targets pendulum-effect where agent oscillates between long/short. |
| **Expected behavior change** | Agent maintains tighter inventory, avoids accumulating large directional positions, and reduces oscillation frequency. |

### E. Realized PnL Signal (Term 13)

| Aspect | Detail |
|:---|:---|
| **Targets** | Direct reinforcement for closing profitable trades |
| **Type** | Soft signal (proportional to realized PnL delta per step) |
| **Weight** | 0.001 (unchanged from v5) |
| **Expected behavior change** | Agent receives a direct signal when a round-trip closes, complementing the log-return which already captures equity changes. |

### F. Quote Placement / Distance Gating (Terms 12, 18, §1.2)

| Aspect | Detail |
|:---|:---|
| **Targets** | Three mechanisms: (12) distance penalty per step, (18) quote presence bonus with distance cap, (§1.2) reprice delta hard gate |
| **Distance penalty** | `0.0001 × avg_distance_to_mid_bps` — penalizes orders far from mid. Note: this is a **paradox** with the toxic-fill penalty. Orders close to mid → more toxic fills. Orders far from mid → more distance penalty. The agent must find the optimal distance where spread capture exceeds adverse selection. |
| **Quote presence bonus** | Only awarded if `distance < 15 bps` and not a taker or cancel action. Incentivizes being present near the spread. |
| **Reprice gate** | Hard gate: reprices < 0.5 bps from current order are silently rejected. Prevents micro-jitter. |
| **Expected behavior change** | Agent posts at intermediate distances (not at mid, not far away), and does not waste reprices on tiny adjustments. |

### G. Reprice / Chasing Penalty (Term 10)

| Aspect | Detail |
|:---|:---|
| **Targets** | Excessive repricing that leads to quote-chasing and eventual toxic fills |
| **Type** | Soft penalty (proportional to reprice count per step) |
| **Weight** | 0.0001 (2x v5) |
| **Expected behavior change** | Agent reprices less frequently, accepting that the quote position may become stale rather than chasing price. Combined with the hard gate (§1.2), this discourages both small and large reprices. |

### H. Microprice Selectivity

> [!WARNING]
> **Not implemented as a separate reward term.** There is no explicit microprice-gating penalty in v6. The microprice signal enters the agent through the **observation vector** (feature `microprice` is an obs input), and the MTM penalty (Term 9) provides an indirect incentive to avoid posting when microprice predicts adverse flow. A dedicated microprice penalty would require new Rust code and is deferred to v7 if v6's MTM penalty proves insufficient.

---

## 3. Execution-Path Separation

```mermaid
graph TD
    A[Step t: Action Selected] --> B{Action Type?}
    B -->|POST_BID/ASK| C[Limit Order Submitted]
    B -->|REPRICE_BID/ASK| D{Delta > 0.5 bps?}
    B -->|CLOSE_POSITION| E[Market Order Submitted]
    B -->|HOLD| F[No Order Action]
    B -->|CLEAR_QUOTES| G[Cancel All Orders]
    
    D -->|Yes| H[Cancel + Resubmit Limit]
    D -->|No| I[Silently Rejected]
    
    C --> J[Execution Engine Matching]
    H --> J
    E --> J
    
    J --> K{Fill Occurred?}
    K -->|Maker Fill| L{Price Crossed?}
    K -->|Taker Fill| M[taker_fill_penalty: -0.002]
    K -->|No Fill| N[idle_penalty if orders exist]
    
    L -->|Yes: Toxic| O[toxic_penalty: -0.005]
    L -->|No: Clean| P[maker_reward: +0.002]
    
    O --> Q[PendingMtm created]
    P --> Q
    
    Q --> R{After 3000ms}
    R -->|Adverse move| S[mtm_signal: -0.5 × |move_bps|]
    R -->|Favorable move| T[mtm_signal: +0.3 × move_bps]
    
    E --> U[take_action_penalty: -0.003]
    
    subgraph "Per-Step Continuous"
        V[distance_penalty: -0.0001 × dist_bps]
        W[skew_penalty: -0.0002 × skew²]
        X[inventory_change: -0.005 × |Δexp|]
        Y[quote_presence: +0.00015 × n_orders]
        Z[two_sided: +0.001 if bid+ask]
    end
```

### Summary Table: Penalty by Execution Path

| Path | Immediate Cost | Deferred Cost | Total Worst Case |
|:---|:---|:---|:---|
| **Clean maker fill** | +0.002 (bonus) | −0.5 × \|AS\| or +0.3 × AS | depends on AS |
| **Toxic maker fill** | −0.005 + 0.002 = −0.003 | −0.5 × \|AS\| | −0.003 − 0.5×\|AS\| |
| **CLOSE_POSITION** | −0.003 (action) − 0.002 (taker fill) = −0.005 | none | −0.005 per use |
| **Reprice** | −0.0001 per reprice | none | small |
| **HOLD (no orders)** | 0.0 | 0.0 | 0.0 |
| **HOLD (with orders)** | −0.00001 (idle) + 0.00015 (presence) ≈ +0.00005 | none | small positive |

---

## 4. Code Mapping

### 4.1 Python: Parameter Definition

[ppo_v16_reward_v6.py:L37-68](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py#L37-L68) — `REWARD_V6` dict, all 17 parameter values.

```python
REWARD_V6 = dict(
    reward_maker_fill_bonus=0.002,           # L39
    reward_taker_fill_penalty=0.002,         # L40
    reward_toxic_fill_penalty=0.005,         # L41
    reward_idle_posting_penalty=0.00001,     # L44
    reward_quote_presence_bonus=0.00015,     # L45
    reward_distance_to_mid_penalty=0.0001,   # L48
    post_delta_threshold_bps=0.5,            # L49
    reward_reprice_penalty_bps=0.0001,       # L50
    reward_mtm_penalty_window_ms=3000,       # L53
    reward_mtm_penalty_multiplier=0.5,       # L54
    reward_adverse_selection_bonus_multiplier=0.3,  # L55
    reward_skew_penalty_weight=0.0002,       # L58
    reward_inventory_change_penalty=0.005,   # L59
    reward_two_sided_bonus=0.001,            # L62
    reward_realized_pnl_multiplier=0.001,    # L63
    reward_cancel_all_penalty=3e-7,          # L66
    reward_taker_action_penalty=0.003,       # L67
)
```

### 4.2 Python → Rust: Parameter Flow

1. `REWARD_V6` is unpacked as `**REWARD_V6` into `GrpcTradingEnv.__init__()` at [ppo_v16_reward_v6.py:L182](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py#L182).
2. [GrpcTradingEnv](file:///c:/Bot%20mk3/python/bot_ml/grpc_env.py#18-226) sets `RLConfig` proto fields at [grpc_env.py:L43+L110](file:///c:/Bot%20mk3/python/bot_ml/grpc_env.py#L43).
3. On `ResetEpisode` gRPC call, the Rust server reads `RLConfig` and constructs [RewardConfig](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#28-49) at [rl.rs:L989-1009](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L989-L1009).

### 4.3 Rust: Reward Input Computation

[rl.rs:L575-655](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L575-L655) — [compute_reward()](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#575-656) method on [EpisodeHandle](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#92-131):

| Input | Computed at | Description |
|:---|:---|:---|
| `num_toxic_fills` | [rl.rs:L587-589](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L587-L589) | Count of fills with `is_toxic == true` |
| `maker_fills` | [rl.rs:L608-613](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L608-L613) | Vec of [MakerFillDetail](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#77-80) from fills with `LiquidityFlag::Maker` |
| `num_taker_fills` | [rl.rs:L615-617](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L615-L617) | Count of fills with `LiquidityFlag::Taker` |
| `distance_to_mid_bps` | [rl.rs:L622-629](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L622-L629) | Avg `\|order.price − mid\| / mid × 10000` across active orders |
| `is_two_sided` | [rl.rs:L578-584](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs#L578-L584) | True if both Buy and Sell orders active |
| `is_taker_action` | passed from action dispatch | True if action was `CLOSE_POSITION` |

### 4.4 Rust: Reward Formula

[reward.rs:L84-232](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L84-L232) — `RewardCalculator::compute_reward()`:

| Term | Code Line(s) |
|:---|:---|
| log_return | [L111](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L111) |
| trade_penalty | [L114](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L114) |
| toxic_penalty | [L117](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L117) |
| exposure_penalty | [L120-121](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L120-L121) |
| tib_reward | [L124](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L124) |
| maker_reward | [L127](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L127) |
| taker_penalty | [L130](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L130) |
| idle_penalty | [L133-137](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L133-L137) |
| mtm_signal | [L139-171](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L139-L171) |
| reprice_penalty | [L174](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L174) |
| cancel_penalty | [L175](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L175) |
| distance_penalty | [L178](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L178) |
| rpnl_reward | [L181](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L181) |
| skew_penalty | [L184-185](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L184-L185) |
| inventory_change | [L188](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L188) |
| two_sided_bonus | [L191](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L191) |
| take_action_penalty | [L194](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L194) |
| quote_presence | [L197-201](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L197-L201) |
| combination | [L208-225](file:///c:/Bot%20mk3/crates/bot-data/src/experience/reward.rs#L208-L225) |

### 4.5 Toxic Fill Classification

[execution.rs:L319-334](file:///c:/Bot%20mk3/crates/bot-data/src/simulation/execution.rs#L319-L334):

```rust
// price_crossed: true if market moved THROUGH the resting order
let price_crossed = match order.side {
    Side::Buy  => best_ask <= order_price,  // L321-324
    Side::Sell => best_bid >= order_price,   // L325-328
};
if price_crossed {
    qty_filled_from_queue = order.remaining;
    is_toxic = true;                         // L333
}
```

### 4.6 Pilot Callback

[ppo_v16_reward_v6.py:L71-138](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py#L71-L138) — [RewardV6Callback](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py#71-139):
- Checkpoint schedule: [L75](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py#L75) — `[50k, 100k, 150k, 200k, 250k, 300k]`
- Scorecard printing: [L119-127](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py#L119-L127)
- Passivity alert: [L130-131](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py#L130-L131) — `HOLD > 80%`
- Toxicity alert: [L132-135](file:///c:/Bot%20mk3/python/ppo_v16_reward_v6.py#L132-L135) — `toxic / maker > 50%`

---

## 5. Pilot Protocol

### 5.1 Checkpoints

| Step | Label | Purpose |
|:---|:---|:---|
| 50k | Early | Detect passivity collapse or immediate instability |
| 100k | Baseline | First meaningful action distribution comparison |
| 150k | Midpoint | Confirm reward signal is differentiating actions |
| 200k | Assessment | Decision point: continue or fail-fast |
| 250k | Consolidation | Quality of fills should be improving |
| 300k | Final | Full audit for go/no-go decision |

### 5.2 Scorecard Fields (per checkpoint)

1. **Economics**: Net PnL (%), Profit Factor, Gross Profit, Gross Loss, Total Fees
2. **Fills**: Total maker fills, toxic fills, toxic fill ratio (toxic/maker)
3. **Action Distribution**: HOLD%, POST_BID%, POST_ASK%, REPRICE_BID%, REPRICE_ASK%, CLEAR_QUOTES%, CLOSE_POSITION%
4. **Drawdown**: Max drawdown (%)

### 5.3 Automated Alerts

| Alert | Trigger | Implication |
|:---|:---|:---|
| **Passivity Collapse** | `HOLD% > 80%` at any checkpoint | Penalties too aggressive, agent retreating |
| **Toxic Fill Dominance** | `toxic_fills / maker_fills > 50%` | Toxic fill penalty insufficient or MTM window too short |
| **CLOSE_POSITION Abuse** | `CLOSE_POSITION% > 5%` | Taker path still exploited despite higher penalty |
| **Zero Fills** | `maker_fills == 0` after 100k | Agent not engaging at all |

### 5.4 Success Criteria (at 300k)

All of the following must be met:
1. `HOLD% < 60%` — actively quoting
2. `toxic_fills / maker_fills < 40%` — improved fill quality vs v15's 500k
3. `Profit Factor > 1.0` — not losing money on average
4. `CLOSE_POSITION% < 3%` — not abusing taker path
5. `POST_BID + POST_ASK > 25%` — actively posting on both sides

### 5.5 Fail-Fast Criteria (stop early)

| Condition | When to check | Action |
|:---|:---|:---|
| `HOLD% > 90%` | 50k | **Stop.** Reduce toxic_fill_penalty to 0.003, reduce mtm_penalty_multiplier to 0.3 |
| `HOLD% > 85%` | 100k | **Stop.** Same adjustments as above |
| `maker_fills == 0` | 100k | **Stop.** Increase maker_fill_bonus to 0.004, reduce distance_penalty to 5e-5 |
| `Net PnL < -0.5%` with `toxic_ratio > 60%` | 200k | **Stop.** Fundamental problem — toxic fills not being avoided |
| Divergence / NaN rewards | Any | **Stop immediately.** Debugging required |

### 5.6 What Would Justify Stopping Early (Positive)

If at 150k: `PF > 2.0` AND `HOLD% < 50%` AND `toxic_ratio < 30%` → skip directly to 300k checkpoint for final assessment. The agent has learned the reward shape fast.

---

## 6. Sanity-Check Expectations

### What should improve first (in order of expected appearance):

| Priority | Metric | Expected at | Reasoning |
|:---|:---|:---|:---|
| 1 | **Action distribution** | 50–100k | First observable change. Agent should show mixed actions, not HOLD-dominant. The quote_presence_bonus and two_sided_bonus should prevent passivity. |
| 2 | **Reduced CLOSE_POSITION usage** | 50–100k | 3x taker action penalty makes CLOSE_POSITION very expensive. Agent should learn to avoid it quickly. |
| 3 | **Reduced toxic fill ratio** | 100–200k | The 5x toxic_fill_penalty and 7x MTM penalty make toxic fills very costly. Agent should learn to avoid posting in adverse contexts. |
| 4 | **Spread capture** | 150–300k | Downstream effect of reduced toxic fills. If fewer fills are toxic, average spread capture should move toward zero or positive. This is the slowest metric to improve because it depends on both entry quality and exit quality. |
| 5 | **Signed adverse selection** | 200–300k | Hardest to improve. Requires the agent to learn from the 3s deferred MTM signal which fills are informed vs uninformed. Only visible after substantial training. |

### What should NOT change:

| Metric | Expected | Why |
|:---|:---|:---|
| `POST_BID + POST_ASK > 20%` | Sustained | quote_presence_bonus preserved |
| `REPRICE usage > 5%` | Sustained | Agent still needs to manage orders |
| `maker_fills > 0` per eval | Always | If this drops to 0, the penalties are too aggressive |

### Red flags that would indicate v6 is broken:

1. **HOLD > 80%** at any checkpoint → passivity collapse
2. **Maker fills = 0** → agent refuses to trade entirely
3. **PF < 0.5 with many fills** → agent is actively losing on every trade (wrong signal)
4. **CLOSE_POSITION > 10%** → taker penalty not working
