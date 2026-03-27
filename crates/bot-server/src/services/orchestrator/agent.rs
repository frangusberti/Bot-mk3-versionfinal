use crate::services::analytics::engine::AnalyticsEvent;
use crate::services::orchestrator::commission::{
    decide_order_type, CommissionPolicy, CommissionStats, OrderDecision, OrderIntent, UrgencyLevel,
};
use crate::services::orchestrator::execution_quality::{
    ExecutionQualityBlock, ExecutionQualityConfig,
};
use bot_data::orderbook::engine::OrderBookStatus;
use crate::services::orchestrator::cost_model::{CostModelBlock, CostModelConfig, EntryMode};
use crate::services::orchestrator::sizing::{DynamicSizingBlock, DynamicSizingConfig};
use crate::services::orchestrator::stop_policy::{StopPolicyBlock, StopPolicyConfig};
use crate::services::orchestrator::experience::ExperienceCommand;
use crate::services::orchestrator::leverage::LeverageManager;
use crate::services::orchestrator::policy::PythonPolicyAdapter;
use crate::services::orchestrator::risk::{
    AccountSnapshot, PositionSnapshot, ProposedOrder, RiskManager, RiskState,
};
use crate::services::orchestrator::gate::RiskGate;
use bot_core::proto::{OrchestratorEvent, SymbolConfig, SymbolStatus};
use bot_data::execution::{ExecutionInterface, PositionInfo};
use bot_data::experience::reward::RewardState;
use bot_data::features_v2::compute_account::AccountState;
use bot_data::features_v2::schema::FeatureRow;
use bot_data::features_v2::FeatureEngineV2;
use bot_data::normalization::schema::NormalizedMarketEvent;
use bot_data::simulation::structs::{OrderType, Side};
use log::{error, info, warn};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use tokio::sync::mpsc;

pub enum AgentEvent {
    MarketData(Box<NormalizedMarketEvent>),
    Fill(bot_data::reporting::backtest::ExecutionRecord),
    RealFillForDivergence(bot_data::reporting::backtest::ExecutionRecord),
    Command(AgentCommand),
}

const LEVERAGE_APPLY_MIN_DELTA: f64 = 0.25;
const LEVERAGE_APPLY_COOLDOWN_MS: i64 = 30_000;

pub enum AgentCommand {
    Stop,
    #[allow(dead_code)]
    UpdateConfig(SymbolConfig),
    ReloadPolicy(String, tokio::sync::oneshot::Sender<Result<(), String>>),
}

pub struct SymbolAgent {
    config: SymbolConfig,
    rx: mpsc::Receiver<AgentEvent>,
    event_tx: mpsc::Sender<OrchestratorEvent>,

    execution: Box<dyn ExecutionInterface>,
    policy: PythonPolicyAdapter,
    feature_engine: FeatureEngineV2,
    risk_manager: Arc<Mutex<RiskManager>>,
    leverage_manager: Arc<Mutex<LeverageManager>>,
    analytics_tx: Option<mpsc::Sender<AnalyticsEvent>>,
    experience_tx: Option<mpsc::Sender<ExperienceCommand>>,
    experience_builder:
        Option<crate::services::orchestrator::experience::builder::ExperienceBuilder>,
    status: Arc<Mutex<SymbolStatus>>,
    commission_policy: Arc<Mutex<CommissionPolicy>>,
    commission_stats: Arc<Mutex<CommissionStats>>,

    // OrderBook
    orderbook: bot_data::orderbook::engine::OrderBook,
    risk_gate: RiskGate,

    // State
    last_decision_ts: i64,
    cash_balance: f64,
    last_sync_ts: Instant,
    last_features: Option<FeatureRow>,

    // Throttling
    last_analytics_ts: i64,

    // Watchdog
    first_desynced_ts: Option<Instant>,
    desync_watchdog_triggered: bool,
    watchdog_halted: bool,

    // RoundTrip Tracking
    current_trip_start_ts: Option<i64>,
    cumulative_trip_fees: f64,
    last_leverage_apply_ts: i64,
    last_applied_leverage: Option<f64>,
    
    // Parity Logger Handshake
    parity_tx: Option<Arc<bot_data::reporting::parity::LiveCaptureWriter>>,
    step_seq: u64,
    last_telemetry_ts: i64,
    run_id: String,
}

