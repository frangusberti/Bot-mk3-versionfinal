use log::{info, error};
use std::collections::HashMap;
use tokio::sync::{mpsc, RwLock};
use std::sync::Arc;
use serde::{Serialize, Deserialize};

use crate::services::analytics::metrics::{SessionMetrics, EquityPoint};
use crate::services::analytics::persistence::AnalyticsPersistence;
use bot_data::simulation::structs::{Side, OrderType};

// Events that Analytics Engine listens to
#[derive(Debug, Clone)]
pub enum AnalyticsEvent {
    SessionStart { session_id: String, start_ts: i64, metadata: crate::services::analytics::metadata::RunMetadata },
    SessionStop { end_ts: i64 },
    
    // Trade Execution
    Fill { 
        symbol: String, 
        side: Side, 
        qty: f64, 
        price: f64, 
        fee: f64, 
        ts: i64,
        order_type: OrderType,
    },
    
    // Round Trip (Completed Trade)
    RoundTrip(RoundTripRecord),
    
    // Portfolio Update (Mark-to-Market)
    PortfolioState(PortfolioSnapshot),
    
    // Fee / Other
    #[allow(dead_code)]
    FeeLog {
        symbol: String,
        fee_total: f64,
        fee_pct_notional: f64,
        side: Side,
        #[allow(dead_code)]
        ts: i64,
    },
    
    // Config Changes
    ConfigChange(ConfigChangeEvent),
    
    // Shadow Trading Analysis
    SimVsRealDivergence(SimVsRealDivergence),
    
