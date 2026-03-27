# Maker-First RL Contract Design (BOTMK3-M1)

This contract defines the transition of BOTMK3 from a Taker-1s strategy to a **Maker-First Scalping** strategy.

## A. Maker Action Contract
The current discrete action space (HOLD, OPEN_LONG, etc.) is market-centric. For Maker, we shift to **Order Management Actions**:

| ID | Action | Operational Logic |
|---|---|---|
| 0 | `HOLD` | Do nothing. Leave existing orders/positions as is. |
| 1 | `POST_BID` | Cancel all Buy orders. Place new **Limit Buy** at `BestBid`. |
| 2 | `POST_ASK` | Cancel all Sell orders. Place new **Limit Sell** at `BestAsk`. |
| 3 | `JOIN_BID` | Cancel all Buy orders. Place new **Limit Buy** at `BestBid` (checks queue position). |
| 4 | `JOIN_ASK` | Cancel all Sell orders. Place new **Limit Sell** at `BestAsk` (checks queue position). |
| 5 | `CANCEL_ALL` | Cancel all outstanding limit orders. **Keep current position.** |
| 6 | `TAKER_EXIT` | **Emergency.** Cancel all orders + **Market Close** all positions. |

> [!NOTE]
> `REDUCE` actions are removed. The agent must learn to reduce inventory by `POST_ASK` when Long.

---

## B. Maker Lifecycle Contract

1. **Posting Rule:** Orders are submitted with a `latency_ms` delay. 
2. **Queue Rule:** Initial `position_ahead` is set to `(BestLevelQty * assume_half_queue)`.
3. **Fill Rule:** 
   - Filled if `TradeVolume(Price) > position_ahead`. 
   - Filled 100% if `Price` is crossed (BuyPrice > BestAsk).
4. **Adverse Selection Rule:** If filled via `Price crossed`, the fill is "Toxic" (adverse selection).
5. **Partial Fill:** If `TradeVolume < RemainingQty` but `> position_ahead`, a partial fill occurs.
6. **Timeout:** Any order not filled within `N` steps (e.g. 30s) is auto-cancelled to prevent "Regime Ghosting".
7. **Missed Move:** If price moves away from our `POST_BID` and we are not filled, this is an **Observation Failure** (negative reward implicit in lost opportunity).

---

## C. Maker Reward Contract

Standardizing the reward for **Liquidity Provision**:

| Component | Logic |
|---|---|
| **Spread Capture** | Equity Gain from `(FullQty * MidPrice)` crossing the spread. |
| **PPO Reward** | `ΔEquity_Step` (Standardized). |
| **Maker Bonus** | +0.5 bps bonus for `Maker Fill` (incentivizes queue usage). |
| **Toxic Fill Penalty** | -2.0 bps if filled via `Adverse Selection` (Price crossed). |
| **Inventory Penalty** | `- (Exposure * Coefficient)` per step to prevent "Bag-holding". |
| **Overtrading Penalty**| Small penalty per `CANCEL/POST` sequence (prevents jitter). |

---

## D. ConservativeMaker Audit (execution.rs)

**Sufficient:**
- [x] Basic Queue depletion logic.
- [x] Mid/Mark price tracking for PnL.
- [x] Fee differentiation (Maker vs Taker).

**Missing / Too Optimistic:**
1. **Cancel Latency:** Current engine cancels instantly. In real life, `CANCEL` also has `latency_ms`.
2. **Queue Reset:** When the Best Bid price changes, our order "loses priority" or must be moved. Current code needs to handle "Price shifts while order is out".
3. **Adverse Selection Heuristic:** Currently constant. Should scale with `RegimeShock`.

---

## E. Implementation Order

1. **Step 1: Proto.** Update `bot.proto` with new `ActionType` enum values.
2. **Step 2: Execution.** Update `execution.rs` to handle `latency_ms` for CANCEL and add "Order Stale" logic.
3. **Step 3: RL Service.** Update `rl.rs` to map `POST_BID` to the specific `submit_order` calls using `BestBid` price.
4. **Step 4: Reward.** Integrate `Toxic Fill Penalty` in `RewardCalculator`.
5. **Step 5: Training Script.** Update `stage3` to cost-gate based on Maker fees (0 bps) and verify the 100% HOLD is broken.

---

## F. Main Risks
- **Over-Optimization:** Agent learns to "Cheat the simulator" if queue depletion is too predictable.
- **Inventory Skew:** Agent might get stuck with a Long position if it's too "scared" to Taker-exit when Sell-Maker doesn't fill.
- **Micro-Alpha Decay:** As latency is introduced, 1s features might lose predictive power for 1s fills.
