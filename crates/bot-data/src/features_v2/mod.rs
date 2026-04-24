//! # Feature Engine V2
//!
//! Modular, deterministic, no-lookahead feature engine producing 83-feature
//! `FeatureRow` vectors at fixed 1-second intervals.
//!
//! ## Architecture
//! - `schema` — FeatureRow struct (the output)
//! - `buffers` — RingBuffer, TradeAccumulator, LiqAccumulator, BoolRingBuffer, EMA, RSI, BB
//! - `compute_*` — Stateful compute modules per feature group
//! - `warmup` — Tracks per-group readiness
//! - `FeatureEngineV2` — Orchestrator tying everything together

pub mod schema;
pub mod buffers;
pub mod compute_price;
pub mod compute_flow;
pub mod compute_micro;
pub mod compute_shocks;
pub mod compute_tech;
pub mod compute_time;
pub mod compute_oi;
pub mod compute_account;
pub mod compute_absorption;
pub mod compute_persistence;
pub mod compute_regime;
pub mod warmup;
pub mod health;

use schema::FeatureRow;
use compute_price::PriceState;
use compute_flow::FlowState;
use compute_micro::MicroState;
use compute_shocks::ShockState;
use compute_tech::TechState;
use compute_account::AccountState;
use warmup::WarmupTracker;
use health::FeatureHealthAggregator;
use crate::normalization::schema::{NormalizedMarketEvent, TimeMode};
use crate::orderbook::engine::{OrderBook, OrderBookStatus};
use log::{warn, info, debug};
use std::collections::VecDeque;

/// Configuration for the FeatureEngineV2.
#[derive(Debug, Clone)]
pub struct FeatureEngineV2Config {
    /// Emission interval in milliseconds (default: 1000 = 1s)
    pub interval_ms: i64,
    /// Symbol this engine is tracking
    pub symbol: String,
    pub time_mode: TimeMode,
    pub recv_time_lag_ms: i64,
    pub micro_strict: bool,
    pub tape_zscore_clamp: (f64, f64),
    pub slow_tf: String,
    
    // Telemetry Config
    pub telemetry_enabled: bool,
    pub telemetry_window_ms: i64,

    // Sprint 2 Thresholds (centralized)
    pub absorption_thresholds: compute_absorption::AbsorptionThresholds,
    pub persistence_thresholds: compute_persistence::PersistenceThresholds,
    pub persistence_window: usize,
    pub regime_thresholds: compute_regime::RegimeThresholds,
}

impl Default for FeatureEngineV2Config {
    fn default() -> Self {
        Self {
            interval_ms: 1000,
            symbol: "BTCUSDT".to_string(),
            time_mode: TimeMode::EventTimeOnly,
            recv_time_lag_ms: 0,
            micro_strict: true,
            tape_zscore_clamp: (-5.0, 5.0),
            slow_tf: "1s".to_string(),
            telemetry_enabled: true,
            telemetry_window_ms: 300_000,
            absorption_thresholds: compute_absorption::AbsorptionThresholds::default(),
            persistence_thresholds: compute_persistence::PersistenceThresholds::default(),
            persistence_window: 10,
            regime_thresholds: compute_regime::RegimeThresholds::default(),
        }
    }
}

/// Feature Engine V2 — Produces 83-feature FeatureRows every Δt seconds.
///
/// ## Contract
/// - `update(ev)`: Feed incoming market events. Only events with timestamp <= t_emit
///   are used in the next emission.
/// - `maybe_emit(current_ts)`: If current_ts >= next_emit_ts, compute and return a FeatureRow.
/// - `warmup_ready()`: Returns true when minimum data requirements are met.
///
/// ## Determinism Guarantee
/// Given the same sequence of `update()` calls with the same event data,
/// `maybe_emit()` produces identical output.
#[derive(Clone)]
struct PendingEvent {
    ev: NormalizedMarketEvent,
    top_bids: Vec<(f64, f64)>,
    top_asks: Vec<(f64, f64)>,
    ob_in_sync: bool,
}

pub struct FeatureEngineV2 {
    config: FeatureEngineV2Config,
    next_emit_ts: i64,

    // ── Compute Sub-Modules ──
    price: compute_price::PriceState,
    flow: compute_flow::FlowState,
    micro: compute_micro::MicroState,
    shocks: compute_shocks::ShockState,
    tech: compute_tech::TechState,
    account: compute_account::AccountState,
    oi: compute_oi::OIState,
    absorption: compute_absorption::AbsorptionState,
    persistence: compute_persistence::PersistenceState,
    regime: compute_regime::RegimeState,
    warmup: warmup::WarmupTracker,
    
    // ── Telemetry ──
    health: FeatureHealthAggregator,
    
    // ── OrderBook Maintenance (L2) ──
    ob: OrderBook,

    // ── State for Emission ──
    current_best_bid: f64,
    current_best_ask: f64,
    pending_queue: std::collections::VecDeque<PendingEvent>,
    
    /// Keep track of last timestamp we advanced technical/price buffers.
    last_state_advance_ts: i64,
}