    // Pre-Flight Candidates
    CandidateRecord(crate::services::analytics::candidate::CandidateDecisionRecord),
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SimVsRealDivergence {
    pub symbol: String,
    pub order_id: String,
    pub event_ts: i64,
    pub side: Side,
    pub order_type: OrderType,
    pub expected_price: f64,    // paper simulation price
    pub expected_qty: f64,      // paper simulation fill qty
    pub expected_fee: f64,      // paper simulation fee
    pub realized_price: Option<f64>,  // true exchange price
    pub realized_qty: f64,      // true fill qty
    pub realized_fee: f64,      // true fee
    pub delay_ms: i64,          // time from paper fill to real fill (or vice versa)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PositionSnapshot {
    pub symbol: String,
    pub side: Side,
    pub qty: f64,
    pub entry_price: f64,
    pub unrealized_pnl: f64,
    pub notional: f64,
    pub margin: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortfolioSnapshot {
    pub ts: i64,
    pub equity: f64,
    pub cash: f64,
    pub unrealized_pnl: f64,
    pub margin_used: f64,
    pub total_fees_entry: f64,
    pub total_fees_exit: f64,
    pub funding_pnl: f64,
    pub positions: HashMap<String, PositionSnapshot> 
}

#[derive(Debug, Default)]
pub struct AnalyticsState {
    pub session_id: Option<String>,
    pub start_ts: i64,
    pub metadata: Option<crate::services::analytics::metadata::RunMetadata>,
    pub equity_curve: Vec<EquityPoint>,
    pub fills: Vec<TradeRecord>,
    pub round_trips: Vec<RoundTripRecord>,
    pub running_metrics: SessionMetrics,
    pub divergence_records: Vec<SimVsRealDivergence>,
    pub monitor: crate::services::analytics::monitor::AlertMonitor,
}

pub struct AnalyticsEngine {
    state: Arc<RwLock<AnalyticsState>>,
    persistence: AnalyticsPersistence,
    rx: mpsc::Receiver<AnalyticsEvent>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TradeRecord {
    pub symbol: String,
    pub side: String,
    pub qty: f64,
    pub price: f64,
    pub fee: f64,
    pub ts: i64,
    pub order_type: String, // "MARKET", "LIMIT"
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoundTripRecord {
    pub symbol: String,
    pub side: String,
    pub qty: f64,
    pub entry_price: f64,
    pub exit_price: f64,
    pub entry_ts: i64,
    pub exit_ts: i64,
    pub margin_used: f64,
    pub leverage: f64,
    pub pnl_gross: f64,
    pub pnl_net: f64,
    pub total_fees: f64,
    pub funding_fees: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigChangeEvent {
    pub ts: i64,
    pub component: String, // "Commission", "Risk"
    pub key: String,       // "maker_fee_bps", "max_daily_dd"
    pub old_value: String,
    pub new_value: String,
}

impl AnalyticsEngine {
    pub fn new(rx: mpsc::Receiver<AnalyticsEvent>, state: Arc<RwLock<AnalyticsState>>) -> Self {
        Self {
            state,
            persistence: AnalyticsPersistence::new("data/analytics"),
            rx,
        }
    }

    pub async fn run(&mut self) {
        info!("Analytics Engine started");
        
        while let Some(event) = self.rx.recv().await {
            match event {
                AnalyticsEvent::SessionStart { session_id, start_ts, metadata } => {
                    self.start_session(session_id, start_ts, metadata).await;
                }
                AnalyticsEvent::SessionStop { end_ts } => {
                    self.finalize_session(end_ts).await;
                }
                AnalyticsEvent::Fill { symbol, side, qty, price, fee, ts, order_type } => {
                    self.record_fill(symbol, side, qty, price, fee, ts, order_type).await;
                }
                AnalyticsEvent::RoundTrip(trip) => {
                    let mut state = self.state.write().await;
                    state.round_trips.push(trip);
                }
                AnalyticsEvent::PortfolioState(snap) => {
                    self.record_equity(snap.ts, snap.equity, snap.cash, snap.unrealized_pnl).await;
                }
                AnalyticsEvent::FeeLog { symbol, fee_total, fee_pct_notional, side, ts: _ } => {
                    info!("[ANALYTICS] Fee Recorded for {}: {:.4} USDT ({:.4}%) side={:?}", symbol, fee_total, fee_pct_notional * 100.0, side);
                    let mut state = self.state.write().await;
                    state.running_metrics.total_fees += fee_total;
                },
                AnalyticsEvent::SimVsRealDivergence(div) => {
                    let mut state = self.state.write().await;
                    state.monitor.check_divergence(&div);
                    state.divergence_records.push(div);
                }
                AnalyticsEvent::CandidateRecord(record) => {
                    let mut state = self.state.write().await;
                    state.monitor.check_candidate(&record);
                    if let Some(ref sid) = state.session_id {
                        self.persistence.append_candidate_record(sid, &record);
                    }
                }
                AnalyticsEvent::ConfigChange(change) => {
                    let mut state = self.state.write().await;
                    if let Some(ref sid) = state.session_id {
                        self.persistence.append_config_change(sid, &change);
                    }
                    info!("[ANALYTICS] Config Change: {}.{} -> {}", change.component, change.key, change.new_value);
                }
            }
        }
    }
    
    async fn start_session(&mut self, session_id: String, start_ts: i64, metadata: crate::services::analytics::metadata::RunMetadata) {
        info!("Analytics: Starting session {}", session_id);
        let mut state = self.state.write().await;
        state.session_id = Some(session_id);
        state.start_ts = start_ts;
        state.metadata = Some(metadata);
        
        state.equity_curve.clear();
        state.fills.clear();
        state.round_trips.clear();
        state.divergence_records.clear();
        state.running_metrics = SessionMetrics::default();
        state.monitor = crate::services::analytics::monitor::AlertMonitor::new();
    }
    
    async fn finalize_session(&mut self, _end_ts: i64) {
        let (sid_opt, meta_opt, fills, trips, curve, mut metrics, div_records) = {
            let state = self.state.write().await;
            (state.session_id.clone(), state.metadata.clone(), state.fills.clone(), state.round_trips.clone(), state.equity_curve.clone(), state.running_metrics.clone(), state.divergence_records.clone())
        };

        if let Some(sid) = sid_opt {
            info!("Analytics: Finalizing session {}", sid);
            
            // Calculate final metrics
            metrics.calculate_final(&curve);
            
            // Update state with final metrics
            {
                let mut state = self.state.write().await;
                state.running_metrics = metrics.clone();
            }

            // Persist
            if let Err(e) = self.persistence.save_session(&sid, meta_opt.as_ref(), &fills, &trips, &curve, &metrics, &div_records).await {
                error!("Failed to save analytics session: {}", e);
            }
        }
        
        let mut state = self.state.write().await;
        state.session_id = None;
    }
    
    #[allow(clippy::too_many_arguments)]
    async fn record_fill(&mut self, symbol: String, side: Side, qty: f64, price: f64, fee: f64, ts: i64, order_type: OrderType) {
        let side_str = match side { Side::Buy => "Buy", Side::Sell => "Sell" };
        let type_str = match order_type { OrderType::Market => "MARKET", OrderType::Limit => "LIMIT", _ => "OTHER" };
        
        let mut state = self.state.write().await;
        
        state.fills.push(TradeRecord {
            symbol: symbol.clone(),
            side: side_str.to_string(),
            qty,
            price,
            fee,
            ts,
            order_type: type_str.to_string(),
        });
        
        state.running_metrics.total_trades += 1;
        state.running_metrics.total_fees += fee;
    }
    
    async fn record_equity(&mut self, ts: i64, equity: f64, cash: f64, unrealized_pnl: f64) {
        let mut state = self.state.write().await;
        
        let should_record = if let Some(last) = state.equity_curve.last() {
            let delta_equity = (equity - last.equity).abs();
            let delta_pct = if last.equity != 0.0 { delta_equity / last.equity } else { 1.0 };
            let delta_ts = ts - last.timestamp;
            
            // Thresholds
            let min_pct = 0.0005; // 0.05% change trigger
            let max_interval_ms = 60_000; // 1 minute mandatory update

            // Check for Extremes (New Peak or Max DD)
            // Note: running_metrics.peak_equity is the *historical* peak
            let is_new_peak = equity > state.running_metrics.peak_equity;
            
            let current_dd = if state.running_metrics.peak_equity > 0.0 {
                (state.running_metrics.peak_equity - equity) / state.running_metrics.peak_equity
            } else { 0.0 };
            
            let is_new_max_dd = (current_dd * 100.0) > state.running_metrics.max_drawdown_pct;

            delta_pct > min_pct || delta_ts > max_interval_ms || is_new_peak || is_new_max_dd
        } else {
            true
        };

        // Always update running metrics to capture high-freq stats
        state.running_metrics.update_equity(equity);
        
        if equity > state.running_metrics.peak_equity {
            state.running_metrics.peak_equity = equity;
        }
        
        // Re-calc DD with potentially new peak
        let dd = if state.running_metrics.peak_equity > 0.0 {
             (state.running_metrics.peak_equity - equity) / state.running_metrics.peak_equity
        } else { 0.0 };
             
        if dd * 100.0 > state.running_metrics.max_drawdown_pct {
            state.running_metrics.max_drawdown_pct = dd * 100.0;
        }
        
        if should_record {
            state.equity_curve.push(EquityPoint {
                timestamp: ts,
                equity,
                cash,
                unrealized_pnl,
            });
        }
    }
}
