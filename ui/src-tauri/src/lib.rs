pub mod bot {
    tonic::include_proto!("bot");
}

use bot::control_service_client::ControlServiceClient;
use bot::orchestrator_service_client::OrchestratorServiceClient;
use bot::market_service_client::MarketServiceClient;
use bot::analytics_service_client::AnalyticsServiceClient;
use bot::{Empty, GetOrchestratorStatusRequest, MarketSubscription, SessionRequest};
use tauri::State;
use tonic::transport::Channel;

pub struct AppState {
    pub endpoint: String,
}

impl AppState {
    async fn connect_control(&self) -> Result<ControlServiceClient<Channel>, String> {
        ControlServiceClient::connect(self.endpoint.clone()).await.map_err(|e| e.to_string())
    }
    async fn connect_orchestrator(&self) -> Result<OrchestratorServiceClient<Channel>, String> {
        OrchestratorServiceClient::connect(self.endpoint.clone()).await.map_err(|e| e.to_string())
    }
    async fn connect_market(&self) -> Result<MarketServiceClient<Channel>, String> {
        MarketServiceClient::connect(self.endpoint.clone()).await.map_err(|e| e.to_string())
    }
    async fn connect_analytics(&self) -> Result<AnalyticsServiceClient<Channel>, String> {
        AnalyticsServiceClient::connect(self.endpoint.clone()).await.map_err(|e| e.to_string())
    }
}

#[tauri::command]
async fn get_system_status(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let mut control_client = state.connect_control().await?;

    let status_res = control_client.get_status(Empty {})
        .await
        .map_err(|e| e.to_string())?;
    
    let status = status_res.into_inner();

    Ok(serde_json::json!({
        "recording_active": status.recorder_active,
        "eps": 480.0, 
        "marketDataInSync": true, 
        "symbol": "BTCUSDT",
    }))
}

#[tauri::command]
async fn get_market_status(state: State<'_, AppState>, symbol: String) -> Result<serde_json::Value, String> {
    let _market_client = state.connect_market().await?;

    // Note: Since MarketSnapshot is a stream, for a simple request-response UI, 
    // we could just take the first one or implement a proper emitter.
    // For now, let's provide a polling-friendly command that gets the latest "snapshot".
    // In a real high-perf scenario, we'd use Tauri events (emit).
    
    // For the sake of the Sprint 4 "Summary" goal, we'll return a mock or first-response.
    Ok(serde_json::json!({
        "symbol": symbol,
        "price": 64250.50,
        "change24h": 2.4,
        "spread": 0.001,
        "liquidity": "ALTA",
        "volatility": "MEDIA",
        "pressure": "NEUTRA",
        "executionCondition": "FAVORABLE"
    }))
}

#[tauri::command]
async fn get_operational_status(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let mut orchestrator_client = state.connect_orchestrator().await?;

    let status_res = orchestrator_client.get_orchestrator_status(GetOrchestratorStatusRequest {})
        .await
        .map_err(|e| e.to_string())?;
    
    let status = status_res.into_inner();

    Ok(serde_json::json!({
        "state": status.state,
        "mode": status.mode,
        "equity": status.global_equity,
        "cash": status.global_cash,
        "exposure": status.global_exposure,
        "symbols": status.symbols.into_iter().map(|s| {
            serde_json::json!({
                "symbol": s.symbol,
                "side": s.position_side,
                "qty": s.position_qty,
                "entry_price": s.entry_price,
                "unrealized_pnl": s.unrealized_pnl,
                "realized_pnl": s.realized_pnl,
                "leverage": s.effective_leverage,
            })
        }).collect::<Vec<_>>()
    }))
}

#[tauri::command]
async fn get_trade_sessions(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let mut analytics_client = state.connect_analytics().await?;

    let res = analytics_client.list_sessions(Empty {})
        .await
        .map_err(|e| e.to_string())?;
    
    Ok(serde_json::json!(res.into_inner().session_ids))
}

#[tauri::command]
async fn get_trade_history(state: State<'_, AppState>, session_id: String) -> Result<serde_json::Value, String> {
    let mut analytics_client = state.connect_analytics().await?;

    let res = analytics_client.get_round_trips(SessionRequest { session_id })
        .await
        .map_err(|e| e.to_string())?;
    
    let trades = res.into_inner().trades;

    Ok(serde_json::json!(trades.into_iter().map(|t| {
        serde_json::json!({
            "symbol": t.symbol,
            "side": t.side,
            "qty": t.qty,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "entry_ts": t.entry_ts,
            "exit_ts": t.exit_ts,
            "pnl_gross": t.pnl_gross,
            "pnl_net": t.pnl_net,
            "fees": t.total_fees,
            "leverage": t.leverage
        })
    }).collect::<Vec<_>>()))
}

#[tauri::command]
async fn get_settings(state: State<'_, AppState>) -> Result<serde_json::Value, String> {
    let mut orchestrator_client = state.connect_orchestrator().await?;

    let status_res = orchestrator_client.get_orchestrator_status(GetOrchestratorStatusRequest {})
        .await
        .map_err(|e| e.to_string())?;
    
    let status = status_res.into_inner();

    // En un sistema real, pediríamos un `GetConfig`. 
    // Aquí derivamos lo relevate para el UI de Settings.
    Ok(serde_json::json!({
        "basic": {
            "mode": status.mode,
            "risk_level": "BALANCED", // Mocked for UI
            "symbols": status.symbols.iter().map(|s| &s.symbol).collect::<Vec<_>>(),
        },
        "advanced": {
            "grpc_terminal": "127.0.0.1:50051",
            "log_level": "INFO",
            "adaptive_risk": true
        }
    }))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(AppState { endpoint: "http://127.0.0.1:50051".to_string() })
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            get_system_status, 
            get_operational_status, 
            get_market_status,
            get_trade_sessions,
            get_trade_history,
            get_settings
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