impl FeatureEngineV2 {
    pub fn new(config: FeatureEngineV2Config) -> Self {
        let persistence_window = config.persistence_window;
        let symbol = config.symbol.clone();
        let telemetry_window_ms = config.telemetry_window_ms;
        Self {
            config,
            next_emit_ts: 0,
            price: compute_price::PriceState::new(),
            flow: compute_flow::FlowState::new(),
            micro: compute_micro::MicroState::new(),
            shocks: compute_shocks::ShockState::new(),
            tech: compute_tech::TechState::new(),
            account: compute_account::AccountState::default(),
            oi: compute_oi::OIState::new(),
            absorption: compute_absorption::AbsorptionState::new(),
            persistence: compute_persistence::PersistenceState::new(persistence_window),
            regime: compute_regime::RegimeState::new(),
            warmup: warmup::WarmupTracker::new(),
            health: FeatureHealthAggregator::new(symbol.clone(), telemetry_window_ms),
            ob: OrderBook::new(symbol),
            current_best_bid: 0.0,
            current_best_ask: 0.0,
            pending_queue: VecDeque::new(),
            last_state_advance_ts: 0,
        }
    }

    fn record_feed_health(&mut self, ev: &NormalizedMarketEvent) {
        let ts = ev.time_canonical;
        let stream = ev.stream_name.as_str();
        let event_type = ev.event_type.as_str();

        if (stream.contains("aggTrade")
            || stream.contains("trade")
            || event_type == "trade"
            || event_type == "aggTrade")
            && ev.price.is_some()
            && ev.qty.is_some()
        {
            self.health.last_trades_ts = ts;
        }

        if (stream.contains("bookTicker") || event_type == "bookTicker")
            && ev.best_bid.unwrap_or_default() > 0.0
            && ev.best_ask.unwrap_or_default() > 0.0
        {
            self.health.last_book_ts = ts;
        }

        if stream.contains("markPrice") || event_type == "markPrice" {
            if ev.mark_price.is_some() {
                self.health.last_mark_ts = ts;
            }
            if ev.funding_rate.is_some() {
                self.health.last_funding_ts = ts;
            }
        }

        if (stream.contains("openInterest") || event_type == "openInterest")
            && (ev.open_interest.is_some() || ev.qty.is_some())
        {
            self.health.last_oi_ts = ts;
        }
    }

    /// Update engine state with an incoming market event.
    /// This is the "hot path" — called for every event.
    pub fn update(&mut self, ev: &NormalizedMarketEvent) {
        let ts = ev.time_canonical;

        if self.next_emit_ts == 0 {
            // Floor-align the first emit timestamp to the interval for deterministic boundaries
            self.next_emit_ts = (ts / self.config.interval_ms) * self.config.interval_ms + self.config.interval_ms;
            info!("FeatureEngineV2 initialized: first_ts={}, next_emit_ts={}, interval={}", 
                ts, self.next_emit_ts, self.config.interval_ms);
        }

        self.record_feed_health(ev);

        match self.config.time_mode {
            TimeMode::EventTimeOnly => {
                self.apply_event(ev);
            }
            TimeMode::RecvTimeAware => {
                let (top_bids, top_asks) = (self.ob.top_bids(10), self.ob.top_asks(10));
                self.pending_queue.push_back(PendingEvent {
                    ev: ev.clone(),
                    top_bids,
                    top_asks,
                    ob_in_sync: self.ob.is_sync(),
                });
            }
        }

        // ── Internal Heartbeat removed from here to prevent maybe_emit starvation ──
    }

