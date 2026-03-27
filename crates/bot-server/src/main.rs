use tonic::transport::Server;
use log::info;
use bot_core::proto::control_service_server::ControlServiceServer;
use bot_core::proto::health_service_server::HealthServiceServer;
use bot_core::proto::market_service_server::MarketServiceServer;
use bot_core::proto::dataset_service_server::DatasetServiceServer;
use bot_core::proto::replay_service_server::ReplayServiceServer;
use bot_core::proto::feature_service_server::FeatureServiceServer;
use bot_core::proto::paper_service_server::PaperServiceServer;
use bot_core::proto::rl_service_server::RlServiceServer;
use bot_core::proto::orchestrator_service_server::OrchestratorServiceServer;
use bot_core::proto::backtest_service_server::BacktestServiceServer;


mod services;
mod engine;
mod runner;
pub mod config;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    env_logger::init_from_env(env_logger::Env::new().default_filter_or("info"));

    let port = std::env::var("BOT_PORT").unwrap_or_else(|_| "50051".to_string());
    let addr: std::net::SocketAddr = format!("0.0.0.0:{}", port).parse().unwrap();
    info!("Bot Mk3 Server listening on {}", addr);

    let health_monitor = std::sync::Arc::new(bot_data::health::HealthMonitor::new());
    let (snapshot_tx, _) = tokio::sync::broadcast::channel(16);
    let data_dir = std::path::PathBuf::from("."); // Default to current dir or load from env
    
    // Load Server Config
    let config_path = "server_config.toml";
    let server_config = if std::path::Path::new(config_path).exists() {
        let content = std::fs::read_to_string(config_path).unwrap_or_default();
        toml::from_str(&content).unwrap_or_else(|e| {
             log::error!("Failed to parse config: {}", e);
             config::ServerConfig::default()
        })
    } else {
        config::ServerConfig::default()
    };
    info!("Loaded Config: {:?}", server_config);

    let control_service = services::control::ControlServiceImpl::new(
        health_monitor.clone(), 
        snapshot_tx.clone(), 
        data_dir.clone(), 
        server_config.recorder,
        server_config.websocket,
    );
    let health_service = services::health::HealthServiceImpl::new(health_monitor.clone());
    let market_service = services::market::MarketServiceImpl::new(snapshot_tx);
    let dataset_service = services::dataset::DatasetServiceImpl::new(data_dir.clone());
    let replay_service = services::replay::ReplayServiceImpl::new(data_dir.join("runs"));
    let feature_service = services::features::FeatureServiceImpl::new(data_dir.join("runs"));
    let paper_service = services::paper::PaperServiceImpl::new(data_dir.join("runs"));
    let rl_service = services::rl::RLServiceImpl::new(data_dir.join("runs"));
    let backtest_service = services::backtest::BacktestServiceImpl::new(data_dir.join("runs"));

    // --- Analytics Engine ---
    let (analytics_tx, analytics_rx) = tokio::sync::mpsc::channel(1000);
    // Shared State
    let analytics_state = std::sync::Arc::new(tokio::sync::RwLock::new(
        services::analytics::engine::AnalyticsState::default()
    ));
    
    let mut analytics_engine = services::analytics::engine::AnalyticsEngine::new(analytics_rx, analytics_state.clone());
    tokio::spawn(async move {
        analytics_engine.run().await;
    });
    
    // ...
    
    let analytics_service = services::analytics::service::AnalyticsServiceImpl::new(analytics_state, data_dir.clone());

    // --- Orchestrator ---
    let (orch_cmd_tx, orch_cmd_rx) = tokio::sync::mpsc::channel(100);
    // Pass analytics_tx to orchestrator
    let orchestrator_engine = services::orchestrator::engine::OrchestratorEngine::new(orch_cmd_rx, Some(analytics_tx));
    tokio::spawn(orchestrator_engine.run());
    let orchestrator_service = services::orchestrator::service::OrchestratorServiceImpl::new(orch_cmd_tx);

    // --- Retention Policy ---
    let retention_config = server_config.retention.clone();
    let retention_dir = data_dir.clone();
    tokio::spawn(async move {
        services::control::run_retention_policy(retention_config, retention_dir).await;
    });


    Server::builder()
        .add_service(ControlServiceServer::new(control_service))
        .add_service(HealthServiceServer::new(health_service))
        .add_service(MarketServiceServer::new(market_service))
        .add_service(DatasetServiceServer::new(dataset_service))
        .add_service(ReplayServiceServer::new(replay_service))
        .add_service(FeatureServiceServer::new(feature_service))
        .add_service(PaperServiceServer::new(paper_service))
        .add_service(RlServiceServer::new(rl_service))
        .add_service(BacktestServiceServer::new(backtest_service))
        .add_service(OrchestratorServiceServer::new(orchestrator_service))
        .add_service(bot_core::proto::analytics_service_server::AnalyticsServiceServer::new(analytics_service))
        .serve(addr)
        .await?;

    Ok(())
}
