# Action Semantics & Execution Coupling Audit
*Phase 16 - Forensic Discovery*

## 1. Action Semantics Matrix

| Action Type | Execution Target | State Transition Triggered | Associated Penalty / Bonus | Queue Priority |
| :--- | :--- | :--- | :--- | :--- |
| **`HOLD`** | None | Pure No-Op. Leaves all orders exactly where they are. | Earns `quote_presence_bonus` (if near mid). | **Preserved** |
| **`POST_BID`** | `Side::Buy` | Computes a new [synthetic_passive_price](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs#701-740). If the delta from the existing BID order is `< post_delta_threshold_bps` (0.05), it treats it as a Lenient Match / No-Op. If delta `>= 0.05` bps, it explicitly **Cancels** the existing BID order and **Submits** a new one at the wider price. | Earns `quote_presence_bonus` AND `two_sided_bonus` (if ASK exists). Pays `reprice_penalty_bps` (0.00005) only if threshold exceeded. | **Lost** (if repriced) / **Preserved** (if threshold not met) |
| **`POST_ASK`** | `Side::Sell` | Identical to `POST_BID`, but operates on the ASK side exclusively. Note: This does **NOT** cancel the BID side. | Earns `quote_presence_bonus` AND `two_sided_bonus` (if BID exists). Pays `reprice_penalty_bps` (0.00005) only if threshold exceeded. | **Lost** (if repriced) / **Preserved** (if threshold not met) |
| **`CANCEL_ALL`** | Both Sides | Immediately cancels all active Limit Orders. Sends agent to a Flat No-Quote state. | Pays explicit `reward_cancel_all_penalty` (`3e-7` or `4e-7`). Loses all quote bonuses. | **Lost** entirely. |

## 2. Threshold & Anti-Chasing Interaction
In [rl.rs](file:///c:/Bot%20mk3/crates/bot-server/src/services/rl.rs), `POST_BID` and `POST_ASK` use a threshold logic:
```rust
let price_delta_bps = (o.price - price).abs() / price * 10000.0;
if price_delta_bps < self.post_delta_threshold_bps {
    log::info!("RL_POST_BID: Threshold not met ... keeping order");
} else {
    self.cancel_side_orders(Side::Buy); 
    self.exec_engine.submit_order( ... );
}
```
If the agent calls `POST_BID` every second, and the price hasn't moved `0.05 bps`, the backend ignores the command. This means **`POST_BID` acts as a perfectly safe `HOLD` command** for that side, refreshing its intent without losing Queue Priority. 

## 3. The "Active Evasion" Paradox (Semantic Loophole)
We discovered why `CANCEL_ALL` is exactly 0.0% and fills are starved at ~18 per run. The agent has invented **Active Evasion**.

1. **The Fear**: The agent knows that taking a fill incurs an `inventory_change_penalty` (0.005) and MTM risk. 
2. **The Greed**: The agent knows it must maintain quotes to get the `quote_presence_bonus`.
3. **The Loophole**: `CANCEL_ALL` costs a huge `3e-7` penalty. But `POST_BID` computes its price using [get_synthetic_passive_price](file:///C:/Bot%20mk3/crates/bot-server/src/services/rl.rs#701-740), which artificially widens the order away from the mid-price during volatility or adverse imbalance:
   `offset_bps = (spread * 0.5).max(0.2) + (vol * 1.5) + (imbalance_shift);`

By spamming `POST_BID` and `POST_ASK` instead of `CANCEL_ALL`, the agent forces the backend to constantly cancel its dangerous old orders and replace them with new, wider, safer orders further away from the mid-price. 
The agent is dodging fills by **running away from the price**, getting paid the `quote_presence_bonus` to do it, and bypassing the `reward_cancel_all_penalty` entirely because the backend handles the cancellation implicitly within `POST_BID` for only a microscopic `reprice_penalty` (0.00005).

## 4. Recommendations
The action semantics contain a fundamental structural flaw: **`POST_BID` is effectively a cheaper `CANCEL_ALL` + `SAFE_REPRICE` combo.**

**Recommendation A: Action semantics are flawed and must be redesigned.**
1. **Remove `CANCEL_ALL` as a separate action.** It is completely redundant. If the agent wants to be flat, it should just `HOLD` with a `0.0` target_notional, or we explicitly give it an `ActionType::ClearQuotes`. 
2. **Decouple Target Qty from Positions.** Currently, `target_notional` forces the agent to place an order *unless* it already holds that position. The agent cannot explicitly express "I want to have zero active orders on the BID side" using `POST_BID`. It relies on the Sizing Engine to do it. 
3. **Surcharge Reprices:** If we want to stop Active Evasion, `reward_reprice_penalty_bps` must be scaled drastically upward to be mathematically equivalent to or worse than `CANCEL_ALL`, making it cheaper to just cancel and wait rather than constantly shuffling orders backward.