    fn apply_event(&mut self, ev: &NormalizedMarketEvent) {
        let ts = ev.time_canonical;
        let stream = ev.stream_name.as_str();
        let event_type = ev.event_type.as_str();

        // ─── AggTrade / Trade ───
        if stream.contains("aggTrade") || stream.contains("trade") || event_type == "trade" || event_type == "aggTrade" {
            if let (Some(price), Some(qty)) = (ev.price, ev.qty) {
                let is_taker_buy = match ev.side.as_deref() {
                    Some("Buy") | Some("buy") | Some("BUY") => true,
                    Some("Sell") | Some("sell") | Some("SELL") => false,
                    _ => {
                        !(ev.payload_json.contains("\"m\":true") || ev.payload_json.contains("\"m\": true"))
                    }
                };
                self.flow.record_trade(ts, qty, is_taker_buy);
                self.warmup.has_trades = true;
                self.health.last_trades_ts = ts;
            }
        }

        // ─── Always sync BBO if present (Robust Sink) ───
        if let (Some(bid), Some(ask)) = (ev.best_bid, ev.best_ask) {
            if bid > 0.0 && ask > 0.0 {
                self.current_best_bid = bid;
                self.current_best_ask = ask;
                self.warmup.has_bbo = true;
                if stream.contains("bookTicker") || event_type == "bookTicker" {
                    self.health.last_book_ts = ts;
                }
            }
        }

        // ─── BookTicker (OrderBook Seeding) ───
        if stream.contains("bookTicker") || event_type == "bookTicker" {
            #[derive(serde::Deserialize)]
            struct BinanceTicker {
                #[serde(alias = "u")]
                update_id: Option<i64>,
                #[serde(alias = "B")]
                bid_qty: Option<String>,
                #[serde(alias = "A")]
                ask_qty: Option<String>,
            }
            if let Ok(ticker) = serde_json::from_str::<BinanceTicker>(&ev.payload_json) {
                if let (Some(bq_str), Some(aq_str)) = (ticker.bid_qty, ticker.ask_qty) {
                    if let (Ok(bq), Ok(aq)) = (bq_str.parse::<f64>(), aq_str.parse::<f64>()) {
                        if let (Some(bid), Some(ask)) = (ev.best_bid, ev.best_ask) {
                            // Seed the orderbook with BBO if it's the only data we have (L1 fallback)
                            if self.ob.status == OrderBookStatus::Desynced || self.ob.status == OrderBookStatus::WaitingForSnapshot {
                                let update_id = ticker.update_id.unwrap_or(1);
                                if update_id >= 0 {
                                    use rust_decimal::Decimal;
                                    use rust_decimal::prelude::FromPrimitive;
                                    let b_dec = Decimal::from_f64(bid).unwrap_or_default();
                                    let a_dec = Decimal::from_f64(ask).unwrap_or_default();
                                    let bq_dec = Decimal::from_f64(bq).unwrap_or_default();
                                    let aq_dec = Decimal::from_f64(aq).unwrap_or_default();
                                    
                                    self.ob.apply_snapshot(update_id, vec![(b_dec, bq_dec)], vec![(a_dec, aq_dec)]);
                                }
                            }
                        }
                    }
                }
            }
        }

        // ─── DepthUpdate (orderbook deltas) ───
        if stream.contains("depth") || event_type == "depthUpdate" {
            // Extract deltas from payload_json
            #[derive(serde::Deserialize)]
            struct BinanceDepthUpdate {
                #[serde(alias = "U")]
                first_update_id: i64,
                #[serde(alias = "u")]
                final_update_id: i64,
                #[serde(alias = "pu")]
                prev_update_id: i64,
                #[serde(alias = "b")]
                bids: Vec<[String; 2]>,
                #[serde(alias = "a")]
                asks: Vec<[String; 2]>,
            }

            if let Ok(update) = serde_json::from_str::<BinanceDepthUpdate>(&ev.payload_json) {
                // Convert strings to Decimal
                use rust_decimal::Decimal;
                use std::str::FromStr;
                
                let mut bids = Vec::new();
                for b in update.bids {
                    if let (Ok(p), Ok(q)) = (Decimal::from_str(&b[0]), Decimal::from_str(&b[1])) {
                        bids.push((p, q));
                    }
                }
                let mut asks = Vec::new();
                for a in update.asks {
                    if let (Ok(p), Ok(q)) = (Decimal::from_str(&a[0]), Decimal::from_str(&a[1])) {
                        asks.push((p, q));
                    }
                }
                
                self.ob.apply_delta(update.first_update_id, update.final_update_id, update.prev_update_id, bids, asks);
                self.warmup.orderbook_in_sync = self.ob.is_sync();
            } else {
                // Fallback for partialDepth snapshots or differnet formats
                debug!("Failed to parse depth update payload for {}", ev.symbol);
            }
        }

        // ─── MarkPrice / FundingRate ───
        if stream.contains("markPrice") || event_type == "markPrice" {
            if let Some(mp) = ev.mark_price {
                self.shocks.update_mark_price(mp);
                self.health.last_mark_ts = ts;
            }
            if let Some(fr) = ev.funding_rate {
                self.shocks.update_funding_rate(fr);
                self.health.last_funding_ts = ts;
            }
        }

        // ─── Liquidation ───
        if stream.contains("forceOrder") || event_type == "liquidation" || event_type == "forceOrder" {
            if let (Some(price), Some(qty)) = (ev.liquidation_price.or(ev.price), ev.liquidation_qty.or(ev.qty)) {
                let is_buy = matches!(ev.side.as_deref(), Some("Buy") | Some("buy"));
                self.shocks.record_liquidation(ts, qty * price, is_buy);
            }
        }

        // ─── Open Interest ───
        if stream.contains("openInterest") || event_type == "openInterest" {
            // In normalized parquet, OI is often in the qty field
            if let Some(oi) = ev.open_interest.or(ev.qty) {
                self.oi.update(ts, oi);
                self.warmup.has_oi = true;
                self.health.last_oi_ts = ts;
            }
        }
    }

    /// Update orderbook levels manually (useful for testing or snapshots).
    pub fn set_orderbook_levels(&mut self, top_bids: Vec<(f64, f64)>, top_asks: Vec<(f64, f64)>) {
        use rust_decimal::Decimal;
        use rust_decimal::prelude::FromPrimitive;
        
        let bids = top_bids.iter()
            .map(|(p, q)| (Decimal::from_f64(*p).unwrap_or_default(), Decimal::from_f64(*q).unwrap_or_default()))
            .collect();
        let asks = top_asks.iter()
            .map(|(p, q)| (Decimal::from_f64(*p).unwrap_or_default(), Decimal::from_f64(*q).unwrap_or_default()))
            .collect();
            
        self.ob.apply_snapshot(0, bids, asks);
    }

    /// Update orderbook sync status (must be InSync for features to be emitted).
    pub fn set_orderbook_in_sync(&mut self, in_sync: bool) {
        if in_sync {
            self.ob.status = OrderBookStatus::InSync;
        } else {
            self.ob.mark_desynced();
        }
        self.warmup.orderbook_in_sync = in_sync;
    }

