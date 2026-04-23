use log::{error, info, warn};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tokio::sync::{mpsc, oneshot};
use tokio::task::JoinHandle;

use crate::services::analytics::engine::AnalyticsEvent;
use crate::services::orchestrator::agent::{AgentCommand, AgentEvent, SymbolAgent};
use crate::services::orchestrator::commission::{
    CommissionPolicy, CommissionStats, MakerTimeoutPolicy,
};
use crate::services::orchestrator::experience::{ExperienceCommand, ExperienceService};
use crate::services::orchestrator::health_monitor::HealthMonitor;
use crate::services::orchestrator::leverage::{LeverageConfig, LeverageManager, LeverageMode};
use crate::services::orchestrator::risk::{RiskConfig, RiskManager, RiskSizingMode};
use bot_core::proto::{
    CommissionPolicyProto, CommissionStatsProto, GetOrchestratorStatusRequest, HealthStatusProto,
    KillSwitchRequest, OrchestratorConfig, OrchestratorEvent, OrchestratorStatus, RiskConfigProto,
    RiskStatusProto, SetModeRequest, SetModeResponse, StartOrchestratorRequest,
    StartOrchestratorResponse, StopOrchestratorRequest, StopOrchestratorResponse, SymbolConfig,
    SymbolHealthProto, SymbolStatus, UpdateConfigRequest, UpdateConfigResponse,
};
use bot_data::binance_futures_live::client::BinanceClient;
use bot_data::binance_futures_live::execution::LiveExecutionAdapter;
use bot_data::binance_futures_live::market::LiveMarketData;
use bot_data::execution::ExecutionInterface;
use bot_data::features_v2::{FeatureEngineV2, FeatureEngineV2Config};
use bot_data::simulation::execution::ExecutionEngine;
use bot_data::simulation::execution::SimExecutionAdapter;
use bot_data::simulation::structs::ExecutionConfig;
use std::path::PathBuf;

pub enum OrchestratorCommand {
    Start(
        StartOrchestratorRequest,
        oneshot::Sender<StartOrchestratorResponse>,
    ),
    Stop(
        StopOrchestratorRequest,
        oneshot::Sender<StopOrchestratorResponse>,
    ),
    GetStatus(
        GetOrchestratorStatusRequest,
        oneshot::Sender<OrchestratorStatus>,
    ),
    SetMode(SetModeRequest, oneshot::Sender<SetModeResponse>),
    UpdateConfig(UpdateConfigRequest, oneshot::Sender<UpdateConfigResponse>),
    SubscribeEvents(mpsc::Sender<Result<OrchestratorEvent, tonic::Status>>),

    ResetPaperState(oneshot::Sender<UpdateConfigResponse>),
    ReloadPolicy(
        bot_core::proto::ReloadPolicyRequest,
        oneshot::Sender<bot_core::proto::ReloadPolicyResponse>,
    ),
    // Risk & Commission
    UpdateRiskConfig(RiskConfigProto, oneshot::Sender<UpdateConfigResponse>),
    GetRiskStatus(oneshot::Sender<RiskStatusProto>),
    UpdateCommissionPolicy(CommissionPolicyProto, oneshot::Sender<UpdateConfigResponse>),
    GetCommissionStats(oneshot::Sender<CommissionStatsProto>),
    ResetRiskState(oneshot::Sender<UpdateConfigResponse>),
    KillSwitch(KillSwitchRequest, oneshot::Sender<UpdateConfigResponse>),
    GetHealthStatus(oneshot::Sender<HealthStatusProto>),
}

pub struct OrchestratorEngine {
    cmd_rx: mpsc::Receiver<OrchestratorCommand>,
    event_subscribers: Vec<mpsc::Sender<Result<OrchestratorEvent, tonic::Status>>>,

    // State
    agents: HashMap<String, mpsc::Sender<AgentEvent>>,
    agent_tasks: Vec<JoinHandle<()>>,
    symbol_statuses: HashMap<String, Arc<Mutex<SymbolStatus>>>,

    risk_manager: Arc<Mutex<RiskManager>>,
    leverage_manager: Arc<Mutex<LeverageManager>>,

    mode: String, // "PAPER" or "LIVE"
    config: Option<OrchestratorConfig>,

    // Shared Paper Engine (for shared capital and persistence)
    // Shared Paper Engine (for shared capital and persistence)
    paper_engine: Option<Arc<Mutex<ExecutionEngine>>>,

    // Analytics
    analytics_tx: Option<mpsc::Sender<AnalyticsEvent>>,

    // Experience
    experience_tx: Option<mpsc::Sender<ExperienceCommand>>,

    // Commission
    commission_policy: Arc<Mutex<CommissionPolicy>>,
    commission_stats: Arc<Mutex<CommissionStats>>,

    // Health Monitor
    health_monitor: Arc<Mutex<HealthMonitor>>,
    
    // State
    current_run_id: Option<String>,
}

