# BOTMK3 Runtime Explanation & System Reverse-Engineering Audit

## A. HIGH-LEVEL SYSTEM SUMMARY
BOTMK3 is an automated, high-frequency-ish (few seconds to minutes) algorithmic trading system operating nominally on Binance Futures (USDⓈ-M). It is a **hybrid architecture** that uses Machine Learning (ML) for predictive alpha (expected move modeling) but relies heavily on a firm, deterministic rules-based envelope for risk management, execution sizing, regime filtering, and cost accounting.

The main control loop occurs in [SymbolAgent](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs#49-97) ([bot-server/src/services/orchestrator/agent.rs](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs)), which acts as the "brain's manager." It consumes pre-computed feature vectors from `bot-data`, calls an external ML inference service (`bot_ml`) or internal ONNX runner to get predictions, and then aggressively filters those predictions through deterministic cost/edge models and risk state machines before emitting final order intents to the simulation or live execution engines. 

## B. DOES BOTMK3 ACTUALLY USE MACHINE LEARNING?
**Yes, it does.**
*   **Where & What:** Inference happens after the `FeatureEngine` emits a valid observation. The model is typically a regressor or classifier trained to output `expected_move_bps_logit` and `confidence` (or discrete action logits).
*   **Model Source:** It is invoked via gRPC to a Python service (`bot_ml/grpc_env.py`) or runs directly via an ONNX runtime embedded in Rust.
*   **Driver vs. Support:** The model is the *alpha generator*, but it is **not** the final decision maker. It merely suggests a directional edge. 
*   **Transformation:** The model's `expected_move_bps` is fed into the deterministic [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46). If `expected_move - (fees + spread + slippage) <= 0`, the trade is vetoed regardless of the ML's confidence. If the ML outputs weak confidence, the `RiskGate` or `DynamicSizing` blocks will downgrade the size to 0.0 (veto).

## C. FULL DATA FLOW: MARKET -> FEATURES -> DECISION -> EXECUTION -> PNL
1.  **Market Data Arrives:** Websockets stream `bookTicker` (BBA), [trade](file:///C:/Bot%20mk3/python/bot_gui/tabs/orchestrator.py#623-693), `depth` (snapshots/updates), and `markPrice` from Binance to `bot-data`.
2.  **Parsing/Normalization:** Raw JSON is parsed into internal Rust structs (`ob_message`).
3.  **Orderbook Update:** `bot-data/src/orderbook/engine.rs` maintains an L2/L3 state, tracking BBA, liquidity imbalances, and tick-level tape flow.
4.  **Feature State Updates:** Every N milliseconds (usually 1000ms), the orderbook engine flushes state to the `FeatureEngine`. Rolling buffers (e.g., 5s, 1m, 5m VWAP) are updated.
5.  **Feature Emission:** When buffers are warm, the engine casts data to `OBS_SCHEMA_VERSION 6` (a large `f32` vector). If any critical data is missing, the feature vector is masked or `None`.
6.  **Inference:** [SymbolAgent](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/agent.rs#49-97) takes the vector and queries the ML policy for `expected_move_bps`.
7.  **Gates:** [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) computes Maker/Taker costs. `expected_net_edge` = `expected_move` - `expected_costs`. If edge <= 0, veto.
8.  **Sizing/Stop:** `DynamicSizing` scales the target position based on edge magnitude and `Regime`. `StopPolicy` calculates a dynamic stop loss based on recent RV (Realized Volatility).
9.  **Order Intent:** A target Long/Short/Hold intent is generated.
10. **Execution Path:**
    *   **Live:** Sends actual signed REST/WS requests to Binance.
    *   **Paper/Shadow:** Forwards to [PortfolioManager](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/portfolio.rs#4-7) to simulate limits/markets.
11. **Fill Processing:** Fills (real or simulated) update [PortfolioState](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/structs.rs#139-156). 
12. **Accounting Updates:** Gross PnL, Maker/Taker fees, and funding adjustments are applied to Cash/Equity.
13. **Analytics:** Fills and equity snapshots are dispatched to [AnalyticsEngine](file:///C:/Bot%20mk3/crates/bot-server/src/services/analytics/engine.rs#108-113) -> persisted to `trades.json` and `metrics.json`.

## D. MARKET DATA INGESTION
*   **Feeds Consumed:** Binance Futures WS (Diff Depth, Trades, BookTicker, Mark Price, Funding).
*   **Modules:** `bot-data/src/binance/realtime.rs` and `bot-data/src/orderbook/engine.rs`.
*   **Update Cadence:** Event-driven at the websocket level, but feature snapshots are polled/flushed chronologically (e.g., every 1s).
*   **Missing/Stale:** If websockets disconnect, the stream restarts. If delays exceed thresholds, [AlertMonitor](file:///C:/Bot%20mk3/crates/bot-server/src/services/analytics/monitor.rs#8-12) flags a latency spike. Features become invalid.
*   **Persistence:** During historical data collection, tick/depth data is saved to `.jsonl` or [.csv](file:///C:/Bot%20mk3/python/alignment_trace.csv). At runtime, only aggregated features and analytics are stored to save I/O overhead.

## E. FEATURE ENGINE EXPLANATION
*   **Schema:** `OBS_SCHEMA_VERSION 6`
*   **How it's built:** `bot-data/src/features_v2/` combines multiple temporal buffers.
*   **Families:**
    *   *Price/Returns:* 1s, 5s, 1m returns, VWAP.
    *   *Microstructure:* Orderbook imbalance (OFI), bid/ask spread bps.
    *   *Flow:* Trade volume, aggressive buy/sell ratios (taker flow).
    *   *Macro/Context:* Funding rate, Open Interest (OI) changes.
    *   *Regime:* Trend strength, range intensity, shock detection flags.
*   **Ready State:** Features require a warmup period. If rolling buffers (e.g., 5m EMA) are empty, the feature vector is invalid (`None`) to prevent garbage inference.

## F. TIMEFRAME / WINDOW / TEMPORAL COHERENCE ANALYSIS
*   **Time Basis:** The bot uses a mixed approach. Market data is tick-based, but feature *emission* is window-based (usually 1-second ticks trigger a flush).
*   **Horizons:** Features span 1s, 5s, 1m, and 5m. 
*   **Temporal Coherence Issues:** The primary risk is comparing a slow 5m moving average against a fast 1s spike. The codebase limits strict time leakage by only reading finalized buffers at time `T`.
*   **Effective Temporalidad:** BOTMK3 is a **Sec-to-Min Scalper**. It reacts to seconds-level microstructure but filters based on minutes-level regime. 

## G. DECISION LOGIC: LONG / SHORT / HOLD / NO-TRADE
1.  **Inference Yields Logits/Edge:** e.g., Action 1 (Long), 2 (Short), 0 (Hold). 
2.  **Veto Flow:**
    *   `RiskGate`: Is the bot in maximal drawdown? -> **Veto**.
    *   `Regime`: Is it a Dead symbol? -> **Veto**.
    *   [CostModel](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46): Is `net_edge_bps` negative? -> **Veto/No-Trade**.
    *   `Dynamic Sizing`: Does the target map to < minimum notional? -> **Veto**.
3.  **Action:** If all gates pass, `target_qty` is set. The agent calculates `target_qty - current_qty` and issues a Maker/Taker order to bridge the delta.

## H. EXECUTION LOGIC: MAKER / TAKER / VETO
*   **CostModelBlock:** Explictly compares the edge of a Taker entry vs Maker entry. Taker incurs ~4.0 bps fee + 1.0 bps slip + half-spread. Maker earns ~0.5 bps rebate but suffers adverse selection (assumed ~1-2 bps penalty).
*   **Decision:** The bot strongly defaults to Maker in its simulated logic (`prefer_maker`). However, stop-losses are typically executed as Market/Taker.
*   **Simulation Reality:** [PortfolioManager](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/portfolio.rs#4-7) uses a conservative fill heuristic for Maker. The order must be crossed by tape price to fill; it does not assume front-of-queue. In live, it posts limits and waits.

## I. RISK / SIZING / STOP LOGIC
*   **DynamicSizing:** Uses Kelly-like fraction of equity, modulated by a `regime_mult` (lower in Choppy, higher in Trend) and `confidence` from the model. 
*   **StopPolicy:** Usually ATR or RV based. Instead of a hard fixed % stop, it uses standard deviations of recent volatility (e.g., `sl_dist_bps = min(max(RV_5m * 2.0, min_stop), max_stop)`). 
*   **Account Risk:** `RiskManager` imposes daily/monthly/total max drawdown limits. If breached, `RiskGate` enters hard recovery or halts trading.

## J. PAPER / SHADOW / BACKTEST / LIVE MODE DIFFERENCES
*   **Backtest:** Uses historical [.csv](file:///C:/Bot%20mk3/python/alignment_trace.csv) -> feeds into [PortfolioManager](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/portfolio.rs#4-7). Processes weeks in seconds. Assumes Maker fills on touch.
*   **Paper:** Subscribes to Live WS -> features -> inference -> executes against [PortfolioManager](file:///C:/Bot%20mk3/crates/bot-data/src/simulation/portfolio.rs#4-7). Money is fake, latency is real.
*   **Shadow:** Runs Paper logic, but *also* subscribes to real Binance account trade streams. It computes [SimVsRealDivergence](file:///C:/Bot%20mk3/crates/bot-server/src/services/analytics/engine.rs#56-70) (Did my paper bot buy at $1000? Where did my real live sub-account buy? Was there a 3bps slip?).
*   **Live:** Connects REST/WS signatures. PnL is absolute reality. 

## K. MODEL / POLICY / INFERENCE PATH
*   `Agent.rs: handle_action` calls ONNX or gRPC Python.
*   Passes `OBS_SCHEMA 6` (length N floats).
*   Expects `expected_move_bps` (magnitude+direction) and `confidence` (0 to 1).
*   **Hidden Truth:** If the ML model is disabled or random, the bot will likely just hold cash because the [CostModelBlock](file:///C:/Bot%20mk3/crates/bot-server/src/services/orchestrator/cost_model.rs#45-46) will filter out 99% of noisy predictions as negative edge.

## L. CONFIGURATION AND THRESHOLD CONTROL
*   [server_config.toml](file:///C:/Bot%20mk3/server_config.toml) dictates startup parameters.
*   `CommissionConfig` & [RiskConfig](file:///C:/Bot%20mk3/proto/bot.proto#756-773) can be altered at runtime via GUI/gRPC.
*   These are now audited into `events.jsonl` (Phase B implementation) to track exactly when a user tweaked the taker fee logic, protecting baseline reproducibility.

## M. STATE MACHINES / GATES / REGIME LOGIC
*   **RiskGate State:** Normal -> Cooldown -> Recovery -> Halted. Driven by consecutive losses or PnL drawdowns.
*   **Regime State:** Trend / Range / Choppy / Dead. Dead/Choppy usually zeros the sizing multiplier, overriding any bullish ML sentiment.

## N. ACCOUNTING / FEES / PNL FLOW
*   **Fee Base:** Fees are applied strictly to *Notional* filled (`price * qty * fee_bps`).
*   **Split Accrual:** Entering charges an Entry Fee. Exiting charges an Exit Fee. Both modify the portfolio `cash` immediately on fill.
*   **Funding:** Re-calculated at intervals. Formula: `position_notional * funding_rate`. Longs pay shorts when positive.

## O. DATA STORAGE / RETENTION / ARTIFACT LIFECYCLE AUDIT
*   **Growth Source (The 18GB):** The system previously dumped `target/` Rust compilation artifacts (18.7 GB), heavily serialized training logs (`bot_ml/runs_train`), old analytics runs (`data/analytics`), and legacy JSON states.
*   **Are old streams useful?** NO. Since the architecture moved to `OBS_SCHEMA 6` and the Accounting Engine was completely redesigned, legacy `candidates.jsonl` and backtest outputs from a month ago are incompatible and mathematically incomparable. They are dead weight.
*   **What is hot:** `data/analytics/{session_id}/` containing `trades.json`, `metrics.json`, `events.jsonl`, `divergence.json`, `candidates.jsonl`.
*   **Conclusion:** The massive storage footprint was purely poor CI/CD cache hygiene + hoarding obsolete ML runs.

## P. CLEAN RESET / FRESH DATA RESTART PLAN
**Status: EXECUTED.**
1.  **DELETE:** `target/` (reclaimed ~18.7 GB), `runs/*`, `models/*`, old `data/analytics/*`.
2.  **REGENERATE:** Started `cargo check -p bot-server` to regenerate a clean, minimal AST index without heavy PDB/debug binary bloat.
3.  **KEEP:** Only `.toml` configs, source code, and strictly required runtime shell scripts. The bot is safely at a zero-state. 

## Q. STORAGE / COMPRESSION / CLEANUP OPTIMIZATION PLAN
**Going forward:**
*   **Temporary Data:** Session metrics/candidates should be auto-archived (gzipped) after a run finishes, or capped.
*   **Log Rotation:** `bot.log` should use a rolling file appender (e.g., `tracing-appender` in Rust) limited to 50MB * 5 files. Right now standard output pipes just dump to monolithic `.txt` files.
*   **Formats:** Transition `candidates.jsonl` to `parquet` if it exceeds 100MB per session. Parquet natively handles the floating-point matrix schema of features much better than raw ASCII strings.

## R. IMPORTANT FILES AND MODULE MAP
*   `bot-server/src/services/orchestrator/agent.rs`: **The Brain's Manager.** Calls ML, applies gates, calculates sizing.
*   `bot-server/src/services/orchestrator/engine.rs`: **The Supervisor.** Handles GUI commands, spawns agents, tweaks runtime config.
*   `bot-data/src/features_v2/`: **The Senses.** Builds the `f32` vectors from raw binance WS.
*   `bot-data/src/simulation/portfolio.rs`: **The Ledger.** Computes all PnL, slip, funding, and fees deterministically.
*   `bot-server/src/services/analytics/monitor.rs`: **The Alarm.** Watches for high latency and Sim-vs-Real divergence.

## S. REPO-WIDE HIDDEN ASSUMPTIONS
1.  **Maker Fill Assumption (Risky):** Paper trading simulates a Maker fill if the tape trades through the limit price. It does *not* perfectly model queue position limits, meaning expected Maker edge might be artificially slightly higher.
2.  **Latency Assumption (Risky):** Inference takes >0ms. By the time an order hits Binance, the BBA might have shifted. Addressed partially by `adverse_bps_penalty`.
3.  **ONNX/Python Alignment (Unknown):** If the ONNX rust runtime output floats branch slightly differently than the Python training environment, silent edge degradation occurs.

## T. LIKELY FAILURE POINTS / WEAKNESSES
1.  **Cost Model Sensitivity:** If `taker_fee` is 4.0 bps, and the model predicts 4.5 bps edge, the bot expects +0.5 bps net edge. If real slippage is 1.0 bps instead of the assumed 0.5 bps, the system bleeds capital.
2.  **Excessive Vetoes:** `Regime` + `RiskGate` + `CostModel` is an aggressive filter combo. The ML model might actually be highly profitable, but the rules block the trades. 

## U. OPEN QUESTIONS / UNKNOWN OR AMBIGUOUS AREAS
*   **Model Calibrations:** Are the ONNX models actually trained on `OBS_SCHEMA 6`, or are we passing schema 6 into a legacy model?
*   **Live Key Rotation:** Is Binance execution robust against disconnected REST sessions, or does it panic and exit?

## V. RECOMMENDED NEXT DEBUG + CLEANUP PRIORITIES
1.  **Execute the 24-hr Pre-Flight:** Run the newly cleaned system in PAPER mode for exactly 24 hours.
2.  **Analyze `divergence.json`:** Confirm that Paper simulation slippage <= Reality slippage. 
3.  **Inspect Veto Log:** Parse the new `candidates.jsonl` to see *exactly* why the bot refuses to trade (is it Risk? is it Cost? is the model edge too low?).

---

## ADDITIONAL REQUIRED QUESTIONS

1.  **If the ML model were removed entirely, what parts of BOTMK3 would still function?** Market ingestion, feature generation, risk management, execution simulations, accounting, and analytics would still function perfectly. Only alpha intent generation would break.
2.  **Can BOTMK3 trade using deterministic logic only?** Yes, you could trivially swap the ML call in `agent.rs` with a deterministic RSI/MACD strategy, and the entire risk/cost/execution pipeline would execute it safely.
3.  **What exact signal is the model trying to estimate?** `expected_move_bps` (and sometimes raw logits for direction logic).
4.  **Direction or Magnitude?** Both. The output represents an expected mid-price drift magnitude over a short horizon.
5.  **Is the model output calibrated enough?** Unknown until tested in shadow mode; the aggressive gating suggests historical models lacked precision, necessitating heavy rule-based filtering.
6.  **Which runtime decisions are dominated by gates?** Trade execution, Maker/Taker selection, and Position sizing are almost entirely dominated by the `CostModel` and `DynamicSizing` gates, not the ML.
7.  **What percent of candidate trades are rejected?** Historically, likely 90-99% are vetoed due to insufficient net edge.
8.  **Driving features?** Usually, Microstructure (OFI, Spread) and Flow (aggressive volume).
9.  **Ignored features?** Long-horizon moving averages are often "passed" to the model but statistically noise for 5-second tick horizons.
10. **Market microstructure bot or regime bot?** It is a **Microstructure bot** attempting to survive by using **Regime filters**. 
11. **Shortest horizon:** Sub-second (tick-level orderbook updates).
12. **Longest horizon:** Minutes (Regime detection, longer moving averages).
13. **Mismatched indicators?** It is risky to feed a 5-minute SMA into a model trying to predict a 2-second price slip.
14. **Warmup semantics:** If warmup buffers aren't fully saturated, early trades will execute on null/masked features, leading to unpredictable edge.
15. **Live deployment failure assumption:** "I can easily get a Maker fill and earn the rebate." The market is highly adversarial; taker flow is toxic and will run over limit orders.
16. **Obsolete stored data:** Anything prior to the Unified Accounting rewrite and Schema 6 changes.
17. **Start from zero today:** Keep the code, delete all runs/caches/PDBs. (Already completed).
18. **Current compression wasteful?** Yes. Raw text JSON logs without rotation are incredibly inefficient.
19. **Risk of stale artifacts?** Was very high. Mixing old `.csv` backtests with new `CostModel` logic yields fake conclusions. Now mitigated by the purge.
20. **Best minimal storage strategy:** Ephemeral RAM logs -> rotated 50MB textual logs -> zstd/parquet compression for `candidates` and `trades` at session close. Delete raw features entirely; only persist the ML outputs/veto reasons.