    /// Advances historical states (RSI, Bollinger, Slopes) to a specific timestamp.
    /// This is called automatically by update() and maybe_emit().
    fn internal_advance(&mut self, t_emit: i64) {
        if t_emit <= self.last_state_advance_ts {
            return;
        }

        let mid = (self.current_best_bid + self.current_best_ask) / 2.0;
        if mid > 0.0 {
            let spread_abs = self.current_best_ask - self.current_best_bid;
            let spread_bps = spread_abs / mid * 10_000.0;

            // 1. Update Technical Indicators (RSI, BB)
            self.tech.update(t_emit, mid);

            // 2. Update Price State (History, Ewma)
            self.price.update_state(mid, spread_bps);
        }

        self.last_state_advance_ts = t_emit;

        // Progress logging (every 1 hour of simulation time at 1s heartbeat)
        if self.last_state_advance_ts % 3_600_000 == 0 {
             info!("FeatureEngine Heartbeat: ts={}, mid={:.2}, candles={}", 
                t_emit, mid, self.tech.h1m.candle_count());
        }

        // Sync telemetry state with current buffers
        if self.config.telemetry_enabled {
            self.warmup.h1m_candles = self.tech.h1m.candle_count();
            self.warmup.h5m_candles = self.tech.h5m.candle_count();
            self.warmup.h15m_candles = self.tech.h15m.candle_count();
            self.warmup.mid_history_len = self.price.mid_count();
        }
    }

    /// Update account state (position, PnL, drawdown) for Group G features.
    pub fn set_account_state(&mut self, state: AccountState) {
        self.account = state;
    }

