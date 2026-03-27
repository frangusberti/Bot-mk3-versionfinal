use tonic::{Request, Response, Status};
use std::sync::Arc;
use tokio::sync::RwLock;

use bot_core::proto::analytics_service_server::AnalyticsService;
use bot_core::proto::{
    SessionRequest, SessionMetricsResponse, EquityCurveResponse, 
    SessionListResponse, Empty
};
use bot_data::reporting::backtest::{BacktestReport, ExecutionRecord, EquityPoint as ReportEquityPoint};
use crate::services::analytics::engine::AnalyticsState;

pub struct AnalyticsServiceImpl {
    state: Arc<RwLock<AnalyticsState>>,
    data_dir: std::path::PathBuf,
}

impl AnalyticsServiceImpl {
    pub fn new(state: Arc<RwLock<AnalyticsState>>, data_dir: std::path::PathBuf) -> Self {
        Self { state, data_dir }
    }
}

#[tonic::async_trait]
impl AnalyticsService for AnalyticsServiceImpl {
    async fn get_session_metrics(
        &self,
        request: Request<SessionRequest>,
    ) -> Result<Response<SessionMetricsResponse>, Status> {
        let _req = request.into_inner();
        let state = self.state.read().await;
        
        // MVP: Return current running metrics if session matches or if empty (current)
        // For historical, we'd need to load from disk or keep in memory.
        
        let metrics = &state.running_metrics;

        // Build Full Report
        let mut report = BacktestReport::new(
            state.session_id.clone().unwrap_or("unknown".to_string()),
            "dataset_unknown".to_string(), // TODO: Store metadata in AnalyticsState
            "symbol_unknown".to_string(),
        );
        report.start_ts = state.start_ts;
        report.end_ts = 0; // TODO
        
        // Map Equity Curve
        report.equity_curve = state.equity_curve.iter().map(|p| ReportEquityPoint {
            ts: p.timestamp,
            equity: p.equity,
            drawdown_pct: 0.0, // Calculated later
        }).collect();
        // Compute metrics inside report (re-calc)
        report.compute_metrics();
        
        // Map Executions
        report.executions = state.fills.iter().map(|f| ExecutionRecord {
            symbol: f.symbol.clone(),
            side: f.side.clone(),
            qty: f.qty,
            price: f.price,
            fee: f.fee,
            ts: f.ts,
            order_type: f.order_type.clone(),
            slippage_bps: 0.0,
            liquidity_flag: "Maker".to_string(),
        }).collect();
        
        let json_report = serde_json::to_string(&report).unwrap_or("{}".to_string());
        
        Ok(Response::new(SessionMetricsResponse {
            total_return: metrics.total_return_pct,
            max_drawdown: metrics.max_drawdown_pct,
            sharpe_ratio: metrics.sharpe_ratio,
            profit_factor: metrics.profit_factor,
            win_rate: metrics.win_rate,
            total_fees: metrics.total_fees,
            total_trades: metrics.total_trades,
            json_report,
        }))
    }

    async fn get_equity_curve(
        &self,
        _request: Request<SessionRequest>,
    ) -> Result<Response<EquityCurveResponse>, Status> {
        let state = self.state.read().await;
        
        let points = state.equity_curve.iter().map(|p| {
            bot_core::proto::EquityPoint {
                timestamp: p.timestamp,
                equity: p.equity,
                cash: p.cash,
                unrealized_pnl: p.unrealized_pnl,
            }
        }).collect();
        
        Ok(Response::new(EquityCurveResponse { points }))
    }

    async fn get_round_trips(
        &self,
        _request: Request<SessionRequest>,
    ) -> Result<Response<bot_core::proto::RoundTripsResponse>, Status> {
        let state = self.state.read().await;
        
        let trades = state.round_trips.iter().map(|t| {
            bot_core::proto::RoundTripRecord {
                symbol: t.symbol.clone(),
                side: t.side.clone(),
                qty: t.qty,
                entry_price: t.entry_price,
                exit_price: t.exit_price,
                entry_ts: t.entry_ts,
                exit_ts: t.exit_ts,
                margin_used: t.margin_used,
                leverage: t.leverage,
                pnl_gross: t.pnl_gross,
                pnl_net: t.pnl_net,
                total_fees: t.total_fees,
                funding_fees: t.funding_fees,
            }
        }).collect();
        
        Ok(Response::new(bot_core::proto::RoundTripsResponse { trades }))
    }

    async fn list_sessions(
        &self,
        _request: Request<Empty>,
    ) -> Result<Response<SessionListResponse>, Status> {
        // Todo: Scan data dir? For now return current ID if active
        let state = self.state.read().await;
        let mut ids = Vec::new();
        if let Some(sid) = &state.session_id {
            ids.push(sid.clone());
        }
        Ok(Response::new(SessionListResponse { session_ids: ids }))
    }

    async fn delete_session(
        &self,
        request: Request<bot_core::proto::DeleteSessionRequest>,
    ) -> Result<Response<bot_core::proto::DeleteResponse>, Status> {
        let req = request.into_inner();
        let session_id = req.session_id; // Usually run_id in current arch
        
        let mut deleted = false;
        
        // 1. Delete from runs/sessions (if we store sessions separately)
        let session_path = self.data_dir.join("runs").join("sessions").join(&session_id);
        if session_path.exists() {
             let _ = std::fs::remove_dir_all(&session_path);
             deleted = true;
        }

        // 2. Delete from runs/runs (Backtest run)
        let run_path = self.data_dir.join("runs").join("runs").join(&session_id);
        if run_path.exists() {
             let _ = std::fs::remove_dir_all(&run_path);
             deleted = true;
        }
        
        // 3. Delete from runs (Legacy root)
        let legacy_path = self.data_dir.join("runs").join(&session_id);
        if legacy_path.exists() && legacy_path.is_dir() {
             let _ = std::fs::remove_dir_all(&legacy_path);
             deleted = true;
        }
        
        if deleted {
             Ok(Response::new(bot_core::proto::DeleteResponse {
                 success: true,
                 message: "Session/Run deleted successfully".to_string(),
             }))
        } else {
             Ok(Response::new(bot_core::proto::DeleteResponse {
                 success: false,
                 message: "Session not found".to_string(),
             }))
        }
    }
}