impl OrchestratorEngine {
    pub fn new(
        cmd_rx: mpsc::Receiver<OrchestratorCommand>,
        analytics_tx: Option<mpsc::Sender<AnalyticsEvent>>,
    ) -> Self {
        Self {
            cmd_rx,
            event_subscribers: Vec::new(),
            agents: HashMap::new(),
            agent_tasks: Vec::new(),
            symbol_statuses: HashMap::new(),
            risk_manager: Arc::new(Mutex::new(RiskManager::default())),
            leverage_manager: Arc::new(Mutex::new(LeverageManager::new())),
            mode: "PAPER".to_string(), // Default safe
            config: None,
            paper_engine: None,
            analytics_tx,
            experience_tx: None,
            commission_policy: Arc::new(Mutex::new(CommissionPolicy::default())),
            commission_stats: Arc::new(Mutex::new(CommissionStats::default())),
            health_monitor: Arc::new(Mutex::new(HealthMonitor::new())),
            current_run_id: None,
        }
    }

    pub async fn run(mut self) {
        info!("Orchestrator Engine Started");
        let mut save_interval = tokio::time::interval(tokio::time::Duration::from_secs(10));

        loop {
            tokio::select! {
                cmd_opt = self.cmd_rx.recv() => {
                    if let Some(cmd) = cmd_opt {
                        match cmd {
                            OrchestratorCommand::Start(req, reply) => {
                                let res = self.handle_start(req).await;
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::Stop(req, reply) => {
                                let res = self.handle_stop(req).await;
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::GetStatus(_, reply) => {
                                let res = self.handle_status().await;
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::SetMode(req, reply) => {
                                let res = self.handle_set_mode(req).await;
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::UpdateConfig(req, reply) => {
                                let res = self.handle_update_config(req).await;
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::SubscribeEvents(tx) => {
                                self.event_subscribers.push(tx);
                            }
                            OrchestratorCommand::ResetPaperState(reply) => {
                                let res = self.handle_reset_paper_state().await;
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::ReloadPolicy(req, reply) => {
                                let res = self.handle_reload_policy(req).await;
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::UpdateRiskConfig(req, reply) => {
                                let res = self.handle_update_risk_config(req);
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::GetRiskStatus(reply) => {
                                let res = self.handle_get_risk_status();
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::UpdateCommissionPolicy(req, reply) => {
                                let res = self.handle_update_commission_policy(req);
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::GetCommissionStats(reply) => {
                                let res = self.handle_get_commission_stats();
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::ResetRiskState(reply) => {
                                let res = self.handle_reset_risk_state();
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::KillSwitch(req, reply) => {
                                let res = self.handle_kill_switch(req);
                                let _ = reply.send(res);
                            }
                            OrchestratorCommand::GetHealthStatus(reply) => {
                                let res = self.handle_get_health_status();
                                let _ = reply.send(res);
                            }
                        }
                    } else {
                        break;
                    }
                }
                _ = save_interval.tick() => {
                    if self.mode == "PAPER" && !self.agents.is_empty() {
                        let _ = self.save_paper_state().await;
                    }
                }
            }
        }
        info!("Orchestrator Engine Stopped");
    }

    async fn handle_start(&mut self, req: StartOrchestratorRequest) -> StartOrchestratorResponse {
        if !self.agents.is_empty() {
            return StartOrchestratorResponse {
                run_id: "".to_string(),
                status: "ALREADY_RUNNING".to_string(),
            };
        }

        let run_id = format!("run_{}", chrono::Utc::now().format("%Y%m%d_%H%M%S"));

        // Update global config
        if let Some(cfg) = req.config {
            self.config = Some(cfg.clone());
            // Legacy config no longer drives risk; kept for reference
            let _ = cfg;
        }

        // Paper mode: auto-set 100% DD limits so paper testing is never blocked
        if self.mode == "PAPER" {
            let now_ms = chrono::Utc::now().timestamp_millis();
            let mut risk = self.risk_manager.lock().unwrap();
            risk.update_config(
                now_ms,
                RiskConfig {
                    max_daily_dd_pct: 100.0,
                    max_monthly_dd_pct: 100.0,
                    max_total_dd_pct: 100.0,
                    ..Default::default()
                },
            );
            info!("PAPER MODE: Auto-set risk limits to 100% (unrestricted paper trading)");
        }

        // Load leverage config from disk
        {
            let mut lm = self.leverage_manager.lock().unwrap();
            if let Err(e) = lm.load_from_disk("data/config/leverage_config.json") {
                error!("Failed to load leverage config: {}", e);
            }
        }

        // Start Experience Service
        if req.record_experience && self.experience_tx.is_none() {
            let (tx, rx) = mpsc::channel(1000);
            let base_dir = PathBuf::from("runs").join(&run_id).join("experience");
            let service = ExperienceService::new(rx, base_dir);
            service.start();
            self.experience_tx = Some(tx);
        }

        // 1. Create a channel to collect events from all agents
        let (agent_event_tx, mut agent_event_rx) = mpsc::channel::<OrchestratorEvent>(1000);

        // 2. Spawn a task to broadcast events to all GUI subscribers
        let subscribers = Arc::new(Mutex::new(self.event_subscribers.clone()));
        let subscribers_for_task = subscribers.clone();
        tokio::spawn(async move {
            while let Some(event) = agent_event_rx.recv().await {
                let mut subs = subscribers_for_task.lock().unwrap();
                subs.retain(|tx| tx.try_send(Ok(event.clone())).is_ok());
            }
        });

        // 3. Create Parity Writer (Live)
        let parity_tx = if self.mode == "LIVE" || self.mode == "PAPER" {
            Some(Arc::new(bot_data::reporting::parity::LiveCaptureWriter::new(&run_id))) 
        } else {
            None
        };

        for sym_cfg in req.symbols {
            let _ = self.setup_leverage_config(&sym_cfg);

            // Global Paper Engine setup
            if self.mode == "PAPER" && self.paper_engine.is_none() {
                // Default initial capital for paper trading.
                // max_total_exposure_frac is a 0-1 fraction, NOT a dollar amount.
                let initial_capital = 1500.0;
                info!("PAPER: initial_capital = {} USDT", initial_capital);
                let mut exec_cfg = ExecutionConfig {
                    base_capital_usdt: initial_capital,
                    ..Default::default()
                };
                exec_cfg.slip_bps = 1.0;
                exec_cfg.disaster_stop_dd_daily_pct = 100.0;

                match self.load_paper_state() {
                    Some(state) => {
                        self.paper_engine = Some(Arc::new(Mutex::new(
                            ExecutionEngine::from_state(exec_cfg, state),
                        )));
                    }
                    None => {
                        self.paper_engine =
                            Some(Arc::new(Mutex::new(ExecutionEngine::new(exec_cfg))));
                    }
                }
            }

            match self.spawn_agent(sym_cfg, agent_event_tx.clone(), parity_tx.clone(), run_id.clone()).await {
                Ok((symbol, tx)) => {
                    self.agents.insert(symbol, tx);
                }
                Err(e) => {
                    error!("Failed to spawn agent: {}", e);
                    return StartOrchestratorResponse {
                        run_id: "".to_string(),
                        status: format!("FAILED: {}", e),
                    };
                }
            }
        }

        // Notify Analytics
        if let Some(tx) = &self.analytics_tx {
            let start_ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis() as i64;
                
            use std::collections::hash_map::DefaultHasher;
            use std::hash::{Hash, Hasher};
            let mut hasher = DefaultHasher::new();
            format!("{:?}", self.config).hash(&mut hasher);
            let config_hash = format!("{:x}", hasher.finish());
            
            let metadata = crate::services::analytics::metadata::RunMetadata::generate(
                run_id.clone(),
                self.mode.clone(),
                config_hash,
                "ConservativeMaker".to_string(),
                start_ts
            );

            let _ = tx
                .send(AnalyticsEvent::SessionStart {
                    session_id: run_id.clone(),
                    start_ts,
                    metadata,
                })
                .await;
        }

        self.current_run_id = Some(run_id.clone());

        StartOrchestratorResponse {
            run_id,
            status: "STARTED".to_string(),
        }
    }

    async fn spawn_agent(
        &mut self,
        config: SymbolConfig,
        event_tx: mpsc::Sender<OrchestratorEvent>,
        parity_tx: Option<Arc<bot_data::reporting::parity::LiveCaptureWriter>>,
        run_id: String,
    ) -> Result<(String, mpsc::Sender<AgentEvent>), String> {
        let symbol = config.symbol.clone();

        // 1. Create Execution Adapter
        let execution: Box<dyn ExecutionInterface> = if self.mode == "LIVE" {
            // Load API keys: try env vars first, then credentials.json saved by GUI
            let (api_key, secret_key, use_testnet) = Self::load_api_credentials();
            if api_key.is_empty() || secret_key.is_empty() {
                return Err(
                    "LIVE mode requires Binance API credentials. \
                     Set BINANCE_API_KEY and BINANCE_SECRET_KEY environment variables, \
                     or configure them in the System tab of the GUI."
                        .to_string(),
                );
            }
            info!("[LIVE] API key loaded ({}...{}), testnet={}",
                  &api_key[..6.min(api_key.len())],
                  &api_key[api_key.len().saturating_sub(4)..],
                  use_testnet);
            let max_retries = 3;
            let retry_backoff_ms = 100;
            Box::new(LiveExecutionAdapter::new(
                api_key,
                secret_key,
                max_retries,
                retry_backoff_ms,
            ))
        } else {
            // Paper - SimAdapter uses Shared Paper Engine
            let engine = self.paper_engine.clone().unwrap_or_else(|| {
                Arc::new(Mutex::new(ExecutionEngine::new(ExecutionConfig::default())))
            });
            Box::new(SimExecutionAdapter::new(engine))
        };

        let feature_engine = FeatureEngineV2::new(FeatureEngineV2Config {
            interval_ms: config.decision_interval_ms as i64,
            symbol: symbol.clone(),
            time_mode: bot_data::normalization::schema::TimeMode::RecvTimeAware,
            recv_time_lag_ms: 10, // Max realism logic: 10ms network lag tolerance buffer
            micro_strict: true,
            tape_zscore_clamp: (-5.0, 5.0),
            slow_tf: "1s".to_string(),
            telemetry_enabled: true,
            telemetry_window_ms: 300_000,
            ..Default::default()
        });

        // 3. Create Policy Adapter
        let policy_addr = std::env::var("POLICY_SERVER_ADDR")
            .unwrap_or_else(|_| "127.0.0.1:50055".to_string());
        let policy = crate::services::orchestrator::policy::PythonPolicyAdapter::new(
            policy_addr,
            "live_run".to_string(),
            config.policy_id.clone(),
        )
        .await
        .map_err(|e| format!("Failed Policy Init: {}", e))?;

        // M1/M2 Handshake Guard
        let expected_version = bot_data::features_v2::schema::FeatureRow::OBS_SCHEMA_VERSION;
        let expected_dim = bot_data::features_v2::schema::FeatureRow::OBS_DIM;
        match policy.get_profile().await {
            Ok(profile) => {
                if profile.schema_version != expected_version {
                    return Err(format!(
                        "Handshake failed! Policy schema_version={} but Bot expects {}",
                        profile.schema_version, expected_version
                    ));
                }

                if profile.obs_dim > 0 && profile.obs_dim != expected_dim {
                    return Err(format!(
                        "Handshake failed! Policy obs_dim={} but Bot obs_dim={}",
                        profile.obs_dim, expected_dim
                    ));
                }
            }
            Err(e) => {
                if self.mode == "PAPER" {
                    warn!(
                        "Policy profile unavailable in PAPER mode for {}: {}. Continuing with runtime HOLD fallback.",
                        symbol, e
                    );
                } else {
                    return Err(format!("Failed to get policy profile: {}", e));
                }
            }
        }

        // 4. Create Status
        let status = Arc::new(Mutex::new(SymbolStatus {
            symbol: symbol.clone(),
            ..Default::default()
        }));
        self.symbol_statuses.insert(symbol.clone(), status.clone());

        // 5. Create Channel
        let (agent_tx, agent_rx) = mpsc::channel(100);

        // 6. Create & Spawn Agent
        let agent = SymbolAgent::new(
            config.clone(),
            agent_rx,
            event_tx,
            execution,
            policy,
            feature_engine,
            self.risk_manager.clone(),
            self.leverage_manager.clone(),
            self.analytics_tx.clone(),
            self.experience_tx.clone(),
            status,
            self.commission_policy.clone(),
            self.commission_stats.clone(),
            parity_tx,
            run_id.clone(),
        );

        let handle = tokio::spawn(agent.run());
        self.agent_tasks.push(handle);

        // 6. Spawn Data Feed (LiveMarketData) associated with this Agent
        // Even in PAPER mode, we use Live Data (for this "Live Paper" implementation).
        let md_tx = agent_tx.clone();
        let md_symbol = symbol.clone();
        let paper_engine = self.paper_engine.clone();
        let is_live = self.mode == "LIVE";

        tokio::spawn(async move {
            let use_testnet = std::env::var("BINANCE_TESTNET")
                .map(|v| v == "1" || v.to_lowercase() == "true")
                .unwrap_or(false);
            let md = LiveMarketData::new(md_symbol, use_testnet);
            let (tx, mut rx) = mpsc::channel(100);
            tokio::spawn(md.run(tx));

            while let Some(res) = rx.recv().await {
                if let Ok(ev) = res {
                    // If in Paper mode, update the simulation engine
                    let fills = if let Some(engine_lock) = &paper_engine {
                        let mut engine = engine_lock.lock().unwrap();
                        engine.update(&ev)
                    } else {
                        vec![]
                    };

                    for fill in fills {
                        let _ = md_tx.send(AgentEvent::Fill(fill)).await;
                    }

                    if md_tx
                        .send(AgentEvent::MarketData(Box::new(ev)))
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
            }
        });

        // 7. In LIVE mode: spawn User Data Stream for fill tracking
        //    and position reconciliation loop.
        if is_live || self.mode == "PAPER" {
            // User Data Stream (live fills)
            let api_key = std::env::var("BINANCE_API_KEY").unwrap_or_default();
            let secret_key = std::env::var("BINANCE_SECRET_KEY").unwrap_or_default();
            if !api_key.is_empty() && !secret_key.is_empty() {
                let live_client = Arc::new(BinanceClient::new(api_key, secret_key));
                let fill_agent_tx = agent_tx.clone();
                let live_client_for_stream = live_client.clone();
                let is_live_inner = is_live;

                tokio::spawn(async move {
                    let (fill_tx, mut fill_rx) = mpsc::channel(100);
                    if let Err(e) = live_client_for_stream.start_user_data_stream(fill_tx).await {
                        error!("[SHADOW] Failed to start User Data Stream: {}", e);
                        return;
                    }

                    while let Some(fill) = fill_rx.recv().await {
                        let exec_record = bot_data::reporting::backtest::ExecutionRecord {
                            symbol: fill.symbol,
                            side: fill.side,
                            qty: fill.qty,
                            price: fill.price,
                            fee: fill.commission,
                            ts: fill.timestamp_ms,
                            order_type: fill.order_type,
                            slippage_bps: 0.0,
                            liquidity_flag: "Maker".to_string(),
                        };
                        
                        let ev = if is_live_inner {
                            AgentEvent::Fill(exec_record)
                        } else {
                            AgentEvent::RealFillForDivergence(exec_record)
                        };

                        if fill_agent_tx.send(ev).await.is_err() {
                            break;
                        }
                    }
                });

                // Position reconciliation (every 30s)
                if is_live_inner {
                    let recon_client = live_client.clone();
                    let recon_symbol = symbol.clone();
                    tokio::spawn(async move {
                        let mut interval = tokio::time::interval(tokio::time::Duration::from_secs(30));
                        loop {
                            interval.tick().await;
                            match recon_client.get_position(&recon_symbol).await {
                                Ok(Some(pos)) => {
                                    info!(
                                        "[RECON][{}] qty={:.4} side={} entry={:.2} upnl={:.4} margin={:.2}",
                                        recon_symbol, pos.qty, pos.side, pos.entry_price,
                                        pos.unrealized_pnl, pos.margin_used
                                    );
                                }
                                Ok(None) => {
                                    // Flat — no position
                                }
                                Err(e) => {
                                    warn!("[RECON][{}] Failed: {}", recon_symbol, e);
                                }
                            }
                        }
                    });
                }
            }
        }

        Ok((symbol, agent_tx))
    }

    /// Load API credentials from environment variables first, then fallback to
    /// `data/config/credentials.json` (saved by the GUI System tab).
    fn load_api_credentials() -> (String, String, bool) {
        let mut api_key = std::env::var("BINANCE_API_KEY").unwrap_or_default();
        let mut secret_key = std::env::var("BINANCE_SECRET_KEY").unwrap_or_default();
        let mut use_testnet = std::env::var("BINANCE_TESTNET")
            .map(|v| v == "1" || v.to_lowercase() == "true")
            .unwrap_or(false);

        // Fallback: read from credentials file if env vars are empty
        if api_key.is_empty() || secret_key.is_empty() {
            let cred_path = PathBuf::from("data/config/credentials.json");
            if cred_path.exists() {
                if let Ok(contents) = std::fs::read_to_string(&cred_path) {
                    if let Ok(json) = serde_json::from_str::<serde_json::Value>(&contents) {
                        if api_key.is_empty() {
                            api_key = json
                                .get("api_key")
                                .and_then(|v| v.as_str())
                                .unwrap_or("")
                                .to_string();
                        }
                        if secret_key.is_empty() {
                            secret_key = json
                                .get("secret_key")
                                .and_then(|v| v.as_str())
                                .unwrap_or("")
                                .to_string();
                        }
                        if let Some(tn) = json.get("testnet").and_then(|v| v.as_bool()) {
                            use_testnet = tn;
                        }
                        if !api_key.is_empty() {
                            info!("[CREDENTIALS] Loaded from {}", cred_path.display());
                        }
                    }
                }
            }
        }

        (api_key, secret_key, use_testnet)
    }

    async fn handle_stop(&mut self, _req: StopOrchestratorRequest) -> StopOrchestratorResponse {
        for tx in self.agents.values() {
            let _ = tx.send(AgentEvent::Command(AgentCommand::Stop)).await;
        }
        self.agents.clear();
        self.symbol_statuses.clear();
        // Save leverage config to disk
        {
            let lm = self.leverage_manager.lock().unwrap();
            if let Err(e) = lm.save_to_disk("data/config/leverage_config.json") {
                error!("Failed to save leverage config: {}", e);
            }
        }
        // Save paper state
        if self.mode == "PAPER" {
            let _ = self.save_paper_state().await;
        }
        // Notify Analytics
        if let Some(tx) = &self.analytics_tx {
            let end_ts = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_millis() as i64;
            let _ = tx.send(AnalyticsEvent::SessionStop { end_ts }).await;
        }

        // Close Experience Service to flush data
        self.experience_tx = None;

        // Tasks will finish naturally
        StopOrchestratorResponse {
            status: "STOPPED".to_string(),
        }
    }

    async fn handle_status(&self) -> OrchestratorStatus {
        let mut symbols = vec![];
        for status_lock in self.symbol_statuses.values() {
            let s = status_lock.lock().unwrap();
            symbols.push(s.clone());
        }

        let total_margin: f64 = symbols.iter().map(|s| s.equity_alloc_used).sum();
        let risk = self.risk_manager.lock().unwrap();

        OrchestratorStatus {
            state: if self.agents.is_empty() {
                "STOPPED".to_string()
            } else {
                "RUNNING".to_string()
            },
            mode: self.mode.clone(),
            start_time: 0,
            global_equity: risk.current_equity,
            global_cash: 0.0,
            global_exposure: risk.total_exposure,
            global_exposure_frac: if risk.equity_peak_daily > 0.0 {
                risk.total_exposure / risk.equity_peak_daily
            } else {
                0.0
            },
            max_dd_seen: risk.max_drawdown_reached,
            symbols,
            global_margin_used: total_margin,
        }
    }

    async fn handle_set_mode(&mut self, req: SetModeRequest) -> SetModeResponse {
        if !self.agents.is_empty() {
            return SetModeResponse {
                old_mode: self.mode.clone(),
                new_mode: self.mode.clone(),
            };
        }
        if req.mode != "PAPER" && req.mode != "LIVE" {
            return SetModeResponse {
                old_mode: self.mode.clone(),
                new_mode: self.mode.clone(),
            };
        }
        let old = self.mode.clone();
        self.mode = req.mode.clone(); // fix self.mode assignment order
        SetModeResponse {
            old_mode: old,
            new_mode: self.mode.clone(),
        }
    }

    async fn handle_update_config(&mut self, req: UpdateConfigRequest) -> UpdateConfigResponse {
        // Update leverage configs from symbol_updates
        for sym_cfg in &req.symbol_updates {
            let _ = self.setup_leverage_config(sym_cfg);
        }
        // Persist
        {
            let lm = self.leverage_manager.lock().unwrap();
            if let Err(e) = lm.save_to_disk("data/config/leverage_config.json") {
                error!("Failed to save leverage config: {}", e);
            }
        }
        UpdateConfigResponse { success: true }
    }

    /// Extract leverage config from proto SymbolConfig and apply to LeverageManager.
    fn setup_leverage_config(&self, sym_cfg: &SymbolConfig) -> Result<(), String> {
        let lev_cfg = LeverageConfig {
            mode: LeverageMode::from_proto(sym_cfg.leverage_mode),
            manual_value: if sym_cfg.leverage_manual > 0.0 {
                sym_cfg.leverage_manual
            } else {
                5.0
            },
            fixed_value: if sym_cfg.leverage_fixed > 0.0 {
                sym_cfg.leverage_fixed
            } else {
                5.0
            },
            auto_min: if sym_cfg.auto_min_leverage > 0.0 {
                sym_cfg.auto_min_leverage
            } else {
                3.0
            },
            auto_max: if sym_cfg.auto_max_leverage > 0.0 {
                sym_cfg.auto_max_leverage
            } else {
                10.0
            },
            auto_cooldown_secs: if sym_cfg.auto_cooldown_seconds > 0 {
                sym_cfg.auto_cooldown_seconds
            } else {
                60
            },
            auto_max_change_per_min: if sym_cfg.auto_max_change_per_min > 0.0 {
                sym_cfg.auto_max_change_per_min
            } else {
                1.0
            },
            auto_vol_ref: if sym_cfg.auto_vol_ref > 0.0 {
                sym_cfg.auto_vol_ref
            } else {
                0.002
            },
            auto_spread_ref: if sym_cfg.auto_spread_ref > 0.0 {
                sym_cfg.auto_spread_ref
            } else {
                0.001
            },
            live_apply_enabled: sym_cfg.live_apply_enabled,
            live_apply_on_start: sym_cfg.live_apply_on_start,
            live_readback_enabled: sym_cfg.live_readback_enabled,
            live_readback_interval_secs: if sym_cfg.live_readback_interval_seconds > 0 {
                sym_cfg.live_readback_interval_seconds
            } else {
                120
            },
        };
        let mut lm = self.leverage_manager.lock().unwrap();
        lm.set_config(&sym_cfg.symbol, lev_cfg)
    }

    async fn save_paper_state(&self) -> Result<(), String> {
        let engine = match &self.paper_engine {
            Some(e) => e,
            None => return Ok(()),
        };
        let state = {
            let eng = engine.lock().map_err(|e| e.to_string())?;
            eng.portfolio.state.clone()
        };
        let path = "data/paper_account.json";
        let _ = std::fs::create_dir_all("data");

        let json = serde_json::to_string_pretty(&state).map_err(|e| e.to_string())?;
        std::fs::write(path, json).map_err(|e| e.to_string())?;
        info!("Paper state saved to {}", path);
        Ok(())
    }

    fn load_paper_state(&self) -> Option<bot_data::simulation::structs::PortfolioState> {
        let path = "data/paper_account.json";
        if !std::path::Path::new(path).exists() {
            return None;
        }
        match std::fs::read_to_string(path) {
            Ok(json) => {
                match serde_json::from_str::<bot_data::simulation::structs::PortfolioState>(&json) {
                    Ok(state) => {
                        info!("Paper state loaded from {}", path);
                        Some(state)
                    }
                    Err(e) => {
                        error!("Failed to parse paper state: {}. Renaming to .bad", e);
                        let _ = std::fs::rename(path, format!("{}.bad", path));
                        None
                    }
                }
            }
            Err(e) => {
                error!("Failed to read paper state: {}", e);
                None
            }
        }
    }

    async fn handle_reset_paper_state(&mut self) -> UpdateConfigResponse {
        let path = "data/paper_account.json";
        if std::path::Path::new(path).exists() {
            let _ = std::fs::remove_file(path);
        }
        self.paper_engine = None;
        info!("Paper state reset successfully");
        UpdateConfigResponse { success: true }
    }

    async fn handle_reload_policy(
        &mut self,
        req: bot_core::proto::ReloadPolicyRequest,
    ) -> bot_core::proto::ReloadPolicyResponse {
        let mut targets = vec![];
        if !req.symbol.is_empty() {
            if let Some(tx) = self.agents.get(&req.symbol) {
                targets.push((req.symbol.clone(), tx.clone()));
            } else {
                return bot_core::proto::ReloadPolicyResponse {
                    success: false,
                    message: format!("Symbol {} not found", req.symbol),
                };
            }
        } else {
            for (sym, tx) in &self.agents {
                targets.push((sym.clone(), tx.clone()));
            }
        }

        if targets.is_empty() {
            return bot_core::proto::ReloadPolicyResponse {
                success: false,
                message: "No active agents to reload".to_string(),
            };
        }

        let mut success = true;
        let mut messages = vec![];

        for (sym, tx) in targets {
            let (reply_tx, reply_rx): (tokio::sync::oneshot::Sender<Result<(), String>>, _) =
                tokio::sync::oneshot::channel();
            if tx
                .send(AgentEvent::Command(AgentCommand::ReloadPolicy(
                    req.model_path.clone(),
                    reply_tx,
                )))
                .await
                .is_err()
            {
                messages.push(format!("{}: Agent unreachable", sym));
                success = false;
                continue;
            }

            match reply_rx.await {
                Ok(Ok(_)) => {
                    messages.push(format!("{}: OK", sym));
                    info!(
                        r#"{{"event": "reload_policy_success", "symbol": "{}", "model_path": "{}"}}"#,
                        sym, req.model_path
                    );
                }
                Ok(Err(e)) => {
                    messages.push(format!("{}: Failed - {}", sym, e));
                    success = false;
                    log::error!(
                        r#"{{"event": "reload_policy_error", "symbol": "{}", "error": "{}"}}"#,
                        sym,
                        e
                    );
                }
                Err(_) => {
                    messages.push(format!("{}: Timeout/Error", sym));
                    success = false;
                    log::error!(
                        r#"{{"event": "reload_policy_timeout", "symbol": "{}"}}"#,
                        sym
                    );
                }
            }
        }

        bot_core::proto::ReloadPolicyResponse {
            success,
            message: messages.join("; "),
        }
    }

    // ── Risk & Commission Handlers ──

    fn handle_update_risk_config(&self, req: RiskConfigProto) -> UpdateConfigResponse {
        let now_ms = chrono::Utc::now().timestamp_millis();
        let sizing_mode = match req.sizing_mode.as_str() {
            "FixedFractionOfEquity" => RiskSizingMode::FixedFractionOfEquity,
            _ => RiskSizingMode::StopDistanceBased,
        };
        let new_config = RiskConfig {
            max_daily_dd_pct: req.max_daily_dd_pct,
            max_monthly_dd_pct: req.max_monthly_dd_pct,
            max_total_dd_pct: req.max_total_dd_pct,
            risk_per_trade_pct: if req.risk_per_trade_pct > 0.0 {
                req.risk_per_trade_pct
            } else {
                1.0
            },
            max_total_leverage: if req.max_total_leverage > 0.0 {
                req.max_total_leverage
            } else {
                20.0
            },
            max_positions_total: if req.max_positions_total > 0 {
                req.max_positions_total as usize
            } else {
                10
            },
            max_positions_per_symbol: if req.max_positions_per_symbol > 0 {
                req.max_positions_per_symbol as usize
            } else {
                1
            },
            max_order_rate_per_min: if req.max_order_rate_per_min > 0 {
                req.max_order_rate_per_min
            } else {
                60
            },
            flatten_on_disable: req.flatten_on_disable,
            kill_switch_enabled: req.kill_switch_enabled,
            min_notional_per_order: req.min_notional_per_order,
            max_notional_per_order: if req.max_notional_per_order > 0.0 {
                req.max_notional_per_order
            } else {
                1_000_000.0
            },
            sizing_mode,
            default_stop_distance_bps: if req.default_stop_distance_bps > 0.0 {
                req.default_stop_distance_bps
            } else {
                50.0
            },
            allow_reduce_only_when_disabled: req.allow_reduce_only_when_disabled,
            profit_floor_bps: if req.profit_floor_bps > 0.0 {
                req.profit_floor_bps
            } else {
                10.0
            },
            stop_loss_bps: if req.stop_loss_bps > 0.0 {
                req.stop_loss_bps
            } else {
                30.0
            },
            use_selective_entry: req.use_selective_entry,
            entry_veto_threshold_bps: if req.entry_veto_threshold_bps > 0.0 {
                req.entry_veto_threshold_bps
            } else {
                1.0
            },
        };
        let mut risk = self.risk_manager.lock().unwrap();
        let old_dd = (
            risk.cfg.max_daily_dd_pct,
            risk.cfg.max_monthly_dd_pct,
            risk.cfg.max_total_dd_pct,
        );
        risk.update_config(now_ms, new_config.clone());

        if let Some(tx) = &self.analytics_tx {
            let change = crate::services::analytics::engine::ConfigChangeEvent {
                ts: now_ms,
                component: "Risk".to_string(),
                key: "DD_Thresholds".to_string(),
                old_value: format!("({:.1},{:.1},{:.1})", old_dd.0, old_dd.1, old_dd.2),
                new_value: format!("({:.1},{:.1},{:.1})", new_config.max_daily_dd_pct, new_config.max_monthly_dd_pct, new_config.max_total_dd_pct),
            };
            let _ = tx.try_send(AnalyticsEvent::ConfigChange(change));
        }

        UpdateConfigResponse { success: true }
    }

    fn handle_get_risk_status(&self) -> RiskStatusProto {
        let risk = self.risk_manager.lock().unwrap();
        let snap = risk.current_drawdowns();
        RiskStatusProto {
            daily_dd_pct: snap.daily_dd_pct,
            monthly_dd_pct: snap.monthly_dd_pct,
            total_dd_pct: snap.total_dd_pct,
            state: snap.state.as_str().to_string(),
            equity: snap.equity,
            daily_peak: snap.daily_peak,
            monthly_peak: snap.monthly_peak,
            total_peak: snap.total_peak,
            order_rate_current: 0, // TODO: expose from SlidingCounter
            last_trigger_kind: String::new(),
            last_trigger_reason: String::new(),
            needs_flatten: risk.flatten_state.is_active(),
        }
    }

    fn handle_update_commission_policy(&self, req: CommissionPolicyProto) -> UpdateConfigResponse {
        let timeout_policy = match req.maker_timeout_policy.as_str() {
            "ConvertToTaker" => MakerTimeoutPolicy::ConvertToTaker,
            _ => MakerTimeoutPolicy::CancelAndSkip,
        };
        let new_policy = CommissionPolicy {
            prefer_maker: req.prefer_maker,
            allow_taker: req.allow_taker,
            max_taker_ratio: req.max_taker_ratio,
            max_fee_bps_per_trade: req.max_fee_bps_per_trade,
            maker_fee_bps: if req.maker_fee_bps > 0.0 {
                req.maker_fee_bps
            } else {
                2.0
            },
            taker_fee_bps: if req.taker_fee_bps > 0.0 {
                req.taker_fee_bps
            } else {
                4.0
            },
            maker_entry_offset_bps: req.maker_entry_offset_bps,
            maker_timeout_ms: if req.maker_timeout_ms > 0 {
                req.maker_timeout_ms
            } else {
                5000
            },
            maker_timeout_policy: timeout_policy,
            allow_emergency_taker: req.allow_emergency_taker,
            allow_override_for_emergency: req.allow_override_for_emergency,
            taker_ratio_window_sec: if req.taker_ratio_window_sec > 0 {
                req.taker_ratio_window_sec
            } else {
                3600
            },
            min_spread_bps_for_maker: req.min_spread_bps_for_maker,
            max_spread_bps_for_entry: if req.max_spread_bps_for_entry > 0.0 {
                req.max_spread_bps_for_entry
            } else {
                100.0
            },
            require_book_for_market_slippage_est: req.require_book_for_market_slippage_est,
        };
        let mut policy_guard = self.commission_policy.lock().unwrap();
        let old_maker = policy_guard.maker_fee_bps;
        let old_taker = policy_guard.taker_fee_bps;
        
        info!("gui_update_commission_policy: prefer_maker={}, allow_taker={}, max_taker_ratio={:.2}, maker_fee={:.1}, taker_fee={:.1}",
              new_policy.prefer_maker, new_policy.allow_taker, new_policy.max_taker_ratio,
              new_policy.maker_fee_bps, new_policy.taker_fee_bps);
        *policy_guard = new_policy.clone();
        drop(policy_guard);

        if let Some(tx) = &self.analytics_tx {
            let now_ms = chrono::Utc::now().timestamp_millis();
            let change = crate::services::analytics::engine::ConfigChangeEvent {
                ts: now_ms,
                component: "Commission".to_string(),
                key: "Fees".to_string(),
                old_value: format!("M:{:.1},T:{:.1}", old_maker, old_taker),
                new_value: format!("M:{:.1},T:{:.1}", new_policy.maker_fee_bps, new_policy.taker_fee_bps),
            };
            let _ = tx.try_send(AnalyticsEvent::ConfigChange(change));
        }

        UpdateConfigResponse { success: true }
    }

    fn handle_get_commission_stats(&self) -> CommissionStatsProto {
        let stats = self.commission_stats.lock().unwrap();
        let total = stats.maker_count + stats.taker_count;
        let maker_ratio = if total > 0 {
            stats.maker_count as f64 / total as f64
        } else {
            0.0
        };
        CommissionStatsProto {
            maker_count: stats.maker_count,
            taker_count: stats.taker_count,
            total_fees_usdt: stats.total_fees_usdt,
            taker_ratio: stats.taker_ratio(),
            avg_fee_bps: stats.avg_fee_bps(),
            maker_ratio,
            avg_total_cost_bps: stats.avg_fee_bps(), // TODO: add slippage estimate
        }
    }

    fn handle_reset_risk_state(&self) -> UpdateConfigResponse {
        let now_ms = chrono::Utc::now().timestamp_millis();
        let mut risk = self.risk_manager.lock().unwrap();
        let equity = risk.current_equity;
        let account = crate::services::orchestrator::risk::AccountSnapshot {
            equity,
            wallet_balance: equity,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
        };
        risk.reset_state(now_ms, &account);
        info!("gui_reset_risk: equity={:.2}, state_after=Running", equity);
        UpdateConfigResponse { success: true }
    }

    fn handle_kill_switch(&self, req: KillSwitchRequest) -> UpdateConfigResponse {
        let now_ms = chrono::Utc::now().timestamp_millis();
        let mut risk = self.risk_manager.lock().unwrap();
        if req.enabled {
            let reason = if req.reason.is_empty() {
                "GUI kill switch"
            } else {
                &req.reason
            };
            risk.kill(now_ms, reason);
            info!("gui_kill_switch: enabled=true, reason={}", reason);
        } else {
            // Cannot un-kill via KillSwitch — must use ResetRisk
            info!("gui_kill_switch: enabled=false (no-op, use ResetRisk to re-enable)");
        }
        UpdateConfigResponse { success: true }
    }

    fn handle_get_health_status(&self) -> HealthStatusProto {
        let hm = self.health_monitor.lock().unwrap();
        let symbols: Vec<SymbolHealthProto> = hm
            .symbol_health()
            .iter()
            .map(|sh| SymbolHealthProto {
                symbol: sh.symbol.clone(),
                ws_connected: sh.ws_connected,
                book_synced: sh.book_synced,
                book_resets: sh.book_resets,
                spread_bps: sh.spread_bps,
            })
            .collect();
        HealthStatusProto {
            symbols,
            lag_p50_ms: hm.lag_p50(),
            lag_p99_ms: hm.lag_p99(),
            errors_total: hm.errors_total,
            last_error: hm.last_error.clone(),
        }
    }
}