impl SymbolAgent {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        config: SymbolConfig,
        rx: mpsc::Receiver<AgentEvent>,
        event_tx: mpsc::Sender<OrchestratorEvent>,
        execution: Box<dyn ExecutionInterface>,
        policy: PythonPolicyAdapter,
        feature_engine: FeatureEngineV2,
        risk_manager: Arc<Mutex<RiskManager>>,
        leverage_manager: Arc<Mutex<LeverageManager>>,
        analytics_tx: Option<mpsc::Sender<AnalyticsEvent>>,
        experience_tx: Option<mpsc::Sender<ExperienceCommand>>,
        status: Arc<Mutex<SymbolStatus>>,
        commission_policy: Arc<Mutex<CommissionPolicy>>,
        commission_stats: Arc<Mutex<CommissionStats>>,
        parity_tx: Option<Arc<bot_data::reporting::parity::LiveCaptureWriter>>,
        run_id: String,
    ) -> Self {
        let initial_equity = 10000.0; // Default or fetch?
        Self {
            orderbook: bot_data::orderbook::engine::OrderBook::new(config.symbol.clone()),
            config,
            rx,
            event_tx,
            execution,
            policy,
            feature_engine,
            risk_manager,
            leverage_manager,
            analytics_tx,
            experience_tx,
            experience_builder: Some(
                crate::services::orchestrator::experience::builder::ExperienceBuilder::new(
                    initial_equity,
                ),
            ),
            status,
            commission_policy,
            commission_stats,
            last_decision_ts: 0,
            cash_balance: initial_equity,
            last_sync_ts: Instant::now(),
            last_features: None,
            last_analytics_ts: 0,
            first_desynced_ts: None,
            desync_watchdog_triggered: false,
            watchdog_halted: false,
            current_trip_start_ts: None,
            cumulative_trip_fees: 0.0,
            last_leverage_apply_ts: 0,
            last_applied_leverage: None,
            parity_tx,
            step_seq: 0,
            last_telemetry_ts: 0,
            run_id,
            risk_gate: RiskGate::new(),
        }
    }

    pub async fn run(mut self) {
        info!("Agent {} started.", self.config.symbol);

        // Initial equity fetch for RewardState
        if let Ok(e) = self.execution.get_equity().await {
            self.cash_balance = e; // Initial assumption: cash = equity if flat
            if let Some(eb) = &mut self.experience_builder {
                eb.reward_state = RewardState::new(e);
                eb.prev_equity = e;
            }
        }

        // Always set leverage on startup so the sim engine knows
        // the correct margin requirements (otherwise defaults to 1x).
        {
            let init_lev = self
                .leverage_manager
                .lock()
                .unwrap()
                .get_effective_leverage(&self.config.symbol);
            if let Err(e) = self
                .execution
                .set_leverage(&self.config.symbol, init_lev)
                .await
            {
                warn!(
                    "Agent {}: initial set_leverage({}) failed: {}",
                    self.config.symbol, init_lev, e
                );
            } else {
                info!(
                    "Agent {}: leverage initialized to {}x",
                    self.config.symbol, init_lev
                );
            }
        }

        while let Some(event) = self.rx.recv().await {
            match event {
                AgentEvent::MarketData(md) => {
                    self.on_market_data(*md).await;
                }
                AgentEvent::Fill(record) => {
                    self.on_fill(record).await;
                }
                AgentEvent::RealFillForDivergence(record) => {
                    self.on_real_fill_for_divergence(record).await;
                }
                AgentEvent::Command(cmd) => {
                    match cmd {
                        AgentCommand::Stop => {
                            info!("Agent {} received Stop command", self.config.symbol);
                            return;
                        }
                        AgentCommand::UpdateConfig(cfg) => {
                            info!(
                                "Agent {} config updated. Resetting watchdog.",
                                self.config.symbol
                            );
                            self.config = cfg;
                            self.first_desynced_ts = None;
                            self.desync_watchdog_triggered = false;
                            self.watchdog_halted = false;
                        }
                        AgentCommand::ReloadPolicy(path, reply) => {
                            info!(
                                "Agent {} reloading policy from {}",
                                self.config.symbol, path
                            );

                            // Enforce Feature Profile Consistency
                            if let Err(e) = self.validate_model_profile(&path).await {
                                let _ = reply.send(Err(e));
                                continue;
                            }

                            let res = self.policy.reload(path).await;
                            let _ = reply.send(res);
                        }
                    }
                }
            }
        }
        info!("Agent {} loop ended.", self.config.symbol);
    }

    async fn on_market_data(&mut self, md: NormalizedMarketEvent) {
        let ts = md.time_canonical;
        let exchange_ts = md.time_exchange;
        let recv_ts = md.recv_time.unwrap_or(0);

        // 1. Maintain OrderBook
        if md.event_type == "depthUpdate" {
            // Deserialize payload
            if let Ok(delta) = serde_json::from_str::<bot_core::schema::BookDelta>(&md.payload_json)
            {
                // Map to OrderBook format
                let bids: Vec<(rust_decimal::Decimal, rust_decimal::Decimal)> =
                    delta.bids.iter().map(|l| (l.price, l.quantity)).collect();
                let asks: Vec<(rust_decimal::Decimal, rust_decimal::Decimal)> =
                    delta.asks.iter().map(|l| (l.price, l.quantity)).collect();

                let first = md.update_id_first.unwrap_or(delta.first_update_id as i64);
                let final_ = md.update_id_final.unwrap_or(delta.final_update_id as i64);
                let prev = md.update_id_prev.unwrap_or(0);

                self.orderbook.apply_delta(first, final_, prev, bids, asks);

                // Pipe top-N levels to FeatureEngineV2 for OBI/microprice
                let top_bids = self.orderbook.top_bids(3);
                let top_asks = self.orderbook.top_asks(3);
                self.feature_engine.set_orderbook_levels(top_bids, top_asks);

                // Check Resync Trigger
                if self.orderbook.status == bot_data::orderbook::engine::OrderBookStatus::Desynced
                    || self.orderbook.status
                        == bot_data::orderbook::engine::OrderBookStatus::GapDetected
                    || self.orderbook.status
                        == bot_data::orderbook::engine::OrderBookStatus::WaitingForSnapshot
                {
                    if let Err(e) = self.orderbook.resync().await {
                        error!("OrderBook Resync Failed: {}", e);
                    }
                }
            }
        }

        // 2. Safety Timeout Check
        if self.orderbook.status == bot_data::orderbook::engine::OrderBookStatus::InSync {
            self.last_sync_ts = Instant::now();
        } else {
            let elapsed = self.last_sync_ts.elapsed();
            if elapsed.as_secs() > 300 {
                // 5 minutes
                error!(
                    "CRITICAL: OrderBook for {} has been DESYNCED for {:?}! Validation Warning.",
                    self.config.symbol, elapsed
                );
            }
        }

        // Sync OB status + update FeatureEngineV2
        let ob_in_sync =
            self.orderbook.status == bot_data::orderbook::engine::OrderBookStatus::InSync;
        self.feature_engine.set_orderbook_in_sync(ob_in_sync);
        self.feature_engine.update(&md);

        // 4. Update Risk Manager State
        // Force refresh if we haven't refreshed in 2s OR if we just took an action
        let needs_refresh = ts - self.last_decision_ts > 1500; 

        let (equity, pos_qty, pos_side, entry_price, realized_fees, realized_pnl, pos_info): (
            f64,
            f64,
            String,
            f64,
            f64,
            f64,
            PositionInfo,
        ) = if needs_refresh {
            let equity = self.execution.get_equity().await.unwrap_or(0.0);
            let pos_info = self
                .execution
                .get_position(&self.config.symbol)
                .await
                .unwrap_or_else(|_| PositionInfo {
                    symbol: self.config.symbol.clone(),
                    side: "Flat".to_string(),
                    qty: 0.0,
                    entry_price: 0.0,
                    unrealized_pnl: 0.0,
                    realized_fees: 0.0,
                    realized_funding: 0.0,
                    realized_pnl: 0.0,
                    margin_used: 0.0,
                    notional_value: 0.0,
                });
            self.cash_balance = equity - pos_info.unrealized_pnl;
            (
                equity,
                pos_info.qty,
                pos_info.side.clone(),
                pos_info.entry_price,
                pos_info.realized_fees,
                pos_info.realized_pnl,
                pos_info,
            )
        } else {
            let (p_qty, p_side, p_entry, p_fees, p_pnl, p_funding, p_margin) =
                if let Some(eb) = &self.experience_builder {
                    (
                        eb.prev_pos_qty,
                        eb.prev_pos_side.clone(),
                        eb.prev_entry_price,
                        eb.prev_realized_fees,
                        eb.prev_realized_pnl,
                        eb.prev_realized_funding,
                        0.0,
                    )
                } else {
                    (0.0, "Flat".to_string(), 0.0, 0.0, 0.0, 0.0, 0.0)
                };

            let cur_mid = self.feature_engine.current_mid_price().unwrap_or(0.0);
            let upnl = (cur_mid - p_entry)
                * p_qty
                * (if p_side == "Buy" {
                    1.0
                } else if p_side == "Sell" {
                    -1.0
                } else {
                    0.0
                });
            let e = self.cash_balance + upnl;

            let p_info = PositionInfo {
                symbol: self.config.symbol.clone(),
                side: p_side.clone(),
                qty: p_qty,
                entry_price: p_entry,
                unrealized_pnl: upnl,
                realized_fees: p_fees,
                realized_funding: p_funding,
                realized_pnl: p_pnl,
                margin_used: p_margin,
                notional_value: p_qty.abs() * cur_mid,
            };
            (e, p_qty, p_side, p_entry, p_fees, p_pnl, p_info)
        };

        let current_mid = self.feature_engine.current_mid_price().unwrap_or(0.0);

        let unrealized_pnl = if pos_qty.abs() > 0.0 {
            let diff = current_mid - entry_price;
            if pos_side == "Buy" {
                diff * pos_qty
            } else {
                -diff * pos_qty
            }
        } else {
            0.0
        };

        // Estimated Liquidation Price
        let liq_price = if pos_qty.abs() > 0.0 {
            let lev = self
                .leverage_manager
                .lock()
                .unwrap()
                .get_effective_leverage(&self.config.symbol);
            let mm = 0.005; // 0.5% Maint. Margin
            if pos_side == "Buy" {
                entry_price * (1.0 - 1.0 / lev + mm)
            } else {
                entry_price * (1.0 + 1.0 / lev - mm)
            }
        } else {
            0.0
        };

        // Apply leverage to Live if enabled and changed
        let target_lev = if pos_qty.abs() > 0.0 {
            self.leverage_manager
                .lock()
                .unwrap()
                .get_effective_leverage(&self.config.symbol)
        } else {
            5.0
        };

        if self.config.live_apply_enabled {
            let should_apply = Self::should_apply_leverage(
                self.last_applied_leverage,
                target_lev,
                ts,
                self.last_leverage_apply_ts,
            );

            if should_apply {
                let res = self
                    .execution
                    .set_leverage(&self.config.symbol, target_lev)
                    .await;
                let mut lm = self.leverage_manager.lock().unwrap();
                match res {
                    Ok(_) => {
                        self.last_applied_leverage = Some(target_lev);
                        self.last_leverage_apply_ts = ts;
                        lm.set_apply_status(&self.config.symbol, "APPLIED_OK", "")
                    }
                    Err(e) => lm.set_apply_status(&self.config.symbol, "APPLIED_FAIL", &e),
                }
            }
        }

        {
            let mut s = self.status.lock().unwrap();
            s.symbol = self.config.symbol.clone();
            s.position_side = pos_side.clone();
            s.position_qty = pos_qty;
            s.entry_price = entry_price;
            s.unrealized_pnl = unrealized_pnl;
            s.mid_price = current_mid;
            s.liquidation_price = liq_price;
            s.realized_fees = realized_fees;
            s.realized_pnl = realized_pnl;
            s.notional_value = pos_qty * current_mid;
            s.funding_pnl = pos_info.realized_funding;
            s.entry_fees = realized_fees;
            s.exit_fees = 0.0;
            s.last_decision_ts = self.last_decision_ts;
            s.status = format!("{:?}", self.orderbook.status);
            s.ob_consecutive_failures = self.orderbook.consecutive_failures;
            s.ob_next_resync_delay_ms = self.orderbook.calculate_backoff_delay().as_millis() as u32;
            s.ob_state = format!("{:?}", self.orderbook.status);

            let report = self.feature_engine.get_health_report(ts);
            s.health_state = report.health_state.clone();
            s.obs_quality = report.obs_quality;

            let lev_mgr = self.leverage_manager.lock().unwrap();
            let lev = lev_mgr.get_effective_leverage(&self.config.symbol);
            s.effective_leverage = lev;
            s.last_risk_score = lev_mgr
                .get_state(&self.config.symbol)
                .map(|st| st.last_risk_score)
                .unwrap_or(0.0);
            s.equity_alloc_used = if lev > 0.0 {
                (pos_qty.abs() * current_mid) / lev
            } else {
                pos_qty.abs() * current_mid
            };

            // Leverage Apply Stats
            if let Some(st) = lev_mgr.get_state(&self.config.symbol) {
                s.leverage_apply_state = st.apply_state.clone();
                s.leverage_apply_error = st.apply_error.clone();
            }
        }

        // Adaptive Risk Info - Separate scope to avoid holding MutexGuard across await
        {
            let mut s = self.status.lock().unwrap();
            let risk = self.risk_manager.lock().unwrap();
            let trades = &risk.rolling_trades;
            let wins = trades.iter().filter(|&&p| p > 0.0).count();
            let wr = if trades.is_empty() {
                0.0
            } else {
                wins as f64 / trades.len() as f64
            };
            let pnl = trades.iter().sum::<f64>();
            s.adaptive_risk_active = false;
            s.rolling_winrate = wr;
            s.rolling_pnl = pnl;
        }

        if ts - self.last_analytics_ts >= 1000 {
            if let Some(tx) = &self.analytics_tx {
                let snap = crate::services::analytics::engine::PortfolioSnapshot {
                    ts,
                    equity,
                    cash: self.cash_balance,
                    unrealized_pnl: equity - self.cash_balance,
                    margin_used: if pos_qty.abs() > 0.0 {
                        let lev = self
                            .leverage_manager
                            .lock()
                            .unwrap()
                            .get_effective_leverage(&self.config.symbol);
                        let safe_lev = if lev > 0.0 { lev } else { 1.0 };
                        (pos_qty.abs() * current_mid) / safe_lev
                    } else {
                        0.0
                    },
                    total_fees_entry: realized_fees,
                    total_fees_exit: 0.0,
                    funding_pnl: pos_info.realized_funding,
                    positions: std::collections::HashMap::new(),
                };
                let _ = tx.send(AnalyticsEvent::PortfolioState(snap)).await;
            }
            self.last_analytics_ts = ts;
        }

        let exposure = pos_qty.abs() * current_mid;

        // Inject account state into FeatureEngineV2 for Group G features
        {
            let pos_flag = if pos_side == "Buy" {
                1.0
            } else if pos_side == "Sell" {
                -1.0
            } else {
                0.0
            };
            let latent_pnl_pct = if equity > 0.0 {
                unrealized_pnl / equity * 100.0
            } else {
                0.0
            };
            let dd_pct = {
                let risk = self.risk_manager.lock().unwrap();
                if risk.equity_peak_total > 0.0 {
                    (risk.equity_peak_total - risk.current_equity) / risk.equity_peak_total * 100.0
                } else {
                    0.0
                }
            };
            self.feature_engine.set_account_state(AccountState {
                position_flag: pos_flag,
                latent_pnl_pct,
                max_pnl_pct: 0.0, // TODO: track max pnl in current position
                current_drawdown_pct: dd_pct,
            });
        }

        let (kill_switch, rollback) = {
            let mut risk = self.risk_manager.lock().unwrap();
            let vol = 0.0; // Volatility now tracked inside FeatureEngineV2
            risk.update_state(&self.config.symbol, equity, exposure, vol);
            let kill = risk.state.is_disabled() || risk.state == RiskState::Killed;
            let rb = risk.consecutive_losses >= 10; // Simple rollback heuristic
            (kill, rb)
        };

        if rollback {
            warn!("Rollback Condition Met! Triggering Policy Reload...");
            if let Err(e) = self.policy.reload(self.config.policy_id.clone()).await {
                error!("Failed to Rollback Policy: {}", e);
            } else {
                info!("Policy Rolled Back successfully.");
                let mut risk = self.risk_manager.lock().unwrap();
                risk.consecutive_losses = 0;
            }
        }

        if kill_switch {
            warn!("Kill Switch Active! Closing {}...", self.config.symbol);
            if pos_qty.abs() > 0.0 {
                let close_side = if pos_side == "Buy" { "Sell" } else { "Buy" };
                match self
                    .execution
                    .submit_order(
                        &self.config.symbol,
                        close_side,
                        pos_qty.abs(),
                        current_mid,
                        "MARKET",
                    )
                    .await
                {
                    Ok(_) => info!("Kill Switch Close Sent"),
                    Err(e) => error!("Kill Switch Close Failed: {}", e),
                }
            }
            return;
        }

        // WATCHDOG HALT CHECK
        if self.watchdog_halted {
            return;
        }

        // TRADING GUARD: Skip inference if OrderBook is not synced
        let is_sync = self.orderbook.status == bot_data::orderbook::engine::OrderBookStatus::InSync;

        if !is_sync {
            // Rate limit warning
            if self.last_sync_ts.elapsed().as_secs().is_multiple_of(5) {
                warn!(
                    "TRADING GUARD: OrderBook NOT SYNCED. Status: {:?}",
                    self.orderbook.status
                );
            }

            // Watchdog Tracking
            if self.first_desynced_ts.is_none() {
                self.first_desynced_ts = Some(Instant::now());
            }

            if let Some(t0) = self.first_desynced_ts {
                let timeout_sec = self
                    .config
                    .watchdog
                    .as_ref()
                    .map(|w| w.timeout_seconds)
                    .unwrap_or(60) as u64;
                let enabled = self
                    .config
                    .watchdog
                    .as_ref()
                    .map(|w| w.enabled)
                    .unwrap_or(true);

                if enabled
                    && t0.elapsed().as_secs() > timeout_sec
                    && !self.desync_watchdog_triggered
                {
                    self.desync_watchdog_triggered = true;
                    self.watchdog_halted = true;

                    error!(
                        r#"{{"event": "orderbook_desync_timeout", "symbol": "{}", "duration_sec": {}}}"#,
                        self.config.symbol,
                        t0.elapsed().as_secs()
                    );

                    warn!(
                        "WATCHDOG: Symbol {} halted due to prolonged desync (> {}s)",
                        self.config.symbol, timeout_sec
                    );

                    // Notify Orchestrator via Event
                    let event = OrchestratorEvent {
                        ts: chrono::Utc::now().timestamp_millis(),
                        level: "ERROR".to_string(),
                        r#type: "HEALTH".to_string(),
                        symbol: self.config.symbol.clone(),
                        message: format!("WATCHDOG HALT: {} desynced too long", self.config.symbol),
                        payload_json: format!(
                            r#"{{"timeout_sec": {}, "actual_sec": {}}}"#,
                            timeout_sec,
                            t0.elapsed().as_secs()
                        ),
                        obs: vec![],
                        metrics: std::collections::HashMap::new(),
                    };
                    let _ = self.event_tx.send(event).await;
                }
            }
            return;
        } else {
            // Reset Watchdog
            self.first_desynced_ts = None;
            self.desync_watchdog_triggered = false;
        }

        self.report_telemetry(ts).await;

        let emit_result: Option<FeatureRow> = self.feature_engine.maybe_emit(ts);

        // Debug Log if NOT emitting (sampled)
        if emit_result.is_none() {
            // Simple modulo check on timestamp to log ~every 5 seconds
            if ts % 5000 < 100 {
                info!("Feature Engine: Buffering data... (TS: {})", ts);
            }
        }

        if let Some(fv) = emit_result {
            let feature_ts = chrono::Utc::now().timestamp_millis();
            self.last_features = Some(fv.clone());
            info!("Agent {} emitting features at {}", self.config.symbol, ts);
            self.last_decision_ts = ts;

            if let Some(eb) = &mut self.experience_builder {
                let exposure = pos_info.qty * current_mid;
                if let Some(row) = eb.finalize_step(
                    self.config.symbol.clone(),
                    ts,
                    current_mid,
                    self.config.decision_interval_ms as u32,
                    equity,
                    pos_info.realized_fees,
                    pos_info.realized_funding,
                    exposure,
                    0, // tib_count (bonus not used in Live/Paper experience recording)
                ) {
                    if let Some(tx) = &self.experience_tx {
                        let _ = tx.send(ExperienceCommand::Record(Box::new(row))).await;
                    }
                }
            }

            let (_vol, _spread) = {
                let vol = fv.rv_30s.unwrap_or(0.0);
                let spread = fv.spread_bps.unwrap_or(0.0);
                let mut lm = self.leverage_manager.lock().unwrap();
                lm.update_auto(&self.config.symbol, vol, spread, ts);
                (vol, spread)
            };

            let effective_lev = {
                let lm = self.leverage_manager.lock().unwrap();
                lm.get_effective_leverage(&self.config.symbol)
            };

            let (obs, _clamped) = fv.to_obs_vec();

            let req = crate::services::orchestrator::policy::HttpInferRequest {
                symbol: self.config.symbol.clone(),
                ts_ms: ts,
                mode: "PAPER".to_string(),
                decision_interval_ms: self.config.decision_interval_ms,
                obs: obs.clone(),
                risk: crate::services::orchestrator::policy::RiskInfo {
                    max_pos_frac: self.config.max_pos_frac,
                    effective_leverage: effective_lev,
                },
                portfolio: crate::services::orchestrator::policy::PortfolioInfo {
                    is_long: if pos_info.side == "Buy" { 1.0 } else { 0.0 },
                    is_short: if pos_info.side == "Sell" { 1.0 } else { 0.0 },
                    is_flat: if pos_info.qty.abs() < 1e-9 { 1.0 } else { 0.0 },
                    position_frac: if equity > 0.0 {
                        (pos_info.qty.abs() * fv.mid_price.unwrap_or(0.0)) / equity
                    } else {
                        0.0
                    },
                    upnl_frac: if equity > 0.0 {
                        pos_info.unrealized_pnl / equity
                    } else {
                        0.0
                    },
                    leverage_used: if equity > 0.0 {
                        (pos_info.qty.abs() * fv.mid_price.unwrap_or(0.0)) / equity
                    } else {
                        0.0
                    },
                    equity,
                    cash: equity,
                },
                meta: std::collections::HashMap::new(),
            };

            // RISK GATE CHECK (Stage 4A)
            let ablation_mode = std::env::var("BOTMK3_ABLATION").unwrap_or_else(|_| "FullSystem".to_string());
            
            let mut gate_res = {
                let risk = self.risk_manager.lock().unwrap();
                let cons_losses = risk.consecutive_losses;
                drop(risk);

                let s = self.status.lock().unwrap();
                self.risk_gate.check_gate(&s, self.orderbook.consecutive_failures, cons_losses)
            };
            
            if ablation_mode == "NoGateCooldowns" {
                gate_res = Ok(self.risk_gate.risk_mode.clone());
            }

            if let Err((reason, metrics)) = gate_res {
                let metrics_json = serde_json::to_string(&metrics).unwrap_or_default();
                warn!(
                    r#"{{"event": "risk_gate_triggered", "symbol": "{}", "reason": "{}", "metrics": {}}}"#,
                    self.config.symbol, reason, metrics_json
                );

                // Notify Orchestrator via Event
                let event = OrchestratorEvent {
                    ts: chrono::Utc::now().timestamp_millis(),
                    level: "WARN".to_string(),
                    r#type: "RISK".to_string(),
                    symbol: self.config.symbol.clone(),
                    message: format!("RISK GATE: {} blocked. Reason: {}", self.config.symbol, reason),
                    payload_json: metrics_json.clone(),
                    obs: obs.clone(),
                    metrics: metrics.iter().map(|(k, v)| (k.clone(), *v as f64)).collect(),
                };
                let _ = self.event_tx.send(event).await;

                // 4. Traceability: Record Veto even before inference
                if let Some(tx) = &self.analytics_tx {
                    let cand_record = crate::services::analytics::candidate::CandidateDecisionRecord {
                        candidate_id: uuid::Uuid::new_v4().to_string(),
                        run_id: self.run_id.clone(),
                        symbol: self.config.symbol.clone(),
                        side_intended: "GateVeto".to_string(),
                        target_qty: 0.0,
                        target_notional: 0.0,
                        regime_classification: "Vetoed-Pre-Inference".to_string(),
                        exec_quality_score: 0.0,
                        expected_move_bps: 0.0,
                        raw_model_value: 0.0,
                        baseline_move_bps: 0.0,
                        expected_move_bps_used: 0.0,
                        cost_gate_mode: "GateVeto".to_string(),
                        fee_bps_est: 0.0,
                        spread_bps_est: 0.0,
                        adverse_bps_penalty: 0.0,
                        slip_bps_est: 0.0,
                        expected_net_edge_bps: 0.0,
                        entry_mode_proposed: "None".to_string(),
                        risk_mode: format!("{:?}", self.risk_gate.risk_mode),
                        is_veto: true,
                        veto_reason: Some(format!("Pre-Inference Gate: {}", reason)),
                        simulator_mode: std::env::var("BOTMK3_PAPER_MODE").unwrap_or_else(|_| "ConservativeMaker".to_string()),
                        contrafactuals: vec![],
                        timestamps: crate::services::analytics::candidate::CandidateTimestamps {
                            exchange_ts,
                            recv_ts,
                            feature_ts,
                            decision_ts: chrono::Utc::now().timestamp_millis(),
                            order_intent_ts: 0,
                            user_stream_ts: 0, 
                            simulated_fill_ts: 0,
                        },
                    };
                    let _ = tx.send(crate::services::analytics::engine::AnalyticsEvent::CandidateRecord(cand_record)).await;
                }

                // Override to HOLD: We skip inference and return. 
                return;
            }

            match self.policy.infer_action(req).await {
                Ok(info) => {
                    info!(
                        "INFERENCE: Symbol={} ActionType={} Confidence={:.4} LogProb={:.4}",
                        self.config.symbol, info.action.r#type, info.confidence, info.log_prob
                    );

                    // Emit AI_FEATURES event for Data Analysis Tab
                    {
                        let mut metrics_map = std::collections::HashMap::new();
                        metrics_map.insert("confidence".to_string(), info.confidence);
                        metrics_map.insert("log_prob".to_string(), info.log_prob as f64);
                        metrics_map.insert("value".to_string(), info.value as f64);
                        metrics_map.insert("action_type".to_string(), info.action.r#type as f64);
                        metrics_map.insert("effective_leverage".to_string(), effective_lev);
                        metrics_map.insert("equity".to_string(), equity);

                        let ai_event = OrchestratorEvent {
                            ts: chrono::Utc::now().timestamp_millis(),
                            level: "INFO".to_string(),
                            r#type: "AI_FEATURES".to_string(),
                            symbol: self.config.symbol.clone(),
                            message: String::new(),
                            payload_json: String::new(),
                            obs: obs.clone(),
                            metrics: metrics_map,
                    };
                    let _ = self.event_tx.send(ai_event).await;
                }

                if let Some(ptx) = &self.parity_tx {
                    self.step_seq += 1;
                    ptx.send(
                        ts, 
                        md.time_exchange,
                        self.step_seq,
                        self.config.symbol.clone(), 
                        obs.clone(), 
                        info.action.r#type.to_string()
                    );
                }

                if let Some(eb) = &mut self.experience_builder {
                        eb.start_step(
                            obs,
                            info.action.r#type,
                            info.log_prob,
                            info.value,
                            pos_info.qty,
                            pos_info.side.clone(),
                            pos_info.entry_price,
                            pos_info.realized_fees,
                            pos_info.realized_funding,
                        );
                    }
                    let action_type = info.action.r#type;
                    let decision_ts = chrono::Utc::now().timestamp_millis();
                    self.handle_action(info.action, info.value as f64, info.confidence, exchange_ts, recv_ts, feature_ts, decision_ts, equity, pos_info.clone()).await;
                    
                    // Force refresh next tick if we took an action
                    if action_type != 0 {
                         self.last_decision_ts = 0; 
                    }
                }
                Err(e) => {
                    error!("Inference Failed: {}", e);
                    // Emit visible warning to GUI
                    let warn_event = OrchestratorEvent {
                        ts: chrono::Utc::now().timestamp_millis(),
                        level: "WARNING".to_string(),
                        r#type: "HEALTH".to_string(),
                        symbol: self.config.symbol.clone(),
                        message: format!("Policy inference failed: {}", e),
                        payload_json: "{}".to_string(),
                        obs: vec![],
                        metrics: std::collections::HashMap::new(),
                    };
                    let _ = self.event_tx.send(warn_event).await;
                }
            }
        }
    }

    async fn handle_action(&mut self, action: bot_core::proto::Action, alpha_logit: f64, confidence: f64, exchange_ts: i64, recv_ts: i64, feature_ts: i64, decision_ts: i64, equity: f64, pos: bot_data::execution::PositionInfo) {
        // ... (rest is same)
        // Action Type: 0=HOLD, 1=OPEN_LONG, 2=OPEN_SHORT, 3=CLOSE_ALL
        // 4=REDUCE_25, 5=REDUCE_50, 6=REDUCE_100

        let type_ = action.r#type;

        let mid = match self.feature_engine.current_mid_price() {
            Some(p) => p,
            None => return,
        };
        if mid <= 0.0 {
            return;
        }

        // Get effective leverage from LeverageManager
        let effective_lev = {
            let lm = self.leverage_manager.lock().unwrap();
            lm.get_effective_leverage(&self.config.symbol)
        };
        // sizing: max_pos_frac = fraction of equity used as MARGIN per position
        //   margin_budget = equity * max_pos_frac
        //   notional_target = margin_budget * leverage
        // Pre-fetch features for Sprint 3 pipeline
        let features = self.last_features.clone().unwrap_or_default();
        
        let ablation_mode = std::env::var("BOTMK3_ABLATION").unwrap_or_else(|_| "FullSystem".to_string());
        let mut regime_scores = (
            features.regime_trend.unwrap_or(0.0),
            features.regime_range.unwrap_or(0.0),
            features.regime_shock.unwrap_or(0.0),
            features.regime_dead.unwrap_or(0.0),
        );
        
        if ablation_mode == "NoRegime" {
            regime_scores = (0.0, 0.0, 0.0, 0.0);
        }
        
        let mut is_dead = regime_scores.3 > regime_scores.0 && regime_scores.3 > regime_scores.1 && regime_scores.3 > regime_scores.2;
        if ablation_mode == "NoRegime" { is_dead = false; }

        // 1. Execution Quality Block
        let exec_qual_cfg = ExecutionQualityConfig::default();
        let mut exec_qual = ExecutionQualityBlock::evaluate(&features, 1.0, &OrderBookStatus::InSync, &exec_qual_cfg);
        if ablation_mode == "NoExecQuality" {
             exec_qual.is_tradeable = true;
             exec_qual.score = 1.0;
             exec_qual.primary_reason = "Ablation Override".to_string();
        }
        
        if !exec_qual.is_tradeable && (type_ == 1 || type_ == 2) {
            warn!("Execution Quality Veto: {}", exec_qual.primary_reason);
            return;
        }

        // 2. Expected Cost & Net Edge (Two-pass for correct slippage notional)
        let mut cost_cfg = CostModelConfig::default();
        if let Ok(m) = std::env::var("BOTMK3_COST_MODE") {
            cost_cfg.value_mapping_mode = match m.as_str() {
                "LegacyRaw" => crate::services::orchestrator::cost_model::ValueMappingMode::LegacyRaw,
                "ScaledX10000" => crate::services::orchestrator::cost_model::ValueMappingMode::ScaledX10000,
                "BaselineOnly" => crate::services::orchestrator::cost_model::ValueMappingMode::BaselineOnly,
                _ => crate::services::orchestrator::cost_model::ValueMappingMode::BaselineOnly,
            };
        }
        let comm_cfg = self.commission_policy.lock().unwrap().clone();
        
        // Pass 1: compute size with confidence
        let sizing_cfg = DynamicSizingConfig::default();
        let risk_cfg = self.risk_manager.lock().unwrap().cfg.clone();
        let temp_size_res = DynamicSizingBlock::compute_size(
            equity,
            mid,
            confidence,
            regime_scores,
            &exec_qual,
            &sizing_cfg,
            &risk_cfg,
            self.config.max_pos_frac,
            effective_lev
        );
        let expected_notional = temp_size_res.as_ref().map(|s| s.target_notional).unwrap_or(0.0);

        let mut expected_cost = CostModelBlock::estimate_cost(&OrderIntent::Entry, &EntryMode::Maker, expected_notional, &features, &comm_cfg);
        if ablation_mode == "NoCostModel" {
             expected_cost.fee_bps_est = 0.0;
             expected_cost.spread_cost_bps_est = 0.0;
             expected_cost.slippage_bps_est = 0.0;
             expected_cost.adverse_selection_bps_est = 0.0;
        }

        let baseline_move_bps = features.rv_5s.unwrap_or(0.0) * 0.5;
        let mut expected_net_edge = CostModelBlock::check_edge(alpha_logit, &expected_cost, &cost_cfg, baseline_move_bps);
        if ablation_mode == "NoCostModel" {
             expected_net_edge.passes_threshold = true;
             expected_net_edge.net_edge_bps = 999.0;
        }

        // 3. Dynamic Sizing Block (Final sizing using net edge)
        let size_res = DynamicSizingBlock::compute_size(
            equity,
            mid,
            confidence,
            regime_scores,
            &exec_qual,
            &sizing_cfg,
            &risk_cfg,
            self.config.max_pos_frac,
            effective_lev
        );

        let mut veto_reason = "None".to_string();
        let target_qty = if let Some(sr) = &size_res {
            sr.target_qty
        } else {
            if type_ == 1 || type_ == 2 {
                veto_reason = "Dynamic Sizing Veto".to_string();
                warn!("{}: Target notional too small or NoTrade regime", veto_reason);
            }
            0.0 // Allow closes
        };

        if !exec_qual.is_tradeable && (type_ == 1 || type_ == 2) {
            veto_reason = format!("Execution Quality Veto: {}", exec_qual.primary_reason);
            warn!("{}", veto_reason);
        }
        
        // 4. Generate Pre-Flight Traceability Candidate Record
        let order_intent_ts = chrono::Utc::now().timestamp_millis();
        
        let mut contrafactuals = vec![];
        if type_ == 1 || type_ == 2 {
            // Contrafactuals for non-HOLD intent
            let taker_cost = CostModelBlock::estimate_cost(&OrderIntent::Entry, &EntryMode::Taker, expected_notional, &features, &comm_cfg);
            let taker_edge = CostModelBlock::check_edge(alpha_logit, &taker_cost, &cost_cfg, baseline_move_bps);
            contrafactuals.push(crate::services::analytics::candidate::ContrafactualOutcome {
                mode: "Taker".to_string(),
                expected_net_edge_bps: taker_edge.net_edge_bps,
            });
        }

        let cand_record = crate::services::analytics::candidate::CandidateDecisionRecord {
            candidate_id: uuid::Uuid::new_v4().to_string(),
            run_id: self.run_id.clone(),
            symbol: self.config.symbol.clone(),
            side_intended: if type_ == 1 { "Buy".to_string() } else if type_ == 2 { "Sell".to_string() } else { "None".to_string() },
            target_qty,
            target_notional: size_res.as_ref().map(|s| s.target_notional).unwrap_or(0.0),
            regime_classification: format!("Trend:{:.2}|Range:{:.2}|Shock:{:.2}|Dead:{:.2}", regime_scores.0, regime_scores.1, regime_scores.2, regime_scores.3),
            exec_quality_score: exec_qual.score,
            expected_move_bps: expected_net_edge.expected_move_bps,
            raw_model_value: expected_net_edge.raw_model_value,
            baseline_move_bps: expected_net_edge.baseline_move_bps,
            expected_move_bps_used: expected_net_edge.expected_move_bps_used,
            cost_gate_mode: expected_net_edge.cost_gate_mode.clone(),
            fee_bps_est: expected_cost.fee_bps_est,
            spread_bps_est: expected_cost.spread_cost_bps_est,
            adverse_bps_penalty: expected_cost.adverse_selection_bps_est,
            slip_bps_est: expected_cost.slippage_bps_est,
            expected_net_edge_bps: expected_net_edge.net_edge_bps,
            entry_mode_proposed: "Maker".to_string(),
            risk_mode: format!("{:?}", self.risk_gate.risk_mode),
            is_veto: target_qty == 0.0 && (type_ == 1 || type_ == 2),
            veto_reason: if type_ == 0 || target_qty > 0.0 { None } else { Some(veto_reason.clone()) },
            simulator_mode: std::env::var("BOTMK3_PAPER_MODE").unwrap_or_else(|_| "ConservativeMaker".to_string()),
            contrafactuals,
            timestamps: crate::services::analytics::candidate::CandidateTimestamps {
                exchange_ts,
                recv_ts,
                feature_ts,
                decision_ts,
                order_intent_ts,
                user_stream_ts: 0, 
                simulated_fill_ts: 0,
            },
        };

        if let Some(tx) = &self.analytics_tx {
            let _ = tx.send(crate::services::analytics::engine::AnalyticsEvent::CandidateRecord(cand_record)).await;
        }

        // End-to-End Decision Diagnostics
        if type_ == 1 || type_ == 2 {
            let mut diag_metrics = std::collections::HashMap::new();
            diag_metrics.insert("expected_move_bps_used".to_string(), expected_net_edge.expected_move_bps_used);
            diag_metrics.insert("raw_model_value".to_string(), expected_net_edge.raw_model_value);
            diag_metrics.insert("baseline_move_bps".to_string(), expected_net_edge.baseline_move_bps);
            diag_metrics.insert("fee_bps_est".to_string(), expected_cost.fee_bps_est);
            diag_metrics.insert("spread_cost_bps_est".to_string(), expected_cost.spread_cost_bps_est);
            diag_metrics.insert("slippage_bps_est".to_string(), expected_cost.slippage_bps_est);
            diag_metrics.insert("adverse_selection_bps_est".to_string(), expected_cost.adverse_selection_bps_est);
            diag_metrics.insert("expected_net_edge_bps".to_string(), expected_net_edge.net_edge_bps);
            
            diag_metrics.insert("regime_mult".to_string(), size_res.as_ref().map(|s| s.regime_mult).unwrap_or(0.0));
            diag_metrics.insert("exec_qual_mult".to_string(), exec_qual.score);
            diag_metrics.insert("confidence_mult".to_string(), if 1.0 > sizing_cfg.min_confidence { 1.0 } else { 0.0 });
            diag_metrics.insert("target_notional".to_string(), size_res.as_ref().map(|s| s.target_notional).unwrap_or(0.0));
    
            let stop_cfg = StopPolicyConfig::default();
            let stop_policy = StopPolicyBlock::compute_stop(&features, regime_scores, &stop_cfg);
            diag_metrics.insert("sl_dist_bps".to_string(), stop_policy.sl_dist_bps);
            
            let current_risk_mode = format!("{:?}", self.risk_gate.risk_mode);
            let payload_json = format!(r#"{{"risk_mode": "{}", "veto_reason": "{}"}}"#, current_risk_mode, veto_reason);

            let event = OrchestratorEvent {
                ts: chrono::Utc::now().timestamp_millis(),
                level: "INFO".to_string(),
                r#type: "DIAGNOSTIC".to_string(),
                symbol: self.config.symbol.clone(),
                message: format!("Candidate Evaluated. Veto: {}", veto_reason),
                payload_json,
                obs: vec![],
                metrics: diag_metrics,
            };
            let _ = self.event_tx.send(event).await;
            
            // Record explicitly evaluated candidate to decay Recovery Mode
            self.risk_gate.record_trade_evaluated();

            if target_qty == 0.0 || !exec_qual.is_tradeable {
                return; // Abort trade execution
            }
        }

        // Logic mapping RL actions to Orders
        // This must match `rl.rs` logic roughly.
        let pos_qty = pos.qty;
        let pos_side = pos.side.as_str(); // "Buy", "Sell", "Flat"

        let current_signed_qty = if pos_side == "Sell" {
            -pos_qty
        } else {
            pos_qty
        };

        match type_ {
            1 => {
                // OPEN_LONG
                if pos_side == "Flat" {
                    let allowed = {
                        let risk = self.risk_manager.lock().unwrap();
                        risk.check_entry_allowed("Buy", features.microprice_minus_mid_bps.unwrap_or(0.0))
                    };
                    match allowed {
                        Ok(()) => self.submit_order("Buy", target_qty, mid, &pos, equity, effective_lev, &features, Some(expected_net_edge.net_edge_bps), is_dead).await,
                        Err(e) => warn!(target: "risk_gate", "OPEN_LONG Blocked: {}", e),
                    }
                }
            }
            2 => {
                // ADD_LONG
                if pos_side == "Buy" {
                    let allowed = {
                        let risk = self.risk_manager.lock().unwrap();
                        risk.check_entry_allowed("Buy", features.microprice_minus_mid_bps.unwrap_or(0.0))
                    };
                    match allowed {
                        Ok(()) => self.submit_order("Buy", target_qty * 0.5, mid, &pos, equity, effective_lev, &features, Some(expected_net_edge.net_edge_bps), is_dead).await,
                        Err(e) => warn!(target: "risk_gate", "ADD_LONG Blocked: {}", e),
                    }
                }
            }
            3 => {
                // REDUCE_LONG
                if pos_side == "Buy" {
                    let allowed = {
                        let risk = self.risk_manager.lock().unwrap();
                        risk.check_exit_allowed(pos_side, pos.entry_price, mid)
                    };
                    match allowed {
                        Ok(()) => {
                            self.submit_order("Sell", pos_qty * 0.5, mid, &pos, equity, effective_lev, &features, Some(expected_net_edge.net_edge_bps), is_dead).await;
                        }
                        Err(e) => warn!("REDUCE_LONG Blocked: {}", e),
                    }
                }
            }
            4 => {
                // CLOSE_LONG
                if pos_side == "Buy" {
                    let allowed = {
                        let risk = self.risk_manager.lock().unwrap();
                        risk.check_exit_allowed(pos_side, pos.entry_price, mid)
                    };
                    match allowed {
                        Ok(()) => {
                            self.submit_order("Sell", pos_qty, mid, &pos, equity, effective_lev, &features, Some(expected_net_edge.net_edge_bps), is_dead).await;
                        }
                        Err(e) => warn!("CLOSE_LONG Blocked: {}", e),
                    }
                }
            }
            5 => {
                // OPEN_SHORT
                if pos_side == "Flat" {
                    let allowed = {
                        let risk = self.risk_manager.lock().unwrap();
                        risk.check_entry_allowed("Sell", features.microprice_minus_mid_bps.unwrap_or(0.0))
                    };
                    match allowed {
                        Ok(()) => self.submit_order("Sell", target_qty, mid, &pos, equity, effective_lev, &features, Some(expected_net_edge.net_edge_bps), is_dead).await,
                        Err(e) => warn!(target: "risk_gate", "OPEN_SHORT Blocked: {}", e),
                    }
                }
            }
            6 => {
                // ADD_SHORT
                if pos_side == "Sell" {
                    let allowed = {
                        let risk = self.risk_manager.lock().unwrap();
                        risk.check_entry_allowed("Sell", features.microprice_minus_mid_bps.unwrap_or(0.0))
                    };
                    match allowed {
                        Ok(()) => self.submit_order("Sell", target_qty * 0.5, mid, &pos, equity, effective_lev, &features, Some(expected_net_edge.net_edge_bps), is_dead).await,
                        Err(e) => warn!(target: "risk_gate", "ADD_SHORT Blocked: {}", e),
                    }
                }
            }
            7 => {
                // REDUCE_SHORT
                if pos_side == "Sell" {
                    let allowed = {
                        let risk = self.risk_manager.lock().unwrap();
                        risk.check_exit_allowed(pos_side, pos.entry_price, mid)
                    };
                    match allowed {
                        Ok(()) => {
                            self.submit_order("Buy", pos_qty * 0.5, mid, &pos, equity, effective_lev, &features, Some(expected_net_edge.net_edge_bps), is_dead).await;
                        }
                        Err(e) => warn!("REDUCE_SHORT Blocked: {}", e),
                    }
                }
            }
            8 => {
                // CLOSE_SHORT
                if pos_side == "Sell" {
                    let allowed = {
                        let risk = self.risk_manager.lock().unwrap();
                        risk.check_exit_allowed(pos_side, pos.entry_price, mid)
                    };
                    match allowed {
                        Ok(()) => {
                            self.submit_order("Buy", pos_qty, mid, &pos, equity, effective_lev, &features, Some(expected_net_edge.net_edge_bps), is_dead).await;
                        }
                        Err(e) => warn!("CLOSE_SHORT Blocked: {}", e),
                    }
                }
            }
            9 => {
                // REPRICE (IntentIONAL RE-POST)
                // In Agent mode, we don't have separate reprice logic yet, it will re-evaluate next tick.
                info!("RL_ACTION_REPRICE: sym={}", self.config.symbol);
            }
            _ => {}
        }
    }

    fn should_apply_leverage(
        last_applied: Option<f64>,
        target_lev: f64,
        now_ts: i64,
        last_apply_ts: i64,
    ) -> bool {
        let lev_changed = last_applied
            .map(|prev| (prev - target_lev).abs() >= LEVERAGE_APPLY_MIN_DELTA)
            .unwrap_or(true);
        let cooldown_elapsed = now_ts - last_apply_ts >= LEVERAGE_APPLY_COOLDOWN_MS;
        lev_changed && cooldown_elapsed
    }

    fn is_reduce_only(current_signed_qty: f64, order_signed_qty: f64, qty: f64) -> bool {
        current_signed_qty.signum() != 0.0
            && current_signed_qty.signum() != order_signed_qty.signum()
            && qty <= current_signed_qty.abs() + 1e-9
    }

    async fn submit_order(
        &mut self,
        side: &str,
        qty: f64,
        price: f64,
        pos_for_risk: &PositionInfo,
        equity_for_risk: f64,
        leverage: f64,
        features: &bot_data::features_v2::schema::FeatureRow,
        expected_net_edge_bps: Option<f64>,
        is_dead_regime: bool,
    ) {
        if equity_for_risk <= 0.0 {
            warn!(
                "Risk check equity <= 0 for {}. Blocking order.",
                self.config.symbol
            );
            return;
        }

        let current_signed_qty = if pos_for_risk.side == "Sell" {
            -pos_for_risk.qty
        } else {
            pos_for_risk.qty
        };
        let order_signed_qty = if side == "Sell" { -qty } else { qty };
        let is_reduce_only = Self::is_reduce_only(current_signed_qty, order_signed_qty, qty);

        let proposed = ProposedOrder {
            symbol: self.config.symbol.clone(),
            side: side.to_string(),
            qty,
            price,
            is_reduce_only,
        };

        let account = AccountSnapshot {
            equity: equity_for_risk,
            wallet_balance: equity_for_risk,
            unrealized_pnl: pos_for_risk.unrealized_pnl,
            realized_pnl: pos_for_risk.realized_pnl,
        };

        let positions = vec![PositionSnapshot {
            symbol: self.config.symbol.clone(),
            qty: pos_for_risk.qty,
            side: pos_for_risk.side.clone(),
            entry_price: pos_for_risk.entry_price,
            notional: pos_for_risk.notional_value,
            margin_used: pos_for_risk.margin_used,
            leverage,
        }];

        let decision = {
            let mut risk = self.risk_manager.lock().unwrap();
            let now_ms = chrono::Utc::now().timestamp_millis();
            risk.check_order_allowed(now_ms, &self.config.symbol, &proposed, &account, &positions)
        };

        match decision {
            Ok(()) => {
                // Commission Policy Decision
                let (best_bid, best_ask) = self.orderbook.best_bid_ask();

                // Guard: if orderbook is empty, fall back to MARKET
                if best_bid <= 0.0 || best_ask <= 0.0 {
                    warn!(
                        "OrderBook empty for {} (bid={}, ask={}), using MARKET",
                        self.config.symbol, best_bid, best_ask
                    );
                    match self
                        .execution
                        .submit_order(&self.config.symbol, side, qty, price, "MARKET")
                        .await
                    {
                        Ok(id) => {
                            info!(
                                "Order SENT (no OB) {}: {} {} MARKET @ {}",
                                self.config.symbol, id, side, price
                            );
                            if let Some(eb) = &mut self.experience_builder {
                                eb.orders_in_step += 1;
                            }
                        }
                        Err(e) => error!("Order Error {}: {}", self.config.symbol, e),
                    }
                    return;
                }

                let order_type = {
                    let policy = self.commission_policy.lock().unwrap();
                    let stats = self.commission_stats.lock().unwrap();
                    // Determine intent / urgency from context
                    let intent = if pos_for_risk.qty.abs() < 1e-9 {
                        OrderIntent::Entry
                    } else {
                        OrderIntent::Exit
                    };
                    let urgency = UrgencyLevel::Normal;
                    decide_order_type(
                        &policy, &stats, &intent, &urgency, side, best_bid, best_ask, 2.0, 4.0,
                        1.0, expected_net_edge_bps, is_dead_regime 
                    )
                };
                let (order_type_str, limit_price) = match order_type {
                    OrderDecision::UseMaker { price: lp } => ("LIMIT", lp),
                    OrderDecision::UseTaker => ("MARKET", price),
                    OrderDecision::Rejected(reason) => {
                        warn!("Commission Rejected {}: {}", self.config.symbol, reason);
                        return;
                    }
                };
                // 4. Stop Policy limits (Informational for now)
                let stop_cfg = StopPolicyConfig::default();
                let regime_scores = (
                    features.regime_trend.unwrap_or(0.0),
                    features.regime_range.unwrap_or(0.0),
                    features.regime_shock.unwrap_or(0.0),
                    features.regime_dead.unwrap_or(0.0),
                );
                let stop_policy = StopPolicyBlock::compute_stop(features, regime_scores, &stop_cfg);
                if !stop_policy.is_valid {
                    warn!("Stop Policy Veto: Required SL {} bps exceeds max {} bps", stop_policy.sl_dist_bps, stop_cfg.max_allowed_sl_bps);
                    // In a live system, we might return here. But since this is informational sprint 3, we log the veto.
                }

                match self
                    .execution
                    .submit_order(&self.config.symbol, side, qty, limit_price, order_type_str)
                    .await
                {
                    Ok(id) => {
                        info!(
                            "Order SENT {}: {} {} {} @ {} ({})",
                            self.config.symbol, id, side, order_type_str, limit_price, qty
                        );
                        if let Some(eb) = &mut self.experience_builder {
                            eb.orders_in_step += 1;
                        }
                    }
                    Err(e) => error!("Order Error {}: {}", self.config.symbol, e),
                }
            }
            Err(reason) => {
                warn!("Risk Blocked {}: {}", self.config.symbol, reason);
            }
        }
    }

    async fn on_fill(&mut self, record: bot_data::reporting::backtest::ExecutionRecord) {
        info!(
            "FILL RECEIVED {}: {} {} @ {}",
            self.config.symbol, record.side, record.qty, record.price
        );

        // Track commission stats (maker vs taker)
        {
            let is_taker = record.order_type == "Market" || record.order_type == "MARKET";
            let notional = record.qty * record.price;
            let mut stats = self.commission_stats.lock().unwrap();
            stats.record_fill(is_taker, record.fee, notional);
        }

        // Notify Analytics (Real Fill)
        if let Some(tx) = &self.analytics_tx {
            let fill_event = AnalyticsEvent::Fill {
                symbol: record.symbol.clone(),
                side: if record.side == "Buy" {
                    Side::Buy
                } else {
                    Side::Sell
                },
                qty: record.qty,
                price: record.price,
                fee: record.fee,
                ts: record.ts,
                order_type: if record.order_type == "Market" {
                    OrderType::Market
                } else {
                    OrderType::Limit
                },
            };
            let _ = tx.send(fill_event).await;
        }

        // Emit Orchestrator Event
        let event = OrchestratorEvent {
            ts: record.ts,
            level: "INFO".to_string(),
            r#type: "FILL".to_string(),
            symbol: record.symbol,
            message: format!(
                "FILL: {} {} @ {} (Fee: {:.4})",
                record.side, record.qty, record.price, record.fee
            ),
            payload_json: "{}".to_string(),
            obs: vec![],
            metrics: std::collections::HashMap::new(),
        };
        let _ = self.event_tx.send(event).await;

        // Structured Logging for Closed Trade & Cash Sync
        if let (Ok(e), Ok(p)) = (
            self.execution.get_equity().await,
            self.execution.get_position(&self.config.symbol).await,
        ) {
            self.cash_balance = e - p.unrealized_pnl;

            // Update multi-level drawdown tracking after every fill
            {
                let mut risk = self.risk_manager.lock().unwrap();
                risk.on_equity_update(e);
            }

            let (prev_qty, prev_entry, prev_margin, _prev_funding) = {
                let s = self.status.lock().unwrap();
                (
                    s.position_qty,
                    s.entry_price,
                    s.equity_alloc_used,
                    s.funding_pnl,
                )
            };

            let lev = self
                .leverage_manager
                .lock()
                .unwrap()
                .get_effective_leverage(&self.config.symbol);

            if prev_qty.abs() < 1e-9 && p.qty.abs() > 1e-9 {
                // Opened
                self.current_trip_start_ts = Some(record.ts);
                self.cumulative_trip_fees = record.fee;
            } else if prev_qty.abs() > 1e-9 && p.qty.abs() > 1e-9 {
                // Added/Reduced
                self.cumulative_trip_fees += record.fee;
            } else if prev_qty.abs() > 1e-9 && p.qty.abs() < 1e-9 {
                // Closed
                self.cumulative_trip_fees += record.fee;

                let pnl_gross = p.realized_pnl;
                let funding = p.realized_funding;
                let pnl_net = pnl_gross - self.cumulative_trip_fees + funding;

                // Adaptive Risk
                {
                    let mut risk = self.risk_manager.lock().unwrap();
                    risk.record_trade_outcome(pnl_net);
                }

                // Analytics
                if let Some(tx) = &self.analytics_tx {
                    // Fee Log
                    let fee_event = AnalyticsEvent::FeeLog {
                        symbol: self.config.symbol.clone(),
                        fee_total: p.realized_fees,
                        fee_pct_notional: if p.notional_value > 0.0 {
                            p.realized_fees / p.notional_value
                        } else {
                            0.0
                        },
                        side: if p.side == "Buy" {
                            Side::Buy
                        } else {
                            Side::Sell
                        },
                        ts: chrono::Utc::now().timestamp_millis(),
                    };
                    let _ = tx.send(fee_event).await;

                    // Round Trip
                    if let Some(start_ts) = self.current_trip_start_ts {
                        let trip = crate::services::analytics::engine::RoundTripRecord {
                            symbol: self.config.symbol.clone(),
                            side: if prev_qty > 0.0 {
                                "LONG".to_string()
                            } else {
                                "SHORT".to_string()
                            },
                            qty: prev_qty.abs(),
                            entry_price: prev_entry,
                            exit_price: record.price,
                            entry_ts: start_ts,
                            exit_ts: record.ts,
                            margin_used: prev_margin,
                            leverage: lev,
                            pnl_gross,
                            pnl_net,
                            total_fees: self.cumulative_trip_fees,
                            funding_fees: funding,
                        };
                        let _ = tx.send(AnalyticsEvent::RoundTrip(trip)).await;
                    }
                }

                info!(
                    r#"{{"event": "trade_closed", "symbol": "{}", "notional": {:.2}, "margin": {:.2}, "leverage": {:.1}, "fees": {:.4}, "pnl": {:.2}, "funding": {:.4}}}"#,
                    self.config.symbol,
                    p.notional_value,
                    prev_margin,
                    lev,
                    self.cumulative_trip_fees,
                    pnl_gross,
                    funding
                );

                self.current_trip_start_ts = None;
                self.cumulative_trip_fees = 0.0;
            }

            // Sync back to experience builder to avoid stale obs on next tick
            if let Some(eb) = &mut self.experience_builder {
                eb.prev_pos_qty = p.qty;
                eb.prev_pos_side = p.side.clone();
                eb.prev_entry_price = p.entry_price;
                eb.prev_realized_fees = p.realized_fees;
                eb.prev_realized_pnl = p.realized_pnl;
                eb.prev_realized_funding = p.realized_funding;
                eb.prev_equity = e;
            }
        }
    }

    async fn validate_model_profile(&self, model_path: &str) -> Result<(), String> {
        // Model path example: models/live/model_20260219_...
        // Metadata path example: models/registry/model_20260219_....json

        // 1. Infer model_id from path
        let path = std::path::Path::new(model_path);
        let model_id = path
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or_else(|| format!("Invalid model path: {}", model_path))?;

        // 2. Load metadata from registry
        let registry_dir = std::path::Path::new("python/models/registry");
        let meta_path = registry_dir.join(format!("{}.json", model_id));

        if !meta_path.exists() {
            // Fallback: check if metadata.json is inside the model_path directory itself
            let alt_path = path.join("metadata.json");
            if !alt_path.exists() {
                return Err(format!("Model metadata not found for {}", model_id));
            }
            return self.check_profile_file(&alt_path).await;
        }

        self.check_profile_file(&meta_path).await
    }

    async fn check_profile_file(&self, path: &std::path::Path) -> Result<(), String> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| format!("Failed to read model metadata: {}", e))?;

        let meta: serde_json::Value = serde_json::from_str(&content)
            .map_err(|e| format!("Failed to parse model metadata: {}", e))?;

        let profile = meta["feature_profile"].as_str().unwrap_or("Rich");
        let required = &self.config.feature_profile;

        if profile.to_lowercase() != required.to_lowercase() {
            return Err(format!(
                "Feature Profile Mismatch: Model uses '{}', but SymbolConfig requires '{}'",
                profile, required
            ));
        }

        Ok(())
    }
    async fn report_telemetry(&mut self, current_ts: i64) {
        if self.last_telemetry_ts == 0 {
            self.last_telemetry_ts = current_ts;
            return;
        }

        if current_ts - self.last_telemetry_ts < 30_000 {
            return;
        }

        self.last_telemetry_ts = current_ts;
        let report = self.feature_engine.get_health_report(current_ts);

        // 1. SymbolStatus is already updated every tick in the main loop before RiskGate.


        // 2. Log to file
        let telemetry_dir = std::path::PathBuf::from("runs").join(&self.run_id).join("telemetry");
        if let Err(e) = std::fs::create_dir_all(&telemetry_dir) {
            error!("Failed to create telemetry dir: {}", e);
            return;
        }

        let file_path = telemetry_dir.join("feature_health.jsonl");
        let line = match serde_json::to_string(&report) {
            Ok(l) => format!("{}\n", l),
            Err(e) => {
                error!("Failed to serialize health report: {}", e);
                return;
            }
        };

        use std::io::Write;
        let result = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&file_path)
            .and_then(|mut f| f.write_all(line.as_bytes()));

        if let Err(e) = result {
            error!("Failed to write telemetry for {}: {}", self.config.symbol, e);
        }
    }

    async fn on_real_fill_for_divergence(&mut self, record: bot_data::reporting::backtest::ExecutionRecord) {
        // Here we compare the REAL FILL (from Binance User Data Stream) with the SIMULATED state.
        // We fetch the current sim equity/position to compute a heuristic divergence.
        
        let sim_pos_res = self.execution.get_position(&self.config.symbol).await;
        
        let mut expected_price = 0.0;
        let mut expected_qty = 0.0;
        let mut expected_fee = 0.0;

        if let Ok(sim_pos) = sim_pos_res {
            expected_price = if sim_pos.qty.abs() > 0.0 { sim_pos.entry_price } else { 0.0 };
            expected_qty = sim_pos.qty;
            expected_fee = sim_pos.realized_fees; // Aggregate
        }

        let div = crate::services::analytics::engine::SimVsRealDivergence {
            symbol: self.config.symbol.clone(),
            order_id: format!("{}_SHADOW_{}", self.config.symbol, record.ts),
            event_ts: record.ts,
            side: if record.side == "Buy" { Side::Buy } else { Side::Sell },
            order_type: if record.order_type == "Market" || record.order_type == "MARKET" { OrderType::Market } else { OrderType::Limit },
            expected_price,
            expected_qty,
            expected_fee,
            realized_price: Some(record.price),
            realized_qty: record.qty,
            realized_fee: record.fee,
            delay_ms: 0, // Hard to compute without exact order submit time mapping, but 0 indicates purely position-level divergence
        };

        if let Some(tx) = &self.analytics_tx {
            let _ = tx.send(crate::services::analytics::engine::AnalyticsEvent::SimVsRealDivergence(div)).await;
        }
    }
}

