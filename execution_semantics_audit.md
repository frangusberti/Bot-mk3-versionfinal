# Position Management & Trade Lifecycle Audit (Phase 3 -> Phase 4 Transition)

This audit evaluates the current execution semantics of BOTMK3 to identify architecture-level risks before proceeding to Microprice Gating (Phase 4).

## 1. Position Model: "Net-Inventory Only"
The current engine reinforces a **Net-Position Model** per symbol.

- **Storage**: [PortfolioManager](file:///c:/Bot%20mk3/crates/bot-data/src/simulation/portfolio.rs#4-7) uses a `HashMap<String, PositionState>`, ensuring exactly one net position per symbol.
- **Concurrency**: The agent can have **simultaneous BID and ASK orders** (Limit) while carrying inventory. 
- **Scaling In**: Supported and common. `POST_BID/ASK` logic calculate `delta_qty = target_notional/mid - current_qty`. It repeatedly "tops up" to reach the `max_pos_frac`.
- **Scaling Out/Partial Reduction**: Only possible **implicitly** via a passive fill on the opposite side. There is no "Reduce Position" action that uses Market orders.
- **Reversal**: Possible if an opposite fill is larger than the current position, but not via an explicit single "Reverse" action.

## 2. Trade Lifecycle Semantics

| Phase | Trigger Action | Order Type | Gating | Implementation Intent |
| :--- | :--- | :--- | :--- | :--- |
| **Open** | `POST_BID` (Flat) | Limit (Passive) | 0.3 bps Offset Gate + Imbalance Gate | **Intentional** (Maker focus) |
| **Increase** | `POST_BID` (Long) | Limit (Passive) | 0.3 bps Offset Gate + Imbalance Gate | **Intentional** (Scaling in) |
| **Reduce** | `POST_ASK` (Long) | Limit (Passive) | None (other than offset) | **Accidental/Implicit** |
| **Close** | `CLOSE_POSITION` | **Market** | **Hard Gate: uPnL < -0.3%** | **Intentional (Stop-Loss only)** |
| **Reverse**| Opposite Fill | Limit (Passive) | None | **Accidental/Implicit** |

> [!IMPORTANT]
> **The Reversal is "Blind"**: Because the agent sees only net position, it might "Reverse" without realizing it has crossed a zero-boundary, losing track of the individual trade economics.

## 3. Exit Logic: The "Profit Trap"
The audit reveals a critical imbalance in exit capability:
- **Profitable Exits**: 100% dependent on **Passive Fills** (`POST_ASK` while Long).
- **Loss Exits**: Can use **Market Orders** (`CLOSE_POSITION`) but only once the loss exceeds 0.3%.
- **The Gap**: If the agent is in +0.2% profit and wants to "lock it in" via a market order, **it cannot**. `CLOSE_POSITION` will be blocked by the `close_position_loss_threshold`. This forces the bot to hold winners until they either hit a passive exit or turn into a -0.3% loser.

## 4. Risk Framework (Current Phase 3 Config)
- **Max Leverage**: **20x Portfolio-wide** (Hardcoded in Rust).
- **Max Position Fraction**: **20% (0.2)** of total equity per env (Default in [grpc_env.py](file:///c:/Bot%20mk3/python/bot_ml/grpc_env.py) and usually overridden in script).
- **Inventory Cap**: Implicitly ~20% of balance.
- **Stop-Loss threshold**: **-0.3% Unrealized PnL** per trade cycle.
- **Reprice Sensitivity**: **0.5 bps** (Min change in price to allow a reprice).
- **Regime Gating**: 
    - **Trend**: 1.0x size.
    - **Shock**: 0.3x size (Adversarial defense).
    - **Dead**: 0.0x size (No trade).

## 5. Design Mismatch Assessment
The current architecture is **unintentionally optimized for Inventory Accumulation** and **Passive Quote Management**, while lacking a framework for **Agile Profit Taking**.

*   **Opening Bias**: The agent is very good at "stacking" inventory through passive BID fills.
*   **Closing Weakness**: The bot lacks a "Market Take Profit". It is forced to be a "Price Taker" for losses and a "Price Maker" for wins. In the toxic/noisy regimes of Phase 3, this creates a **Negative Skew** in the trade distribution.

## 6. Verdict & Phase 4 Recommendation

> [!CAUTION]
> **DO NOT proceed to Phase 4 (Microprice) without correcting the Exit Architecture.**

If we add Microprice Gating now, we will only make the "Opening" more selective, but we won't fix the fact that the bot is "trapped" in positions once they are open.

### Proposed Architecture Correction (Pre-Phase 4):
1.  **Remove the `CLOSE_POSITION` Gate for Profits**: Allow the agent to use `CLOSE_POSITION` (Market) if `unrealized_pnl > +0.1%` (or even `> 0`).
2.  **Define a "Target Profit" Action**: Instead of just `CLOSE_POSITION`, we should potentially have an action that aggressively moves the resting quote to the Mid (or Microprice) to prioritize exit.
3.  **Round-Trip Awareness**: The reward function should explicitly reward "Round Trip Completion" to discourage infinite inventory holding.

**Decision**: I recommend a "Phase 3.5: Exit Refactor" before starting Phase 4 Microprice implementation.
