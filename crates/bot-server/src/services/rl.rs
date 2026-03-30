use bot_core::proto::rl_service_server::RlService;
use bot_core::proto::{
    ResetRequest, ResetResponse,
    StepRequest, StepResponse,
    EnvInfoRequest, EnvInfoResponse,
    Observation, EnvState, StepInfo,
    ActionType, TradeFill,
    RlConfig, FeatureHealth,
};
use bot_data::replay::engine::ReplayEngine;
use bot_data::replay::types::ReplayConfig;
use bot_data::features_v2::FeatureEngineV2;
use bot_data::features_v2::FeatureEngineV2Config;
use bot_data::features_v2::schema::FeatureRow;
use bot_data::normalization::schema::TimeMode;
use bot_data::simulation::execution::ExecutionEngine;
use bot_data::simulation::structs::{ExecutionConfig, Side, OrderType};
use bot_data::normalization::schema::NormalizedMarketEvent;
use bot_data::experience::reward::{RewardCalculator, RewardState, RewardConfig};

use tonic::{Request, Response, Status};
use tokio::sync::Mutex as TokioMutex;
use std::sync::{Arc, RwLock};
use std::collections::{HashMap, BTreeMap};
use std::path::PathBuf;
use std::str::FromStr;
use std::fs::File;
use std::io::BufReader;
use serde_json::Value;
use log::info;
use uuid::Uuid;
use rand::{Rng, SeedableRng};
use rand::rngs::StdRng;
use rust_decimal::Decimal;
use rust_decimal::prelude::{FromPrimitive, ToPrimitive};

// --- Constants ---
const OBS_DIM: usize = 148;
const ACTION_DIM: i32 = 10;

const ACTION_LABELS: [&str; 10] = [
    "HOLD", "OPEN_LONG", "ADD_LONG", "REDUCE_LONG", "CLOSE_LONG", 
    "OPEN_SHORT", "ADD_SHORT", "REDUCE_SHORT", "CLOSE_SHORT", "REPRICE",
];

// --- SimOrderBook for RL ---
struct SimOrderBook {
    bids: BTreeMap<Decimal, Decimal>,
    asks: BTreeMap<Decimal, Decimal>,
}

impl SimOrderBook {
    fn new() -> Self {
        Self { bids: BTreeMap::new(), asks: BTreeMap::new() }
    }
    fn apply_delta(&mut self, bids: &[[String; 2]], asks: &[[String; 2]]) {
        for b in bids {
            if let (Ok(p), Ok(q)) = (Decimal::from_str(&b[0]), Decimal::from_str(&b[1])) {
                if q.is_zero() { self.bids.remove(&p); } else { self.bids.insert(p, q); }
            }
        }
        for a in asks {
            if let (Ok(p), Ok(q)) = (Decimal::from_str(&a[0]), Decimal::from_str(&a[1])) {
                if q.is_zero() { self.asks.remove(&p); } else { self.asks.insert(p, q); }
            }
        }
    }
    fn update_bbo(&mut self, bid: f64, bq: f64, ask: f64, aq: f64) {
        if let (Some(bp), Some(bq_dec), Some(ap), Some(aq_dec)) = (
            Decimal::from_f64(bid), Decimal::from_f64(bq),
            Decimal::from_f64(ask), Decimal::from_f64(aq)
        ) {
            // For BBO updates, we want a clean slate to avoid stale price legacy
            self.bids.clear();
            self.asks.clear();
            self.bids.insert(bp, bq_dec);
            self.asks.insert(ap, aq_dec);
        }
    }
    fn top_bids(&self, n: usize) -> Vec<(f64, f64)> {
        self.bids.iter().rev().take(n)
            .map(|(p, q): (&Decimal, &Decimal)| (p.to_f64().unwrap_or(0.0), q.to_f64().unwrap_or(0.0)))
            .collect()
    }
    fn top_asks(&self, n: usize) -> Vec<(f64, f64)> {
        self.asks.iter().take(n)
            .map(|(p, q): (&Decimal, &Decimal)| (p.to_f64().unwrap_or(0.0), q.to_f64().unwrap_or(0.0)))
            .collect()
    }
}

// --- Episode Handle ---

struct EpisodeHandle {
    replay: ReplayEngine,
    feature_engine: FeatureEngineV2,
    exec_engine: ExecutionEngine,

    // Config
    symbol: String,
    // decision_interval_ms: i64, // Unused
    initial_equity: f64,
    max_pos_frac: f64,
    hard_disaster_dd: f64,
    max_daily_dd: f64,
    max_hold_ms: u64,
    end_ts: i64, // 0 = no limit

    // State tracking
    // prev_equity: f64, // Removed, handled by RewardState
    peak_equity: f64,
    step_count: u32,
    done: bool,
    last_obs: Vec<f32>,
    last_features: Option<FeatureRow>,
    last_tick_ts: i64,
    last_mid_price: f64,
    last_mark_price: f64,
    cancel_count_in_step: u32,
    reprice_count_in_step: u32,
    post_delta_threshold_bps: f64,
    prev_realized_pnl: f64,
    prev_exposure: f64,

    // Reward
    reward_state: RewardState,
    reward_config: RewardConfig,
    decision_interval_ms: u32,
    
    // vNext: Hard gate configs
    use_vnext_reward: bool,
    close_position_loss_threshold: f64,
    min_post_offset_bps: f64,
    imbalance_block_threshold: f64,
    profit_floor_bps: f64,
    stop_loss_bps: f64,
    // vNext: Gate telemetry (per-step counters)
    gate_close_blocked_in_step: u32,
    gate_offset_blocked_in_step: u32,
    gate_imbalance_blocked_in_step: u32,
    entry_veto_count_in_step: u32,
    exit_blocked_count: u32,
    exit_blocked_pnl_sum: f64,
    exit_blocked_1_to_4_count: u32,
    max_blocked_upnl_bps: f64, // peak uPnL during blocked exits for current trade
    opportunity_lost_count: u32,
    entry_veto_count: u32,
    
    // Lifecycle telemetry (Accumulated over episode)
    action_counts: HashMap<String, u32>,
    exit_distribution: HashMap<String, u32>,
    current_trade_start_ts: Option<i64>,
    win_count: u32,
    loss_count: u32,
    sum_win_hold_ms: u64,
    sum_loss_hold_ms: u64,
    realized_pnl_total: f64,
    
    // Selective Entry Gating (Offline/Training)
    use_selective_entry: bool,
    entry_veto_threshold_bps: f64,
    
    // OrderBook simulation for features
    orderbook: SimOrderBook,
}