#[cfg(test)]
mod unit_tests {
    use super::SymbolAgent;

    #[test]
    fn reduce_only_detects_closing_trade() {
        assert!(SymbolAgent::is_reduce_only(2.0, -1.0, 1.0));
    }

    #[test]
    fn reduce_only_rejects_same_direction() {
        assert!(!SymbolAgent::is_reduce_only(2.0, 1.0, 1.0));
    }

    #[test]
    fn reduce_only_rejects_flip_order() {
        assert!(!SymbolAgent::is_reduce_only(2.0, -3.0, 3.0));
    }

    #[test]
    fn leverage_apply_requires_cooldown_and_delta() {
        assert!(!SymbolAgent::should_apply_leverage(
            Some(5.0),
            5.10,
            20_000,
            0
        ));
        assert!(!SymbolAgent::should_apply_leverage(
            Some(5.0),
            5.50,
            10_000,
            0
        ));
        assert!(SymbolAgent::should_apply_leverage(
            Some(5.0),
            5.50,
            31_000,
            0
        ));
    }

    #[test]
    fn leverage_apply_first_time_allowed_after_cooldown() {
        assert!(!SymbolAgent::should_apply_leverage(None, 7.0, 10_000, 0));
        assert!(SymbolAgent::should_apply_leverage(None, 7.0, 31_000, 0));
    }
}
