use bot_core::proto::backtest_service_server::BacktestService;
use bot_core::proto::{BacktestRequest, BacktestResponse};
use tonic::{Request, Response, Status};
use std::path::PathBuf;
use log::info;
use uuid::Uuid;

use bot_data::features_v2::{FeatureEngineV2, FeatureEngineV2Config};
use bot_data::replay::reader::BatchedReplayReader;
use bot_data::replay::types::ClockMode;
use bot_data::strategy::{Strategy, MeanReversionV2Strategy, MicrostructureMomentumStrategy};
use bot_data::strategy::mean_reversion_v2::MeanReversionV2Config;
use bot_data::strategy::microstructure_momentum::MicroMomentumConfig;
use crate::runner::episode_runner::{EpisodeRunner, StrategyPolicy, Environment};
use bot_data::simulation::execution::ExecutionEngine;
use bot_data::simulation::structs::ExecutionConfig;
use bot_data::reporting::backtest::BacktestReport;

pub struct BacktestServiceImpl {
    runs_dir: PathBuf,
}

impl BacktestServiceImpl {
    pub fn new(runs_dir: PathBuf) -> Self {
        Self { runs_dir }
    }
    
    fn find_dataset(&self, dataset_id: &str) -> Option<PathBuf> {
        let mut roots = vec![self.runs_dir.clone()];
        let nested = self.runs_dir.join("runs");
        if nested.exists() { roots.push(nested); }

        for root in roots {
            if let Ok(entries) = std::fs::read_dir(&root) {
                for entry in entries.flatten() {
                    let p = entry.path();
                    if p.is_dir() {
                        let candidate = p.join("datasets").join(dataset_id);
                        if candidate.exists() {
                            let pq = candidate.join("normalized_events.parquet");
                            if pq.exists() { return Some(pq); }
                            return Some(candidate);
                        }
                    }
                }
            }
        }
        None
    }
    
    fn create_strategy(&self, name: &str, config_json: &str) -> Result<Box<dyn Strategy>, String> {
        match name {
            "EmaCross" | "Momentum" => {
                Ok(Box::new(MicrostructureMomentumStrategy::new(MicroMomentumConfig::default())))
            },
            "RangeBreakout" => {
                Ok(Box::new(MicrostructureMomentumStrategy::new(MicroMomentumConfig::default())))
            },
            "MeanReversion" => {
                let mut config = MeanReversionV2Config::default();
                if let Ok(v) = serde_json::from_str::<serde_json::Value>(config_json) {
                    if let Some(w) = v.get("max_bb_width").and_then(|x| x.as_f64()) { config.max_bb_width = w; }
                    if let Some(m) = v.get("max_rv_5m").and_then(|x| x.as_f64()) { config.max_rv_5m = m; }
                    if let Some(q) = v.get("qty_fraction").and_then(|x| x.as_f64()) { config.qty_frac = q; }
                }
                Ok(Box::new(MeanReversionV2Strategy::new(config)))
            },
            _ => Err(format!("Unknown strategy: {}", name)),
        }
    }
}

#[tonic::async_trait]
impl BacktestService for BacktestServiceImpl {
    async fn run_backtest(&self, request: Request<BacktestRequest>) -> Result<Response<BacktestResponse>, Status> {
        let req = request.into_inner();
        let timestamp = chrono::Utc::now().format("%H%M").to_string();
        let backtest_id = format!("{}_BACKTEST_{}", req.dataset_id.replace("_DS", ""), timestamp);
        
        info!("Starting Backtest {} for Dataset {} with Strategy {}", backtest_id, req.dataset_id, req.strategy_name);
        
        let dataset_path = self.find_dataset(&req.dataset_id)
            .ok_or_else(|| Status::not_found("Dataset not found"))?;
            
        let reader = BatchedReplayReader::new(
            dataset_path, 
            0, 
            ClockMode::Canonical, 
            true
        ).map_err(|e| Status::internal(format!("Failed to open dataset: {}", e)))?;
        
        let feature_engine = FeatureEngineV2::new(FeatureEngineV2Config {
            interval_ms: 1000,
            symbol: "BTCUSDT".to_string(), // TODO: Detect from dataset
            time_mode: bot_data::normalization::schema::TimeMode::EventTimeOnly,
            recv_time_lag_ms: 0,
            micro_strict: true,
            tape_zscore_clamp: (-5.0, 5.0),
            slow_tf: "1s".to_string(),
            telemetry_enabled: false,
            telemetry_window_ms: 60_000,
            ..Default::default()
        });
        
        let exec_engine = ExecutionEngine::new(ExecutionConfig {
             base_capital_usdt: 10000.0,
             leverage_cap: 5.0,
             maker_fee_bps: 2.0,
             taker_fee_bps: 5.0,
             latency_ms: 0,
             exit_timeout_ms: 0,
             disaster_stop_dd_daily_pct: 10.0,
             allow_taker_for_disaster_exit: true,
             allow_mock_fills: true,
             slip_bps: 1.0,
             symbol_whitelist: vec![],
             max_retries: 3,
             retry_backoff_ms: 100,
             slippage_model: bot_data::simulation::structs::SlippageModel::default(),
             maker_fill_model: bot_data::simulation::structs::MakerFillModel::default(),
        });
        
        let strategy = self.create_strategy(&req.strategy_name, &req.strategy_config_json)
            .map_err(Status::invalid_argument)?;
            
        let policy = StrategyPolicy::new(strategy);
        
        let mut env = Environment {
            reader,
            feature_engine,
            execution_engine: exec_engine,
            symbol: "BTCUSDT".to_string(), 
            dataset_id: req.dataset_id.clone(),
            report: BacktestReport::new(
                req.strategy_name.clone(),
                req.dataset_id.clone(),
                "BTCUSDT".to_string()
            ),
        };
        
        let mut runner = EpisodeRunner::new(policy);
        
        match runner.run(&mut env).await {
            Ok(_) => {
                let json = serde_json::to_string(&env.report).unwrap_or_default();
                Ok(Response::new(BacktestResponse {
                    backtest_id,
                    success: true,
                    error_message: "".to_string(),
                    json_report: json,
                }))
            },
            Err(e) => {
                Ok(Response::new(BacktestResponse {
                    backtest_id,
                    success: false,
                    error_message: e.to_string(),
                    json_report: "".to_string(),
                }))
            }
        }
    }
}