impl EpisodeHandle {
    fn advance_to_next_tick(&mut self) -> (Option<FeatureRow>, bool) {
        loop {
            match self.replay.next_event() {
                Some(event) => {
                    // Update OrderBook (Depth/Ticker)
                    if event.event_type == "depthUpdate" || event.stream_name.contains("depth") {
                        #[derive(serde::Deserialize)]
                        struct DepthPay { 
                            #[serde(alias="b")] bids: Vec<[String; 2]>, 
                            #[serde(alias="a")] asks: Vec<[String; 2]> 
                        }
                        if let Some(ref json) = event.payload_json {
                            if let Ok(pay) = serde_json::from_str::<DepthPay>(json) {
                                self.orderbook.apply_delta(&pay.bids, &pay.asks);
                            }
                        }
                    } else if event.event_type == "bookTicker" || event.stream_name.contains("bookTicker") {
                        #[derive(serde::Deserialize)]
                        struct TickerPay { 
                            #[serde(alias="b")] b: String, #[serde(alias="B")] bq: String,
                            #[serde(alias="a")] a: String, #[serde(alias="A")] aq: String
                        }
                        if let Some(ref json) = event.payload_json {
                            if let Ok(pay) = serde_json::from_str::<TickerPay>(json) {
                                if let (Ok(bp), Ok(bq), Ok(ap), Ok(aq)) = (
                                    pay.b.parse::<f64>(), pay.bq.parse::<f64>(),
                                    pay.a.parse::<f64>(), pay.aq.parse::<f64>()
                                ) {
                                    self.orderbook.update_bbo(bp, bq, ap, aq);
                                }
                            }
                        }
                    }

                    // Convert ReplayEvent to NormalizedMarketEvent
                    let norm = NormalizedMarketEvent {
                        schema_version: 1,
                        run_id: String::new(),
                        exchange: "binance".to_string(),
                        market_type: "future".to_string(),
                        symbol: event.symbol.clone(),
                        stream_name: event.stream_name.clone(),
                        event_type: event.event_type.clone(),
                        time_exchange: event.ts_exchange,
                        time_local: event.ts_local,
                        time_canonical: event.ts_canonical,
                        recv_time: None,
                        price: event.price,
                        qty: event.quantity,
                        side: event.side.clone(),
                        best_bid: event.best_bid,
                        best_ask: event.best_ask,
                        mark_price: event.mark_price,
                        funding_rate: event.funding_rate,
                        liquidation_price: event.liquidation_price,
                        liquidation_qty: event.liquidation_qty,
                        update_id_first: None,
                        update_id_final: None,
                        update_id_prev: None,
                        payload_json: event.payload_json.unwrap_or_default(),
                        open_interest: event.open_interest,
                        open_interest_value: event.open_interest_value,
                    };

                    // Update mid/mark price tracking and propagate BBO to execution engine
                    if let (Some(b), Some(a)) = (norm.best_bid, norm.best_ask) {
                        self.last_mid_price = (b + a) / 2.0;
                        // Propagate 10-level book to execution engine.
                        // We do NOT manually seed the feature engine here to avoid 0-ID gaps; 
                        // the feature_engine's own internal logic handles synced L2.
                        let bids = self.orderbook.top_bids(10);
                        let asks = self.orderbook.top_asks(10);
                        if !bids.is_empty() && !asks.is_empty() {
                            self.exec_engine.set_book_levels(bids, asks);
                        } else {
                            // Fallback to 1-level in sim-engine if SimOrderBook not yet warm
                            self.exec_engine.set_book_levels(vec![(b, 1000.0)], vec![(a, 1000.0)]);
                        }
                    }
                    if let Some(p) = norm.price {
                        if p > 0.0 { self.last_mid_price = p; }
                    }
                    if self.last_mid_price == 0.0 {
                        // Hard fallback: use first mark price or a generic BTC price if nothing else
                        if let Some(mp) = norm.mark_price { self.last_mid_price = mp; }
                    }
                    if let Some(mp) = norm.mark_price {
                        self.last_mark_price = mp;
                    }

                    // Feed into execution engine (handles fills, PnL, risk)
                    self.exec_engine.update(&norm);

                    // Feed into feature engine
                    if self.step_count == 0 && self.last_tick_ts == 0 {
                         info!("EVENT TRACER: First event saw by RL loop at {}. type={}, stream={}", 
                            norm.time_canonical, norm.event_type, norm.stream_name);
                    }
                    self.feature_engine.update(&norm);

                    // Check if feature engine emits at this tick
                    if let Some(mut fv) = self.feature_engine.maybe_emit(norm.time_canonical) {
                        self.last_tick_ts = norm.time_canonical;
                        info!("EVENT TRACER: FIRST FEATURE EMITTED AT {}", self.last_tick_ts);
                        return (Some(fv), false);
                    }
                }
                None => {
                    // End of dataset
                    return (None, true);
                }
            }
        }
    }

    /// Build the full 148-float observation vector from features + portfolio context.
    fn build_obs(&mut self, fv: &mut FeatureRow) -> Vec<f32> {
        self.last_features = Some(fv.clone());
        let equity = self.exec_engine.portfolio.state.equity_usdt;

        // Portfolio context
        let pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let (is_long, is_short, _is_flat, pos_qty, entry_price, upnl) = match pos {
            Some(p) if p.qty > 1e-9 => {
                let long = if p.side == Side::Buy { 1.0f32 } else { 0.0 };
                let short = if p.side == Side::Sell { 1.0f32 } else { 0.0 };
                (long, short, 0.0f32, p.qty, p.entry_vwap, p.unrealized_pnl)
            }
            _ => (0.0, 0.0, 1.0, 0.0, 0.0, 0.0),
        };

        // We use upnl.max(0.0) as an approximation of max_pnl for training right now 
        let max_pnl = upnl.max(0.0);
        let _notional = pos_qty * entry_price;
        let pos_flag = is_long - is_short; // 1 for long, -1 for short, 0 for flat
        
        // Percentages relative to equity
        let latent_pnl_pct = if equity > 0.0 && upnl.is_finite() { (upnl / equity) * 100.0 } else { 0.0 };
        let max_pnl_pct = if equity > 0.0 && max_pnl.is_finite() { (max_pnl / equity) * 100.0 } else { 0.0 };
        let current_drawdown_pct = if max_pnl > upnl && equity > 0.0 { ((max_pnl - upnl) / equity) * 100.0 } else { 0.0 };

        fv.position_flag = Some(pos_flag as f64);
        fv.latent_pnl_pct = Some(latent_pnl_pct);
        fv.max_pnl_pct = Some(max_pnl_pct);
        fv.current_drawdown_pct = Some(current_drawdown_pct);

        let (obs, _) = fv.to_obs_vec();
        self.last_obs = obs.clone();
        obs
    }

    /// Build EnvState proto message from current portfolio state.
    fn build_env_state(&self) -> EnvState {
        let state = &self.exec_engine.portfolio.state;
        let pos = state.positions.get(&self.symbol);

        let (pos_qty, entry_price, upnl, rpnl, side_str) = match pos {
            Some(p) if p.qty > 1e-9 => {
                let side = match p.side {
                    Side::Buy => "LONG",
                    Side::Sell => "SHORT",
                };
                (p.qty, p.entry_vwap, p.unrealized_pnl, p.realized_pnl, side)
            }
            _ => (0.0, 0.0, 0.0, 0.0, "FLAT"),
        };

        let notional = pos_qty * entry_price;
        let leverage = if state.equity_usdt > 0.0 { notional / state.equity_usdt } else { 0.0 };

        EnvState {
            equity: state.equity_usdt,
            cash: state.cash_usdt,
            position_qty: if side_str == "SHORT" { -pos_qty } else { pos_qty },
            entry_price,
            unrealized_pnl: upnl,
            realized_pnl: rpnl,
            fees_paid: self.initial_equity - state.cash_usdt + rpnl - upnl,
            leverage,
            position_side: side_str.to_string(),
        }
    }
    
    /// Build FeatureHealth proto message for temporal audit.
    fn build_feature_health(&self) -> FeatureHealth {
        let health = self.feature_engine.get_health_report(self.last_tick_ts);
        FeatureHealth {
            book_age_ms: health.book_age_ms,
            trades_age_ms: health.trades_age_ms,
            mark_age_ms: health.mark_age_ms,
            funding_age_ms: health.funding_age_ms,
            oi_age_ms: health.oi_age_ms,
            obs_quality: health.obs_quality,
        }
    }