    /// Try to emit a feature row. Returns Some if current_ts >= next_emit_ts.
    ///
    /// ## No-Lookahead Guarantee
    /// All sub-modules compute features using only data from events
    /// that arrived BEFORE this call (via update()).
    pub fn maybe_emit(&mut self, current_processing_ts: i64) -> Option<FeatureRow> {
        if self.next_emit_ts == 0 || current_processing_ts < self.next_emit_ts {
            return None;
        }

        let t_emit = self.next_emit_ts;
        
        // Drain pending queue up to cutoff if RecvTimeAware
        if self.config.time_mode == TimeMode::RecvTimeAware {
            let cutoff = t_emit + self.config.recv_time_lag_ms;
            while let Some(front) = self.pending_queue.front() {
                let recv_t = front.ev.recv_time.unwrap_or(front.ev.time_canonical);
                if recv_t <= cutoff {
                    let pending = self.pending_queue.pop_front().unwrap();
                    self.warmup.orderbook_in_sync = pending.ob_in_sync;
                    self.apply_event(&pending.ev);
                } else {
                    break;
                }
            }
        }

        self.next_emit_ts += self.config.interval_ms;

        // Readiness check: need BBO at minimum
        if !self.warmup.has_bbo || self.current_best_bid <= 0.0 || self.current_best_ask <= 0.0 {
            if self.warmup.emit_count % 1000 == 0 || self.warmup.emit_count < 10 {
                 info!("FeatureEngineV2 not ready to emit at {}: has_bbo={}, bid={}, ask={}", 
                    t_emit, self.warmup.has_bbo, self.current_best_bid, self.current_best_ask);
            }
            return None;
        }

        // Spread validity
        if self.current_best_ask < self.current_best_bid {
            warn!("Crossed book at {}: ask={} < bid={}", t_emit, self.current_best_ask, self.current_best_bid);
            return None;
        }

        self.warmup.record_emit();

        // ── Real L2 Levels ──
        let top_bids = self.ob.top_bids(10);
        let top_asks = self.ob.top_asks(10);

        // ── Advance State for this emit window ──
        self.internal_advance(t_emit);

        // ── Compute all feature groups ──
        let pf = self.price.compute(self.current_best_bid, self.current_best_ask);
        let ff = self.flow.compute(t_emit, self.config.tape_zscore_clamp);
        let strict_in_sync = !self.config.micro_strict || self.ob.is_sync();
        let mf = self.micro.compute(&top_bids, &top_asks, pf.mid_price, strict_in_sync);
        let sf = self.shocks.compute(t_emit, pf.mid_price);
        let tf = self.tech.compute(pf.mid_price);
        let tmf = compute_time::compute_time_features(t_emit);
        let af = self.account.to_features();
        let oif = self.oi.compute(t_emit);

        // ── Sprint 2: Absorption → Persistence → Regime ──
        let tape_trades_5s = self.flow.trade_count_5s(t_emit);
        let absf = self.absorption.compute(
            &pf, &ff, &mf,
            self.warmup.orderbook_in_sync,
            &self.config.absorption_thresholds,
            tape_trades_5s,
        );
        let perf = self.persistence.compute(
            mf.obi_top1,
            ff.trade_imbalance_5s,
            pf.spread_vs_baseline,
            mf.depth_change_bid_1s,
            mf.depth_change_ask_1s,
            &self.config.persistence_thresholds,
        );
        let regime_inputs = compute_regime::RegimeInputs {
            flow_persistence_buy: perf.flow_persistence_buy,
            flow_persistence_sell: perf.flow_persistence_sell,
            slope_mid_5s: pf.slope_mid_5s,
            slope_mid_5m: pf.slope_mid_5m,
            slope_mid_15m: pf.slope_mid_15m,
            slope_mid_1h: pf.slope_mid_1h,
            microprice_confirmation: absf.microprice_confirmation_5s,
            breakout_failure: absf.breakout_failure_5s,
            spread_vs_baseline: pf.spread_vs_baseline,
            rv_5s: pf.rv_5s,
            rv_15m: pf.rv_15m,
            rv_1h: pf.rv_1h,
            tape_intensity_z: ff.tape_intensity_z,
            liq_count_30s: sf.liq_count_30s,
            tape_trades_1s: ff.tape_trades_1s,
            trade_imbalance_5s: ff.trade_imbalance_5s,
            ret_5m: pf.ret_5m,
            ret_15m: pf.ret_15m,
            ret_1h: pf.ret_1h,
            range_pos_5m: pf.range_pos_5m,
            range_pos_15m: pf.range_pos_15m,
            range_pos_1h: pf.range_pos_1h,
        };
        let regf = compute_regime::classify(
            &mut self.regime,
            &regime_inputs,
            &self.config.regime_thresholds,
        );

        // ── Assemble FeatureRow ──
        let row = FeatureRow {
            symbol: self.config.symbol.clone(),
            t_emit,

            // A) Price/Spread
            mid_price: Some(pf.mid_price),
            spread_abs: Some(pf.spread_abs),
            spread_bps: Some(pf.spread_bps),
            spread_vs_baseline: pf.spread_vs_baseline,

            // B) Returns & Volatility
            ret_1s: pf.ret_1s,
            ret_3s: pf.ret_3s,
            ret_5s: pf.ret_5s,
            ret_10s: pf.ret_10s,
            ret_30s: pf.ret_30s,
            rv_5s: pf.rv_5s,
            rv_30s: pf.rv_30s,
            rv_5m: pf.rv_5m,
            slope_mid_5s: pf.slope_mid_5s,
            slope_mid_15s: pf.slope_mid_15s,
            slope_mid_60s: pf.slope_mid_60s,
            slope_mid_5m: pf.slope_mid_5m,
            slope_mid_15m: pf.slope_mid_15m,

            // C) Taker Flow
            taker_buy_vol_1s: Some(ff.taker_buy_vol_1s),
            taker_sell_vol_1s: Some(ff.taker_sell_vol_1s),
            taker_buy_vol_5s: Some(ff.taker_buy_vol_5s),
            taker_sell_vol_5s: Some(ff.taker_sell_vol_5s),
            tape_trades_1s: Some(ff.tape_trades_1s),
            tape_intensity_z: ff.tape_intensity_z,
            trade_imbalance_1s: ff.trade_imbalance_1s,
            trade_imbalance_5s: ff.trade_imbalance_5s,
            trade_imbalance_15s: ff.trade_imbalance_15s,
            tape_intensity_5s_z: ff.tape_intensity_5s_z,

            // D) Microstructure
            obi_top1: mf.obi_top1,
            obi_top3: mf.obi_top3,
            obi_top10: mf.obi_top10,
            microprice: mf.microprice,
            microprice_minus_mid_bps: mf.microprice_minus_mid_bps,
            obi_delta_5s: mf.obi_delta_5s,
            delta_obi_top1_1s: mf.delta_obi_top1_1s,
            delta_microprice_1s: mf.delta_microprice_1s,
            depth_bid_top5: mf.depth_bid_top5,
            depth_ask_top5: mf.depth_ask_top5,
            depth_imbalance_top5: mf.depth_imbalance_top5,
            depth_change_bid_1s: mf.depth_change_bid_1s,
            depth_change_ask_1s: mf.depth_change_ask_1s,

            // E) Shocks
            liq_buy_vol_30s: Some(sf.liq_buy_vol_30s),
            liq_sell_vol_30s: Some(sf.liq_sell_vol_30s),
            liq_net_30s: Some(sf.liq_net_30s),
            liq_count_30s: Some(sf.liq_count_30s),
            mark_minus_mid_bps: sf.mark_minus_mid_bps,
            funding_rate: sf.funding_rate,
            funding_zscore: sf.funding_zscore,

            // F) Technicals
            ema200_distance_pct: tf.ema200_distance_pct,
            rsi_1m: tf.rsi_1m,
            bb_width_1m: tf.bb_width_1m,
            bb_pos_1m: tf.bb_pos_1m,
            rsi_5m: tf.rsi_5m,
            bb_width_5m: tf.bb_width_5m,
            bb_pos_5m: tf.bb_pos_5m,
            rsi_15m: tf.rsi_15m,
            bb_width_15m: tf.bb_width_15m,
            bb_pos_15m: tf.bb_pos_15m,

            // G) Account State
            position_flag: Some(af.position_flag),
            latent_pnl_pct: Some(af.latent_pnl_pct),
            max_pnl_pct: Some(af.max_pnl_pct),
            current_drawdown_pct: Some(af.current_drawdown_pct),

            // H) Time
            time_sin: Some(tmf.time_sin),
            time_cos: Some(tmf.time_cos),

            // I) Open Interest
            oi_value: oif.oi_value,
            oi_delta_30s: oif.oi_delta_30s,
            oi_delta_1m: oif.oi_delta_1m,
            oi_delta_5m: oif.oi_delta_5m,
            oi_zscore_30m: oif.oi_zscore_30m,

            // J) Absorption (Sprint 2)
            price_response_buy_5s: absf.price_response_buy_5s,
            price_response_sell_5s: absf.price_response_sell_5s,
            microprice_confirmation_5s: absf.microprice_confirmation_5s,
            breakout_failure_5s: absf.breakout_failure_5s,

            // K) Persistence (Sprint 2)
            obi_persistence_buy: perf.obi_persistence_buy,
            obi_persistence_sell: perf.obi_persistence_sell,
            flow_persistence_buy: perf.flow_persistence_buy,
            flow_persistence_sell: perf.flow_persistence_sell,
            spread_deterioration: perf.spread_deterioration,
            depth_deterioration_bid: perf.depth_deterioration_bid,
            depth_deterioration_ask: perf.depth_deterioration_ask,

            // L) Regime (Sprint 2)
            regime_trend: Some(regf.regime_trend),
            regime_range: Some(regf.regime_range),
            regime_shock: Some(regf.regime_shock),
            regime_dead: Some(regf.regime_dead),

            // M) Multi-Timeframe Context
            ret_5m: pf.ret_5m,
            ret_15m: pf.ret_15m,
            ret_1h: pf.ret_1h,
            rv_15m: pf.rv_15m,
            rv_1h: pf.rv_1h,
            slope_mid_1h: pf.slope_mid_1h,
            range_pos_5m: pf.range_pos_5m,
            range_pos_15m: pf.range_pos_15m,
            range_pos_1h: pf.range_pos_1h,
            context_regime_trend: Some(regf.context_regime_trend),
            context_regime_range: Some(regf.context_regime_range),
            context_regime_shock: Some(regf.context_regime_shock),
            context_regime_dead: Some(regf.context_regime_dead),
            trend_bias_5m: Some(regf.trend_bias_5m),
            trend_bias_15m: Some(regf.trend_bias_15m),
            trend_bias_1h: Some(regf.trend_bias_1h),
            trend_alignment: Some(regf.trend_alignment),
        };

        if self.config.telemetry_enabled {
            let (obs, clamped) = row.to_obs_vec();
            let n = FeatureRow::OBS_DIM / 2;
            let values = obs[0..n].to_vec();
            let masks = obs[n..FeatureRow::OBS_DIM].to_vec();
            
            // Update warmup audit counts (already done in internal_advance, but for safety)
            self.health.ingest(t_emit, values, masks, clamped);
        }

        Some(row)
    }