    /// Cancel all outstanding limit orders for a given side.
    fn cancel_side_orders(&mut self, side: Side) -> u32 {
        let mut cancelled = 0;
        let ids: Vec<String> = self.exec_engine.portfolio.state.active_orders.iter()
            .filter(|(_, o)| o.side == side && o.order_type == OrderType::Limit)
            .map(|(id, _)| id.clone())
            .collect();
        
        for id in ids {
            if self.exec_engine.cancel_order(&id) {
                cancelled += 1;
            }
        }
        cancelled
    }

    fn cancel_all_orders(&mut self) -> u32 {
        self.exec_engine.clear_all_orders()
    }

    /// Returns (number of trades executed, was_invalid_action).
    fn apply_action(&mut self, action: ActionType) -> (u32, bool) {
        self.exec_engine.clear_step_stats();
        self.cancel_count_in_step = 0;
        self.reprice_count_in_step = 0;
        
        let mid = self.last_mid_price;
        if mid <= 0.0 { return (0, false); }

        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let (has_pos, pos_side, pos_qty, _upnl) = match current_pos {
            Some(p) if p.qty > 1e-9 => (true, p.side, p.qty, p.unrealized_pnl),
            _ => (false, Side::Buy, 0.0, 0.0),
        };

        // --- Economic Floor Logic (Config-driven) ---
        let equity = self.exec_engine.portfolio.state.equity_usdt;
        let base_notional = self.max_pos_frac * equity;
        
        // ... sizing logic
        let f = self.last_features.clone().unwrap_or_default();
        let sho = f.regime_shock.unwrap_or(0.0);
        let dea = f.regime_dead.unwrap_or(0.0);
        let tre = f.regime_trend.unwrap_or(0.0);
        let ran = f.regime_range.unwrap_or(0.0);
        let spread_bps = f.spread_bps.unwrap_or(0.0);

        let regime_mult = if sho > tre && sho > ran && sho > dea { 0.30 } 
            else if dea > tre && dea > ran { 0.15 } // Loosened from 0.00 to allow expert trades in quiet markets
            else if ran > tre { 0.75 } else { 1.00 };

        let exec_qual_mult = if spread_bps > 25.0 { 0.25 } else if spread_bps > 15.0 { 0.50 } else { 1.00 };
        let mut target_notional = (base_notional * regime_mult * exec_qual_mult).clamp(0.0, 100000.0);
        if target_notional > 0.0 && target_notional < 15.0 { target_notional = 0.0; }
        let target_qty = if target_notional > 0.0 { target_notional / mid } else { 0.0 };

        match action {
            ActionType::Hold => (0, false),

            ActionType::OpenLong => {
                *self.action_counts.entry("OPEN_LONG".to_string()).or_insert(0) += 1;
                if has_pos { 
                    log::debug!("RL_OPEN_LONG: Already in position. Use ADD."); 
                    return (0, true); // INVALID: Open while already in pos
                }
                if self.use_selective_entry {
                    let micro_diff = self.last_features.as_ref().and_then(|f| f.microprice_minus_mid_bps).unwrap_or(0.0);
                    if micro_diff < -self.entry_veto_threshold_bps {
                        self.entry_veto_count_in_step += 1;
                        self.entry_veto_count += 1;
                        return (0, true); // Vetoed entry is now an invalid intent
                    }
                }
                (self.submit_passive_order(Side::Buy, target_qty), false)
            }
            ActionType::AddLong => {
                *self.action_counts.entry("ADD_LONG".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Side::Buy { return (0, true); }
                if self.use_selective_entry {
                    let micro_diff = self.last_features.as_ref().and_then(|f| f.microprice_minus_mid_bps).unwrap_or(0.0);
                    if micro_diff < -self.entry_veto_threshold_bps {
                        self.entry_veto_count_in_step += 1;
                        self.entry_veto_count += 1;
                        return (0, true); // Vetoed add is an invalid intent
                    }
                }
                let delta = (target_qty - pos_qty).max(0.0);
                ((if delta > 0.0 { self.submit_passive_order(Side::Buy, delta) } else { 0 }), false)
            }
            ActionType::ReduceLong => {
                *self.action_counts.entry("REDUCE_LONG".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Side::Buy { return (0, true); }
                (self.attempt_market_exit(Side::Sell, pos_qty * 0.5, self.profit_floor_bps, self.stop_loss_bps), false)
            }
            ActionType::CloseLong => {
                *self.action_counts.entry("CLOSE_LONG".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Side::Buy { return (0, true); }
                (self.attempt_market_exit(Side::Sell, pos_qty, self.profit_floor_bps, self.stop_loss_bps), false)
            }

            ActionType::OpenShort => {
                *self.action_counts.entry("OPEN_SHORT".to_string()).or_insert(0) += 1;
                if has_pos { return (0, true); }
                if self.use_selective_entry {
                    let micro_diff = self.last_features.as_ref().and_then(|f| f.microprice_minus_mid_bps).unwrap_or(0.0);
                    if micro_diff > self.entry_veto_threshold_bps {
                        self.entry_veto_count_in_step += 1;
                        self.entry_veto_count += 1;
                        return (0, true);
                    }
                }
                (self.submit_passive_order(Side::Sell, target_qty), false)
            }
            ActionType::AddShort => {
                *self.action_counts.entry("ADD_SHORT".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Side::Sell { return (0, true); }
                if self.use_selective_entry {
                    let micro_diff = self.last_features.as_ref().and_then(|f| f.microprice_minus_mid_bps).unwrap_or(0.0);
                    if micro_diff > self.entry_veto_threshold_bps {
                        self.entry_veto_count_in_step += 1;
                        self.entry_veto_count += 1;
                        return (0, true);
                    }
                }
                let delta = (target_qty - pos_qty).max(0.0);
                ((if delta > 0.0 { self.submit_passive_order(Side::Sell, delta) } else { 0 }), false)
            }
            ActionType::ReduceShort => {
                *self.action_counts.entry("REDUCE_SHORT".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Side::Sell { return (0, true); }
                (self.attempt_market_exit(Side::Buy, pos_qty * 0.5, self.profit_floor_bps, self.stop_loss_bps), false)
            }
            ActionType::CloseShort => {
                *self.action_counts.entry("CLOSE_SHORT".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Side::Sell { return (0, true); }
                (self.attempt_market_exit(Side::Buy, pos_qty, self.profit_floor_bps, self.stop_loss_bps), false)
            }

            ActionType::Reprice => {
                let cancelled = self.cancel_all_orders();
                self.cancel_count_in_step += cancelled;
                if has_pos {
                    let _ = self.submit_passive_order(pos_side, target_qty - pos_qty);
                    (0, false)
                } else {
                    (0, cancelled == 0) // INVALID if no pos AND nothing was cancelled
                }
            }
        }
    }

    fn submit_passive_order(&mut self, side: Side, qty: f64) -> u32 {
        if qty <= 1e-6 { return 0; }
        // vNext Gate: Imbalance block
        if let Some(ref f) = self.last_features {
            // vNext Gate: Selective Entry Veto
            if self.use_selective_entry {
                let mp_dist = f.microprice_minus_mid_bps.unwrap_or(0.0);
                if (side == Side::Buy && mp_dist < -self.entry_veto_threshold_bps) || 
                   (side == Side::Sell && mp_dist > self.entry_veto_threshold_bps) {
                    *self.action_counts.entry("ENTRY_VETO".to_string()).or_insert(0) += 1;
                    return 0;
                }
            }

            let imb = f.trade_imbalance_5s.unwrap_or(0.0);
            if (side == Side::Buy && imb < -self.imbalance_block_threshold) || 
               (side == Side::Sell && imb > self.imbalance_block_threshold) {
                self.gate_imbalance_blocked_in_step += 1;
                return 0;
            }
        }

        if let Some(price) = self.get_synthetic_passive_price(side) {
            // vNext Gate: Min offset
            let mid = self.last_mid_price;
            let offset_bps = (price - mid).abs() / mid * 10000.0;
            if offset_bps < self.min_post_offset_bps {
                self.gate_offset_blocked_in_step += 1;
                return 0;
            }

            self.exec_engine.submit_order(&self.symbol, side, price, qty, OrderType::Limit);
        }
        0
    }

    fn attempt_market_exit(&mut self, side: Side, qty: f64, profit_floor_bps: f64, stop_loss_bps: f64) -> u32 {
        let pos = match self.exec_engine.portfolio.state.positions.get(&self.symbol) {
            Some(p) => p,
            None => return 0,
        };
        
        let upnl_bps = pos.unrealized_pnl / self.exec_engine.portfolio.state.equity_usdt * 10000.0;
        let is_emergency = upnl_bps < -stop_loss_bps;
        let is_profitable = upnl_bps > profit_floor_bps;

        if is_emergency || is_profitable {
            let _ = self.cancel_all_orders();
            self.exec_engine.submit_order(&self.symbol, side, 0.0, qty, OrderType::Market);
            1
        } else {
            log::info!("RL_EXIT_BLOCKED: uPnL={:.1}bps (Floor={:.1}, SL={:.1})", upnl_bps, profit_floor_bps, -stop_loss_bps);
            self.gate_close_blocked_in_step += 1;
            self.exit_blocked_count += 1;
            self.exit_blocked_pnl_sum += upnl_bps;
            
            // New Telemetry: +1 to +4 bps zone
            if upnl_bps >= 1.0 && upnl_bps < 4.0 {
                self.exit_blocked_1_to_4_count += 1;
            }
            
            // Peak tracking for "Lost Opportunity"
            if upnl_bps > self.max_blocked_upnl_bps {
                self.max_blocked_upnl_bps = upnl_bps;
            }
            
            0
        }
    }

    fn compute_reward(&mut self, num_trades: u32, realized_pnl_step: f64, is_cancel_all: bool, is_taker_action: bool, active_order_count: u32, is_invalid: bool) -> f64 {
        let equity = self.exec_engine.portfolio.state.equity_usdt;
        let mid = self.last_mid_price;

        let micro_minus_mid = self.last_features.as_ref().and_then(|f| f.microprice_minus_mid_bps).unwrap_or(0.0);
        let imbalance = self.last_features.as_ref().and_then(|f| f.trade_imbalance_5s).unwrap_or(0.0);

        // Construct MakerFillDetail list (needed by both paths)
        let maker_fills: Vec<bot_data::experience::reward::MakerFillDetail> = self.exec_engine.last_fill_events.iter()
            .filter(|e| e.liquidity_flag == bot_data::simulation::structs::LiquidityFlag::Maker)
            .map(|e| bot_data::experience::reward::MakerFillDetail {
                side: if e.qty_filled > 0.0 { 1.0 } else { -1.0 },
            })
            .collect();

        let exposure = self.exec_engine.portfolio.state.positions.values()
            .map(|p| p.qty * mid)
            .sum::<f64>();

        let reward = if self.use_vnext_reward {
            // ── vNext: Simplified 4-term reward ──
            let fees_this_step: f64 = self.exec_engine.last_fill_events.iter()
                .map(|e| e.fee_paid.abs())
                .sum();

            RewardCalculator::compute_reward(
                &mut self.reward_state,
                equity,
                mid,
                self.decision_interval_ms,
                fees_this_step,
                exposure,
                &maker_fills,
                active_order_count,
                realized_pnl_step,
                is_taker_action,
                is_invalid,
                micro_minus_mid,
                imbalance,
                &self.reward_config,
            )
        } else {
            // ── Legacy 18-term reward (backward compat) ──
            let mut has_bid = false;
            let mut has_ask = false;
            for order in self.exec_engine.portfolio.state.active_orders.values() {
                if format!("{:?}", order.side) == "Buy" { has_bid = true; }
                if format!("{:?}", order.side) == "Sell" { has_ask = true; }
            }
            let is_two_sided = has_bid && has_ask;

            let num_toxic_fills = self.exec_engine.last_fill_events.iter()
                .filter(|f| f.is_toxic)
                .count() as u32;

            let tib_count = if mid > 0.0 && self.reward_config.tib_bonus > 0.0 {
                self.exec_engine.portfolio.state.active_orders.values()
                    .filter(|o| (o.price - mid).abs() / mid * 10000.0 < 20.0)
                    .count() as u32
            } else {
                0
            };

            let num_taker_fills = self.exec_engine.last_fill_events.iter()
                .filter(|e| e.liquidity_flag == bot_data::simulation::structs::LiquidityFlag::Taker)
                .count() as u32;

            let active_order_count = self.exec_engine.portfolio.state.active_orders.len() as u32;

            let distance_to_mid_bps = if mid > 0.0 && active_order_count > 0 {
                let sum_dist: f64 = self.exec_engine.portfolio.state.active_orders.values()
                    .map(|o| (o.price - mid).abs() / mid * 10000.0)
                    .sum();
                sum_dist / (active_order_count as f64)
            } else {
                0.0
            };

            RewardCalculator::compute_reward_legacy(
                &mut self.reward_state,
                equity,
                mid,
                self.decision_interval_ms,
                num_trades,
                num_toxic_fills,
                exposure,
                tib_count,
                &maker_fills,
                num_taker_fills,
                active_order_count,
                self.reprice_count_in_step,
                distance_to_mid_bps,
                realized_pnl_step,
                is_cancel_all,
                is_two_sided,
                is_taker_action,
                self.prev_exposure,
                micro_minus_mid,
                imbalance,
                &self.reward_config,
            )
        };

        self.prev_exposure = exposure;
        reward
    }

    // total_realized_pnl removed


    /// Check if episode should end.
    fn check_done(&self) -> (bool, &'static str) {
        let equity = self.exec_engine.portfolio.state.equity_usdt;

        // Hard disaster stop
        if self.hard_disaster_dd > 0.0 {
            let dd = (self.peak_equity - equity) / self.peak_equity;
            if dd >= self.hard_disaster_dd {
                return (true, "HARD_DISASTER_STOP");
            }
        }

        // Daily drawdown
        if self.max_daily_dd > 0.0 {
            let dd = (self.initial_equity - equity) / self.initial_equity;
            if dd >= self.max_daily_dd {
                return (true, "DAILY_DD_LIMIT");
            }
        }

        // Time limit (end_ts)
        if self.end_ts > 0 && self.last_tick_ts >= self.end_ts {
            return (true, "TIME_LIMIT_REACHED");
        }

        // Equity depleted
        if equity <= 0.0 {
            return (true, "BANKRUPT");
        }

        // Max hold time
        if self.max_hold_ms > 0 {
            if let Some(pos) = self.exec_engine.portfolio.state.positions.get(&self.symbol) {
                if pos.qty > 1e-9 {
                    let duration = self.last_tick_ts - pos.open_ts;
                    if duration >= self.max_hold_ms as i64 {
                         return (true, "MAX_HOLD_TIME");
                    }
                }
            }
        }

        (false, "NORMAL")
    }

    fn check_numeric_stability(&self) -> Option<String> {
        let state = &self.exec_engine.portfolio.state;
        if !state.equity_usdt.is_finite() { return Some(format!("Equity not finite: {}", state.equity_usdt)); }
        if !state.cash_usdt.is_finite() { return Some(format!("Cash not finite: {}", state.cash_usdt)); }
        if !self.last_mid_price.is_finite() { return Some(format!("Mid price not finite: {}", self.last_mid_price)); }
        None
    }

    fn get_synthetic_passive_price(&self, side: Side) -> Option<f64> {
        let mid = self.last_mid_price;
        if mid <= 0.0 { return None; }

        let f = match self.last_features.as_ref() {
            Some(f) => f,
            None => {
                log::warn!("RL_SYNTHETIC_PRICE: Missing last_features, cannot calculate price");
                return None;
            }
        };
        
        // Extract features
        let spread = f.spread_bps.unwrap_or(1.0).max(0.05);
        let vol = f.rv_5s.unwrap_or(0.2).max(0.0);
        let imbalance = f.trade_imbalance_5s.unwrap_or(0.0);

        // Adaptive Offset: D_bps = max(0.2, spread_bps * 0.5) + (1.5 * rv_5s) + Shift
        let mut offset_bps = (spread * 0.5).max(0.2) + (vol * 1.5);

        // Adverse selection shift: widen if flow is against us
        let side_mult = if side == Side::Buy { 1.0 } else { -1.0 };
        // If we Buy (1.0) and imbalance is -ve (selling pressure), side_mult*imb is -ve -> widen.
        if (imbalance * side_mult) < 0.0 {
            offset_bps += imbalance.abs() * vol * 2.0;
        }

        let price = match side {
            Side::Buy => mid * (1.0 - offset_bps / 10000.0),
            Side::Sell => mid * (1.0 + offset_bps / 10000.0),
        };

        if self.step_count % 100 == 0 {
            log::info!("RL_SYNTHETIC_PRICE: side={:?}, offset={:.2}bps, price={:.2}, mid={:.2}, vol={:.2}, imb={:.2}", 
                side, offset_bps, price, mid, vol, imbalance);
        }
            
        Some(price)
    }
}

// --- RL Service ---

pub struct RLServiceImpl {
    runs_dir: PathBuf,
    episodes: std::sync::RwLock<HashMap<String, Arc<TokioMutex<EpisodeHandle>>>>,
}

impl RLServiceImpl {
    pub fn new(runs_dir: PathBuf) -> Self {
        Self {
            runs_dir,
            episodes: std::sync::RwLock::new(HashMap::new()),
        }
    }

    fn find_dataset(&self, dataset_id: &str) -> Option<PathBuf> {
        // Search in runs_dir (and runs_dir/runs) for dataset
        let mut roots = vec![self.runs_dir.clone()];
        // Check for nested "runs" folder (legacy structure)
        let nested = self.runs_dir.join("runs");
        if nested.exists() {
            roots.push(nested);
        }

        for root in roots {
            if let Ok(entries) = std::fs::read_dir(&root) {
                for entry in entries.flatten() {
                    let p = entry.path();
                    if p.is_dir() {
                        let candidate_folder = p.join("datasets").join(dataset_id);
                        if candidate_folder.exists() {
                            let pq = candidate_folder.join("normalized_events.parquet");
                            if pq.exists() {
                                return Some(pq);
                            }
                            return Some(candidate_folder);
                        }
                    }
                }
            }
        }
        None
    }


    // Helper to validate dataset profile vs brain requirement
    // TODO: This should be called in reset_episode once we have Metadata in ResetRequest
    #[allow(clippy::result_large_err)]
    fn validate_profile(&self, dataset_id: &str, required_profile: &str) -> Result<(), Status> {
        let path = std::path::Path::new("runs").join(dataset_id).join("metadata.json");
        if !path.exists() {
            return Err(Status::not_found(format!("Dataset metadata not found: {:?}", path)));
        }

        let content = std::fs::read_to_string(&path)
            .map_err(|e| Status::internal(format!("Failed to read metadata: {}", e)))?;
        
        let meta: serde_json::Value = serde_json::from_str(&content)
            .map_err(|e| Status::internal(format!("Failed to parse metadata: {}", e)))?;
        
        let profile = meta["feature_profile"].as_str().unwrap_or("simple");
        
        if profile.to_lowercase() != required_profile.to_lowercase() {
            return Err(Status::failed_precondition(format!(
                "Feature Profile Mismatch: Dataset uses '{}', but RLConfig requires '{}'", 
                profile, required_profile
            )));
        }
        
        Ok(())
    }
}

#[tonic::async_trait]
impl RlService for RLServiceImpl {
    async fn reset_episode(
        &self,
        request: Request<ResetRequest>,
    ) -> Result<Response<ResetResponse>, Status> {
        let req = request.into_inner();
        let timestamp = chrono::Utc::now().format("%H%M%S").to_string();
        let suffix = Uuid::new_v4().to_string()[..4].to_string();
        let episode_id = format!("{}_RL_{}_{}", req.dataset_id.replace("_DS", ""), timestamp, suffix);

        info!("RL ResetEpisode: dataset={} symbol={} seed={} episode={}",
            req.dataset_id, req.symbol, req.seed, episode_id);

        // Enforce Feature Profile Consistency
        if let Some(profile) = req.metadata.get("feature_profile") {
            self.validate_profile(&req.dataset_id, profile)?;
        }

        // Find dataset
        let dataset_path = self.find_dataset(&req.dataset_id)
            .ok_or_else(|| Status::not_found(format!("Dataset '{}' not found", req.dataset_id)))?;

        // Parse config with defaults
        let cfg = req.config.unwrap_or_default();
        let initial_equity = if cfg.initial_equity > 0.0 { cfg.initial_equity } else { 10000.0 };
        let max_leverage = if cfg.max_leverage > 0.0 { cfg.max_leverage } else { 5.0 };
        let max_pos_frac = if cfg.max_pos_frac > 0.0 { cfg.max_pos_frac } else { 0.20 };
        let maker_fee = if cfg.maker_fee > 0.0 { cfg.maker_fee } else { 2.0 }; // bps
        let taker_fee = if cfg.taker_fee > 0.0 { cfg.taker_fee } else { 5.0 }; // bps
        let decision_interval_ms = if cfg.decision_interval_ms > 0 { cfg.decision_interval_ms as i64 } else { 1000 };
        let hard_dd = if cfg.hard_disaster_drawdown > 0.0 { cfg.hard_disaster_drawdown } else { 0.06 };
        let max_daily_dd = if cfg.max_daily_drawdown > 0.0 { cfg.max_daily_drawdown } else { 0.03 };

        info!("Reset Config Check: random_start={} req_start_ts={} min_events={}", 
            cfg.random_start_offset, req.start_ts, cfg.min_episode_events);

        // Create ReplayEngine (no sleeping — virtual time for training)
        let mut start_ts_opt = if req.start_ts > 0 { Some(req.start_ts) } else { None };
        let end_ts_val = if req.end_ts > 0 { req.end_ts } else { 0 };

        // Block 1: Random Start Offset Logic
        if cfg.random_start_offset && req.start_ts == 0 {
            // dataset_path points to the parquet, we need the parent dir for manifest
            if let Some(parent_dir) = dataset_path.parent() {
                let manifest_path = parent_dir.join("dataset_manifest.json");
                match File::open(&manifest_path) {
                Ok(file) => {
                    let reader = BufReader::new(file);
                    match serde_json::from_reader::<_, Value>(reader) {
                        Ok(manifest) => {
                            let d_start = manifest.get("start_ts").and_then(|v| v.as_i64()).unwrap_or(0);
                            let d_end = manifest.get("end_ts").and_then(|v| v.as_i64()).unwrap_or(0);
                            
                            if d_start > 0 && d_end > d_start {
                                // Always use entropy for the start offset to ensure diversity
                                // unless we specifically want deterministic replay in the future.
                                let mut rng = StdRng::from_entropy();
                                
                                let min_events = if cfg.min_episode_events > 0 { cfg.min_episode_events } else { 500 };
                                let buffer_ms = min_events * 500;
                                
                                if d_end - d_start > buffer_ms {
                                    let rand_ts = rng.gen_range(d_start..d_end - buffer_ms);
                                    start_ts_opt = Some(rand_ts);
                                    info!("Random start offset chosen: {} (Dataset: {} to {})", rand_ts, d_start, d_end);
                                } else {
                                    log::warn!("Dataset too short for buffer_ms: {} vs {}", d_end - d_start, buffer_ms);
                                }
                            } else {
                                log::warn!("Invalid start/end in manifest: start={}, end={}", d_start, d_end);
                            }
                        },
                        Err(e) => log::error!("Failed to parse manifest at {:?}: {}", manifest_path, e),
                    }
                },
                Err(e) => log::error!("Failed to open manifest at {:?}: {}", manifest_path, e),
                }
            }
        }

        let replay_cfg = ReplayConfig {
            speed: 0.0, // No throttle
            allow_bad_quality: cfg.allow_bad_quality,
            start_ts: start_ts_opt,
            debug_include_raw: true,
            ..Default::default()
        };

        let replay = ReplayEngine::new(dataset_path, replay_cfg)
            .map_err(|e| Status::internal(format!("Failed to create ReplayEngine: {}", e)))?;

        let feature_cfg = FeatureEngineV2Config {
            interval_ms: decision_interval_ms as i64,
            symbol: req.symbol.clone(),
            time_mode: TimeMode::EventTimeOnly, // safe deterministic for local replay
            recv_time_lag_ms: 0,
            micro_strict: false,
            tape_zscore_clamp: (-5.0, 5.0),
            slow_tf: "1s".to_string(), // not strictly needed for offline, but match live defaults
            telemetry_enabled: true,
            telemetry_window_ms: 10_000,
            ..Default::default()
        };
        let feature_engine = FeatureEngineV2::new(feature_cfg);

        let internal_fill_model = match cfg.fill_model() {
            bot_core::proto::MakerFillModel::Optimistic => bot_data::simulation::structs::MakerFillModel::Optimistic,
            bot_core::proto::MakerFillModel::SemiOptimistic => bot_data::simulation::structs::MakerFillModel::SemiOptimistic,
            bot_core::proto::MakerFillModel::Conservative => bot_data::simulation::structs::MakerFillModel::Conservative,
        };

        // Create ExecutionConfig
        let exec_cfg = ExecutionConfig {
            base_capital_usdt: initial_equity,
            leverage_cap: max_leverage,
            maker_fee_bps: maker_fee,
            taker_fee_bps: taker_fee,
            latency_ms: 10, // Reduced for audit to ensure fills at 100ms intervals
            exit_timeout_ms: 60000,
            disaster_stop_dd_daily_pct: hard_dd * 100.0,
            allow_taker_for_disaster_exit: true,
            allow_mock_fills: true,
            slip_bps: if cfg.slip_bps > 0.0 { cfg.slip_bps } else { 1.0 },
            symbol_whitelist: vec![req.symbol.clone()],
            max_retries: 3,
            retry_backoff_ms: 100,
            slippage_model: bot_data::simulation::structs::SlippageModel::default(),
            maker_fill_model: internal_fill_model,
        };
        
        info!("EPISODE_{} START: fill_model={:?}, maker_bonus={:.6}, idle_penalty={:.8}, reprice_penalty={:.6}, threshold={:.2}", 
            episode_id, exec_cfg.maker_fill_model, cfg.reward_maker_fill_bonus, cfg.reward_idle_posting_penalty, cfg.reward_reprice_penalty_bps, cfg.post_delta_threshold_bps);

        let exec_engine = ExecutionEngine::new(exec_cfg);

        let mut episode = EpisodeHandle {
            replay,
            feature_engine,
            exec_engine,
            symbol: req.symbol.clone(),
            // decision_interval_ms,
            initial_equity,
            max_pos_frac,
            hard_disaster_dd: hard_dd,
            max_daily_dd,
            max_hold_ms: if cfg.max_hold_ms > 0 { cfg.max_hold_ms as u64 } else { 0 },
            end_ts: end_ts_val,
            // prev_fees: 0.0,
            peak_equity: initial_equity,
            step_count: 0,
            done: false,
            last_obs: vec![0.0; OBS_DIM],
            last_features: None,
            last_tick_ts: 0,
            last_mid_price: 0.0,
            last_mark_price: 0.0,
            cancel_count_in_step: 0,
            reprice_count_in_step: 0,
            post_delta_threshold_bps: cfg.post_delta_threshold_bps, 
            profit_floor_bps: if cfg.profit_floor_bps > 0.0 { cfg.profit_floor_bps } else { 0.5 },
            stop_loss_bps: if cfg.stop_loss_bps > 0.0 { cfg.stop_loss_bps } else { 30.0 },
            prev_realized_pnl: 0.0,
            prev_exposure: 0.0,
            reward_state: RewardState::new(initial_equity),
            reward_config: RewardConfig {
                // vNext reward params
                fee_cost_weight: if cfg.reward_fee_cost_weight > 0.0 { cfg.reward_fee_cost_weight } else { 0.0 },
                as_penalty_weight: if cfg.reward_as_penalty_weight > 0.0 { cfg.reward_as_penalty_weight } else { 0.0 },
                as_horizon_ms: if cfg.reward_as_horizon_ms > 0 { cfg.reward_as_horizon_ms } else { 0 },
                inventory_risk_weight: if cfg.reward_inventory_risk_weight > 0.0 { cfg.reward_inventory_risk_weight } else { 0.0 },
                realized_pnl_bonus_weight: if cfg.reward_realized_pnl_bonus_weight > 0.0 { cfg.reward_realized_pnl_bonus_weight } else { 2.0 },

                // Legacy reward params (used only if use_vnext_reward=false)
                overtrading_penalty: if cfg.reward_overtrading_penalty > 0.0 { cfg.reward_overtrading_penalty } else { 0.0 },
                exposure_penalty: if cfg.reward_exposure_penalty > 0.0 { cfg.reward_exposure_penalty } else { 0.00001 },
                toxic_fill_penalty: if cfg.reward_toxic_fill_penalty > 0.0 { cfg.reward_toxic_fill_penalty } else { 0.0002 },
                tib_bonus: if cfg.reward_tib_bonus_bps > 0.0 { cfg.reward_tib_bonus_bps / 10000.0 } else { 0.0 }, 
                maker_fill_bonus: if cfg.reward_maker_fill_bonus > 0.0 { cfg.reward_maker_fill_bonus } else { 0.002 }, 
                taker_fill_penalty: if cfg.reward_taker_fill_penalty > 0.0 { cfg.reward_taker_fill_penalty } else { 0.0005 }, 
                idle_posting_penalty: if cfg.reward_idle_posting_penalty > 0.0 { cfg.reward_idle_posting_penalty } else { 0.000001 }, 
                mtm_penalty_window_ms: cfg.reward_mtm_penalty_window_ms,
                mtm_penalty_multiplier: if cfg.reward_mtm_penalty_multiplier > 0.0 { cfg.reward_mtm_penalty_multiplier } else { 0.0 },
                reprice_penalty_bps: if cfg.reward_reprice_penalty_bps > 0.0 { cfg.reward_reprice_penalty_bps } else { 0.0 },
                reward_distance_to_mid_penalty: if cfg.reward_distance_to_mid_penalty > 0.0 { cfg.reward_distance_to_mid_penalty } else { 0.0 },
                reward_skew_penalty_weight: if cfg.reward_skew_penalty_weight > 0.0 { cfg.reward_skew_penalty_weight } else { 0.0 },
                reward_adverse_selection_bonus_multiplier: if cfg.reward_adverse_selection_bonus_multiplier > 0.0 { cfg.reward_adverse_selection_bonus_multiplier } else { 0.0 },
                reward_realized_pnl_multiplier: if cfg.reward_realized_pnl_multiplier > 0.0 { cfg.reward_realized_pnl_multiplier } else { 0.0 },
                reward_cancel_all_penalty: if cfg.reward_cancel_all_penalty > 0.0 { cfg.reward_cancel_all_penalty } else { 0.0 },
                reward_inventory_change_penalty: if cfg.reward_inventory_change_penalty > 0.0 { cfg.reward_inventory_change_penalty } else { 0.0 },
                reward_two_sided_bonus: if cfg.reward_two_sided_bonus > 0.0 { cfg.reward_two_sided_bonus } else { 0.0 },
                reward_taker_action_penalty: if cfg.reward_taker_action_penalty > 0.0 { cfg.reward_taker_action_penalty } else { 0.0 },
                reward_quote_presence_bonus: if cfg.reward_quote_presence_bonus > 0.0 { cfg.reward_quote_presence_bonus } else { 0.0 },
                invalid_action_penalty: 0.1, // Magnitude of penalty for illegal actions
                thesis_decay_weight: if cfg.reward_thesis_decay_weight > 0.0 { cfg.reward_thesis_decay_weight } else { 0.0001 },
            },
            decision_interval_ms: decision_interval_ms.try_into().unwrap_or(100),
            // vNext: Hard gate configs
            use_vnext_reward: cfg.reward_as_penalty_weight > 0.0 || cfg.reward_fee_cost_weight > 0.0 || cfg.reward_thesis_decay_weight > 0.0,
            close_position_loss_threshold: cfg.close_position_loss_threshold,
            min_post_offset_bps: cfg.min_post_offset_bps,
            imbalance_block_threshold: cfg.imbalance_block_threshold,
            gate_close_blocked_in_step: 0,
            gate_offset_blocked_in_step: 0,
            gate_imbalance_blocked_in_step: 0,
            entry_veto_count_in_step: 0,
            action_counts: HashMap::new(),
            exit_distribution: HashMap::new(),
            entry_veto_count: 0,
            exit_blocked_count: 0,
            exit_blocked_pnl_sum: 0.0,
            exit_blocked_1_to_4_count: 0,
            max_blocked_upnl_bps: 0.0,
            opportunity_lost_count: 0,
            current_trade_start_ts: None,
            win_count: 0,
            loss_count: 0,
            sum_win_hold_ms: 0,
            sum_loss_hold_ms: 0,
            realized_pnl_total: 0.0,
            use_selective_entry: cfg.use_selective_entry,
            entry_veto_threshold_bps: if cfg.entry_veto_threshold_bps > 0.0 { cfg.entry_veto_threshold_bps } else { 1.0 },
            orderbook: SimOrderBook::new(),
        };

        // Warmup: advance until first feature emission
        let obs = match episode.advance_to_next_tick() {
            (Some(mut fv), false) => {
                episode.build_obs(&mut fv)
            }
            (_, true) => {
                return Err(Status::internal("Dataset too short — no features emitted during warmup"));
            }
            _ => {
                return Err(Status::internal("Failed to generate initial observation"));
            }
        };

        let env_state = episode.build_env_state();
        let f_health = episode.build_feature_health();

        let response = ResetResponse {
            episode_id: episode_id.clone(),
            obs: Some(Observation {
                vec: obs,
                ts: episode.last_tick_ts,
            }),
            state: Some(env_state),
            info: None, // Added in proto update
            feature_health: Some(f_health),
        };

        self.episodes.write().unwrap().insert(episode_id, Arc::new(TokioMutex::new(episode)));

        Ok(Response::new(response))
    }

    async fn step(
        &self,
        request: Request<StepRequest>,
    ) -> Result<Response<StepResponse>, Status> {
        let req = request.into_inner();

        let episode_arc = {
            let episodes = self.episodes.read().unwrap();
            episodes.get(&req.episode_id).cloned()
        }.ok_or_else(|| Status::not_found("Episode not found"))?;

        let mut episode = episode_arc.lock().await;

        if episode.done {
            return Err(Status::failed_precondition("Episode already done"));
        }

        // 1. Apply action
        let action = req.action
            .ok_or_else(|| Status::invalid_argument("Missing action"))?;
        let action_raw = action.r#type;
        let action_type = ActionType::try_from(action_raw)
            .unwrap_or(ActionType::Hold);
        
        log::debug!("RL_STEP: episode={} action_raw={} action_type={:?}", req.episode_id, action_raw, action_type);

        let has_pos_before = episode.exec_engine.portfolio.state.positions.get(&episode.symbol).is_some();

        let (_, is_invalid) = episode.apply_action(action_type);

        // 2. Advance to next decision tick
        let (fv_opt, end_of_data) = episode.advance_to_next_tick();
        
        let has_pos_after = episode.exec_engine.portfolio.state.positions.get(&episode.symbol).is_some();
        let now_ts = episode.last_tick_ts;

        if has_pos_before && !has_pos_after {
            // Trade ended
            if let Some(start_ts) = episode.current_trade_start_ts.take() {
                let hold_ms = (now_ts - start_ts).max(0) as u64;
                let current_total_rpnl = episode.exec_engine.portfolio.state.cumulative_pnl.get(&episode.symbol).cloned().unwrap_or(0.0);
                
                // Use the delta about to be computed for reward
                let trade_pnl = current_total_rpnl - episode.prev_realized_pnl;

                if trade_pnl > 0.0 {
                    episode.win_count += 1;
                    episode.sum_win_hold_ms += hold_ms;
                } else if trade_pnl < 0.0 {
                    episode.loss_count += 1;
                    episode.sum_loss_hold_ms += hold_ms;
                }
                
                let was_taker = match action_type {
                    ActionType::ReduceLong | ActionType::CloseLong | 
                    ActionType::ReduceShort | ActionType::CloseShort => true,
                    _ => false,
                };
                let exit_key = if was_taker { "Market" } else { "Passive" };
                *episode.exit_distribution.entry(exit_key.to_string()).or_insert(0) += 1;

                // Check for "Lost Opportunity"
                if episode.max_blocked_upnl_bps > trade_pnl {
                    episode.opportunity_lost_count += 1;
                }
                // Reset peak for next trade
                episode.max_blocked_upnl_bps = 0.0;
            }
        }
        
        // Count trades NOW, after they have materialized inside advance_to_next_tick
        let trades_this_step = episode.exec_engine.last_fill_events.len() as u32;

        // 3. Check done conditions
        let (mut risk_done, mut reason) = episode.check_done();
        
        // 3b. Numeric Stability Check
        if let Some(err_msg) = episode.check_numeric_stability() {
            log::error!("NUMERIC ERROR in Episode {}: {}", req.episode_id, err_msg);
            // Log full snapshot
            log::error!("SNAPSHOT: Equity={:.2}, Price={:.2}, Pos={:?}, Obs={:?}", 
                episode.exec_engine.portfolio.state.equity_usdt,
                episode.last_mid_price,
                episode.exec_engine.portfolio.state.positions.get(&episode.symbol),
                episode.last_obs
            );
            risk_done = true;
            reason = "NUMERIC_ERROR";
        }
        
        let done = end_of_data || risk_done;
        episode.done = done;

        // 4. Compute reward
        let current_rpnl = episode.exec_engine.portfolio.state.positions.get(&episode.symbol)
            .map(|p| p.realized_pnl)
            .unwrap_or(0.0);
        let rpnl_step = current_rpnl - episode.prev_realized_pnl;
        episode.prev_realized_pnl = current_rpnl;
        let is_cancel_all = action_type == ActionType::Reprice; // Reprice clears and reposts
        let is_taker_action = match action_type {
            ActionType::ReduceLong | ActionType::CloseLong | 
            ActionType::ReduceShort | ActionType::CloseShort => true,
            _ => false,
        };

        let active_order_count = episode.exec_engine.portfolio.state.active_orders.len() as u32;
        let mut reward = episode.compute_reward(trades_this_step, rpnl_step, is_cancel_all, is_taker_action, active_order_count, is_invalid);
        if !reward.is_finite() {
            log::error!("Reward is not finite: {}. Clamping to -1.0.", reward);
            reward = -1.0;
        }
        episode.step_count += 1;

        // 5. Build observation
        let obs = if let Some(mut fv) = fv_opt {
            episode.build_obs(&mut fv)
        } else {
            episode.last_obs.clone()
        };

        let final_reason = if end_of_data { "END_OF_DATA" } else { reason };
        let maker_fills = episode.exec_engine.last_fill_events.iter().filter(|e| e.cost_source == bot_data::simulation::structs::CostSource::Simulated).count() as u32;
        let toxic_fills = episode.exec_engine.last_fill_events.iter().filter(|e| e.is_toxic).count() as u32;
        let stale_expiries = episode.exec_engine.stale_expiries_in_step;
        let cancel_count = episode.cancel_count_in_step;
        let active_order_count_for_info = episode.exec_engine.portfolio.state.active_orders.len() as u32;

        let mut fills = Vec::new();
        for event in &episode.exec_engine.last_fill_events {
            fills.push(TradeFill {
                trace_id: event.order_id.clone(),
                symbol: event.symbol.clone(),
                side: format!("{:?}", event.side),
                price: event.price,
                qty: event.qty_filled,
                fee: event.fee_paid,
                liquidity: format!("{:?}", event.liquidity_flag),
                ts_event: event.event_time,
                ts_recv_local: episode.last_tick_ts,
                is_toxic: event.is_toxic,
            });
        }

        if trades_this_step > 0 {
            use std::io::Write;
            if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open("C:\\Bot mk3\\bot_trades_debug.txt") {
                let _ = writeln!(file, "TICK trades={} pos={:?}", trades_this_step, episode.exec_engine.portfolio.state.positions.get(&episode.symbol).map(|p| p.qty));
            }
        }
        
        let env_state = episode.build_env_state();
        let f_health = episode.build_feature_health();

        let avg_win_hold = if episode.win_count > 0 { episode.sum_win_hold_ms as f64 / episode.win_count as f64 } else { 0.0 };
        let avg_loss_hold = if episode.loss_count > 0 { episode.sum_loss_hold_ms as f64 / episode.loss_count as f64 } else { 0.0 };
        episode.realized_pnl_total = episode.exec_engine.portfolio.state.cumulative_pnl.get(&episode.symbol).cloned().unwrap_or(0.0);

        let response = StepResponse {
            obs: Some(Observation {
                vec: obs,
                ts: episode.last_tick_ts,
            }),
            reward,
            done: episode.done,
            info: Some(StepInfo {
                ts: episode.last_tick_ts,
                reason: final_reason.to_string(),
                mid_price: episode.last_mid_price,
                mark_price: episode.last_mark_price,
                trades_executed: trades_this_step,
                maker_fills,
                toxic_fills,
                stale_expiries,
                cancel_count,
                active_order_count,
                reprice_count: episode.reprice_count_in_step,
                fills,
                gate_close_blocked: episode.gate_close_blocked_in_step,
                gate_offset_blocked: episode.gate_offset_blocked_in_step,
                gate_imbalance_blocked: episode.gate_imbalance_blocked_in_step,
                action_counts: episode.action_counts.clone(),
                realized_pnl_total: episode.realized_pnl_total,
                avg_win_hold_ms: avg_win_hold,
                avg_loss_hold_ms: avg_loss_hold,
                exit_distribution: episode.action_counts.clone(), // Wait, should be action_counts or exit_distribution? 
                // Using exit_distribution mapping and entry_veto_count
                entry_veto_count: episode.entry_veto_count_in_step, 
                exit_blocked_count: episode.exit_blocked_count,
                exit_blocked_avg_pnl_bps: if episode.exit_blocked_count > 0 { episode.exit_blocked_pnl_sum / episode.exit_blocked_count as f64 } else { 0.0 },
                exit_blocked_1_to_4_count: episode.exit_blocked_1_to_4_count,
                opportunity_lost_count: episode.opportunity_lost_count,
                thesis_decay_penalty: episode.reward_state.last_thesis_penalty,
                is_invalid,
            }),
            state: Some(env_state),
            feature_health: Some(f_health),
        };

        // Reset per-step counters
        episode.gate_close_blocked_in_step = 0;
        episode.gate_offset_blocked_in_step = 0;
        episode.gate_imbalance_blocked_in_step = 0;
        episode.entry_veto_count_in_step = 0;
        episode.exit_blocked_count = 0;
        episode.exit_blocked_pnl_sum = 0.0;

        // Cleanup done episodes
        if done {
            info!("RL Episode {} done: reason={} steps={} equity={:.2}",
                req.episode_id, final_reason, episode.step_count,
                episode.exec_engine.portfolio.state.equity_usdt);
        }

        Ok(Response::new(response))
    }

    async fn get_env_info(
        &self,
        _request: Request<EnvInfoRequest>,
    ) -> Result<Response<EnvInfoResponse>, Status> {
        Ok(Response::new(EnvInfoResponse {
            obs_dim: OBS_DIM as i32,
            action_dim: ACTION_DIM,
            obs_labels: (0..OBS_DIM).map(|i| format!("feat_{}", i)).collect(),
            action_labels: ACTION_LABELS.iter().map(|s| s.to_string()).collect(),
            feature_signature: "default_v1".to_string(), 
            feature_profile: "Dynamic".to_string(), // In MK3, this is driven by RLConfig
        }))
    }


}