    /// Advances the internal engine state by computing features internally and discarding them.
    /// This is strictly used during the high-speed dataset pre-roll so that all internal 
    /// buffers (Flow, Micro, Shocks, etc.) are correctly warmed up without returning FeatureRows.
    pub fn tick_preroll(&mut self, current_processing_ts: i64) {
        while self.next_emit_ts > 0 && current_processing_ts >= self.next_emit_ts {
            let _ = self.maybe_emit(current_processing_ts);
        }
    }

    /// Returns the current health snapshot for telemetry.
    pub fn get_health_report(&self, current_ts: i64) -> health::FeatureHealthReport {
        self.health.snapshot(current_ts)
    }

    /// Returns a detailed warmup audit.
    pub fn get_warmup_audit(&self) -> warmup::WarmupTracker {
        self.warmup.clone()
    }

    /// Returns true when minimum data requirements are met for trading.
    pub fn warmup_ready(&self) -> bool {
        self.warmup.is_ready()
    }

    /// Returns the current mid price (for sizing, risk checks, etc.)
    pub fn current_mid_price(&self) -> Option<f64> {
        self.price.current_mid()
    }

    /// Returns the warmup tracker state for diagnostics.
    pub fn warmup_state(&self) -> &WarmupTracker {
        &self.warmup
    }

    /// Returns the number of features in the observation vector.
    pub fn obs_dim(&self) -> usize {
        FeatureRow::OBS_DIM
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::normalization::schema::NormalizedMarketEvent;

    fn make_bookticker(ts: i64, bid: f64, ask: f64) -> NormalizedMarketEvent {
        NormalizedMarketEvent {
            time_canonical: ts,
            stream_name: "bookTicker".to_string(),
            event_type: "bookTicker".to_string(),
            best_bid: Some(bid),
            best_ask: Some(ask),
            payload_json: format!("{{\"b\":\"{}\",\"a\":\"{}\",\"B\":\"1.0\",\"A\":\"1.0\"}}", bid, ask),
            ..Default::default()
        }
    }

    fn make_aggtrade(ts: i64, price: f64, qty: f64, is_buyer_maker: bool) -> NormalizedMarketEvent {
        NormalizedMarketEvent {
            time_canonical: ts,
            stream_name: "aggTrade".to_string(),
            event_type: "trade".to_string(),
            price: Some(price),
            qty: Some(qty),
            payload_json: format!("{{\"m\":{}}}", is_buyer_maker),
            ..Default::default()
        }
    }

    #[test]
    fn test_basic_emission() {
        let config = FeatureEngineV2Config {
            interval_ms: 1000,
            symbol: "BTCUSDT".to_string(),
            ..Default::default()
        };
        let mut engine = FeatureEngineV2::new(config);
        engine.set_orderbook_levels(
            vec![(50000.0, 1.0)],
            vec![(50010.0, 1.0)],
        );
        engine.set_orderbook_in_sync(true);

        // First event initializes timing
        let ev1 = make_bookticker(1000, 50000.0, 50010.0);
        engine.update(&ev1);

        // Not yet time to emit
        assert!(engine.maybe_emit(1500).is_none());

        // At t=2000 we should emit
        let ev2 = make_bookticker(1999, 50001.0, 50011.0);
        engine.update(&ev2);
        let row = engine.maybe_emit(2000);
        assert!(row.is_some());
        let row = row.unwrap();
        assert!(row.mid_price.is_some());
        assert!((row.mid_price.unwrap() - 50006.0).abs() < 1.0);
    }

    #[test]
    fn test_determinism() {
        let run = || {
            let config = FeatureEngineV2Config {
                interval_ms: 1000,
                symbol: "TEST".to_string(),
                ..Default::default()
            };
            let mut engine = FeatureEngineV2::new(config);
            engine.set_orderbook_levels(vec![(100.0, 5.0)], vec![(101.0, 5.0)]);
            engine.set_orderbook_in_sync(true);

            let events = vec![
                make_bookticker(100, 100.0, 101.0),
                make_aggtrade(200, 100.5, 0.1, false),
                make_aggtrade(500, 100.6, 0.2, true),
                make_bookticker(800, 100.1, 101.1),
                make_bookticker(1100, 100.2, 101.2),
            ];

            for ev in &events {
                engine.update(ev);
            }

            engine.maybe_emit(1100)
        };

        let row1 = run();
        let row2 = run();
        assert!(row1.is_some());
        assert_eq!(
            format!("{:?}", row1.unwrap()),
            format!("{:?}", row2.unwrap()),
            "Two runs must produce identical features"
        );
    }

    #[test]
    fn test_no_emit_without_bbo() {
        let config = FeatureEngineV2Config::default();
        let mut engine = FeatureEngineV2::new(config);
        engine.set_orderbook_in_sync(true);

        // Only trades, no BBO
        let ev = make_aggtrade(1000, 50000.0, 0.1, true);
        engine.update(&ev);

        let row = engine.maybe_emit(2000);
        assert!(row.is_none(), "Should not emit without BBO data");
    }

    #[test]
    fn test_obs_vec_dimension() {
        let config = FeatureEngineV2Config {
            interval_ms: 1000,
            symbol: "TEST".to_string(),
            ..Default::default()
        };
        let mut engine = FeatureEngineV2::new(config);
        engine.set_orderbook_levels(vec![(100.0, 5.0)], vec![(101.0, 5.0)]);
        engine.set_orderbook_in_sync(true);

        let ev = make_bookticker(100, 100.0, 101.0);
        engine.update(&ev);

        let row = engine.maybe_emit(1100).unwrap();
        let (obs, _clamped) = row.to_obs_vec();
        assert_eq!(obs.len(), FeatureRow::OBS_DIM);
    }

    #[test]
    fn test_recv_time_aware_anti_leakage() {
        let mut config = FeatureEngineV2Config::default();
        config.time_mode = TimeMode::RecvTimeAware;
        config.recv_time_lag_ms = 50; // allow 50ms processing delay

        let mut engine = FeatureEngineV2::new(config);
        engine.set_orderbook_levels(vec![(100.0, 5.0)], vec![(101.0, 5.0)]);
        engine.set_orderbook_in_sync(true);

        // First event establishes BBO at t=100
        let mut ev1 = make_bookticker(100, 100.0, 101.0);
        ev1.recv_time = Some(110);
        engine.update(&ev1);

        // A trade happens at t=1900, but is delayed heavily in network, arriving at t=2120.
        // It belongs to the [1100, 2100] emit window. Use is_buyer_maker=false to mark it as Taker BUY.
        let mut ev2 = make_aggtrade(1900, 100.5, 10.0, false);
        ev2.recv_time = Some(2120);
        engine.update(&ev2);

        // Emit at t=1100. 
        let row1 = engine.maybe_emit(1100).unwrap();
        
        // Assert trade is NOT in this feature row (taker flow is 0.0)
        assert_eq!(row1.taker_buy_vol_1s.unwrap(), 0.0);
        assert_eq!(row1.tape_trades_1s.unwrap(), 0.0);

        // Now move forward. The next emit is at t=2100. By then, the cutoff is 2150.
        // The event arrived at 2120, so it will be included in the NEXT window (1100 -> 2100).
        let row2 = engine.maybe_emit(2100).unwrap();
        assert!(row2.taker_buy_vol_1s.is_some());
        assert_eq!(row2.taker_buy_vol_1s.unwrap(), 10.0);
    }

    #[test]
    fn test_recv_time_aware_feed_health_updates_before_emit() {
        let mut config = FeatureEngineV2Config::default();
        config.time_mode = TimeMode::RecvTimeAware;
        config.recv_time_lag_ms = 50;

        let mut engine = FeatureEngineV2::new(config);
        engine.set_orderbook_levels(vec![(100.0, 5.0)], vec![(101.0, 5.0)]);
        engine.set_orderbook_in_sync(true);

        let mut bbo = make_bookticker(1000, 100.0, 101.0);
        bbo.recv_time = Some(1010);
        engine.update(&bbo);

        let mut trade = make_aggtrade(1020, 100.5, 2.0, false);
        trade.recv_time = Some(1030);
        engine.update(&trade);

        let report = engine.get_health_report(1030);
        assert_eq!(report.book_age_ms, 30);
        assert_eq!(report.trades_age_ms, 10);
        assert_eq!(report.health_state, "NORMAL");
    }

    #[test]
    fn test_micro_strict_gating() {
        let mut config = FeatureEngineV2Config::default();
        config.micro_strict = true;

        let mut engine = FeatureEngineV2::new(config);
        
        // 1. Fully Synced
        engine.set_orderbook_levels(vec![(100.0, 5.0)], vec![(101.0, 5.0)]);
        engine.set_orderbook_in_sync(true);
        engine.update(&make_bookticker(100, 100.0, 101.0));
        
        let row1 = engine.maybe_emit(1100).unwrap();
        assert!(row1.obi_top1.is_some(), "Microfeatures should exist when synced");
        assert!(row1.mid_price.is_some(), "Price features should exist");

        // 2. OrderBook Gap -> Desynced
        engine.set_orderbook_in_sync(false);
        // We still receive bookTickers via WS which keeps BBO updated!
        engine.update(&make_bookticker(1200, 100.5, 101.5));
        
        let row2 = engine.maybe_emit(2100).unwrap();
        assert!(row2.obi_top1.is_none(), "Microfeatures MUST be None when desynced");
        assert!(row2.microprice.is_none(), "Microfeatures MUST be None when desynced");
        assert!(row2.mid_price.is_some(), "Price features should still exist based on bookTicker");
        assert_eq!(row2.mid_price.unwrap(), 101.0, "Mid price should be updated");

        // 3. Resynced
        engine.set_orderbook_levels(vec![(100.5, 10.0)], vec![(101.5, 10.0)]);
        engine.set_orderbook_in_sync(true);
        engine.update(&make_bookticker(2200, 100.5, 101.5));

        let row3 = engine.maybe_emit(3100).unwrap();
        assert!(row3.obi_top1.is_some(), "Microfeatures should recover when resynced");
        assert_eq!(row3.obi_top1.unwrap(), 0.0); // (10-10)/(10+10) = 0
    }

    #[test]
    fn test_golden_feature_determinism() {
        // Golden test ensures that specific sequences of identical events
        // always result in bit-for-bit identical feature rows and vectors.
        // This stops subtle floating-point accumulative drift or "off by one" leakage.
        
        let config = FeatureEngineV2Config {
            interval_ms: 1000,
            symbol: "GOLDEN_BTC".to_string(),
            ..Default::default()
        };
        let mut engine = FeatureEngineV2::new(config);
        
        engine.set_orderbook_levels(vec![(50000.0, 10.0)], vec![(50050.0, 10.0)]);
        engine.set_orderbook_in_sync(true);

        engine.update(&make_bookticker(1000, 50000.0, 50050.0));
        engine.update(&make_aggtrade(1200, 50025.0, 0.5, true));
        engine.update(&make_aggtrade(1500, 50025.0, 0.5, false));
        engine.update(&make_bookticker(1800, 50005.0, 50045.0));
        
        // Exact state expected
        let row = engine.maybe_emit(2000).expect("Must emit golden row");
        let (obs, _clamped) = row.to_obs_vec();
        
        // Ensure vector is fully formed and deterministic despite Nones
        assert_eq!(obs.len(), FeatureRow::OBS_DIM);
        
        // Re-run and compare identical output (Determinism Core Policy)
        let mut engine2 = FeatureEngineV2::new(FeatureEngineV2Config {
            interval_ms: 1000,
            symbol: "GOLDEN_BTC".to_string(),
            ..Default::default()
        });
        engine2.set_orderbook_levels(vec![(50000.0, 10.0)], vec![(50050.0, 10.0)]);
        engine2.set_orderbook_in_sync(true);
        engine2.update(&make_bookticker(1000, 50000.0, 50050.0));
        engine2.update(&make_aggtrade(1200, 50025.0, 0.5, true));
        engine2.update(&make_aggtrade(1500, 50025.0, 0.5, false));
        engine2.update(&make_bookticker(1800, 50005.0, 50045.0));

        let row2 = engine2.maybe_emit(2000).expect("Must emit golden row 2");
        let (obs2, _clamped2) = row2.to_obs_vec();
        
        for i in 0..obs.len() {
            assert_eq!(obs[i], obs2[i], "Golden mismatch at index {}", i);
        }
    }

    #[test]
    fn test_obs_mask_behavior() {
        let mut row = FeatureRow::new("TEST".to_string(), 1000);
        
        // Set some features directly
        row.mid_price = Some(50000.0);
        row.spread_abs = None; // Should result in value=0.0, mask=0.0
        row.ret_1s = Some(0.001);
        row.rsi_14 = None; // Should result in value=50.0 (fallback), mask=0.0
        row.oi_delta_5m = Some(0.5);
        
        let (obs, _clamped) = row.to_obs_vec();
        
        // Total dim must be 148 (74 values + 74 masks)
        assert_eq!(obs.len(), FeatureRow::OBS_DIM);
        
        let n = FeatureRow::OBS_DIM / 2; // 74
        
        // Index 0: mid_price
        assert_eq!(obs[0], 50000.0);
        assert_eq!(obs[n + 0], 1.0); // mask for mid_price
        
        // Index 1: spread_abs (None → value=0, mask=0)
        assert_eq!(obs[1], 0.0);
        assert_eq!(obs[n + 1], 0.0);
        
        // Index 4: ret_1s (A=4 fields, so ret_1s is at idx 4)
        assert_eq!(obs[4], 0.001);
        assert_eq!(obs[n + 4], 1.0);
        
        // rsi_14: A(4) + B(10) + C(10) + D(13) + E(7) + F(ema200=0, rsi=1) = idx 45
        assert_eq!(obs[45], 50.0); // Fallback applied
        assert_eq!(obs[n + 45], 0.0);  // But mask is 0

        // oi_delta_5m: I starts at 4+10+10+13+7+4+4+2 = 54, oi_delta_5m = I[3] = 57
        assert_eq!(obs[57], 0.5);
        assert_eq!(obs[n + 57], 1.0);
    }
}

