use bot_core::proto::paper_service_server::PaperService;
use bot_core::proto::{
    StartPaperRequest, StartPaperResponse,
    StopPaperRequest, StopPaperResponse,
    GetPortfolioStatusRequest, PortfolioStatus,
    StreamPortfolioUpdatesRequest, PortfolioUpdate,
    Position as ProtoPosition,
};
use bot_data::simulation::execution::ExecutionEngine;
use bot_data::simulation::structs::{ExecutionConfig, Side, OrderType};
use bot_data::replay::engine::ReplayEngine;
use bot_data::replay::types::ReplayConfig;
use tokio_stream::StreamExt;

use tonic::{Request, Response, Status};
use tokio::sync::{mpsc, broadcast, Mutex as TokioMutex}; 
use std::sync::{Arc, Mutex}; // Standard Mutex for sync engine
use std::collections::HashMap;
use std::path::PathBuf;
use log::{info, error, warn};
use uuid::Uuid;

pub struct PaperSession {
    pub _id: String,
    pub status: String,
    pub engine: Arc<Mutex<ExecutionEngine>>, // Sync Mutex
    pub stop_sender: mpsc::Sender<()>,
    pub update_sender: broadcast::Sender<PortfolioUpdate>,
}

pub struct PaperServiceImpl {
    runs_dir: PathBuf,
    sessions: Arc<TokioMutex<HashMap<String, PaperSession>>>,
}

impl PaperServiceImpl {
    pub fn new(runs_dir: PathBuf) -> Self {
        Self {
            runs_dir,
            sessions: Arc::new(TokioMutex::new(HashMap::new())),
        }
    }

    fn find_dataset(&self, dataset_id: &str) -> Option<PathBuf> {
        info!("Searching for dataset {} in runs_dir: {:?}", dataset_id, self.runs_dir);
        if let Ok(entries) = std::fs::read_dir(&self.runs_dir) {
            for entry in entries.flatten() {
                 let run_path = entry.path();
                 let ds_path = run_path.join("datasets").join(dataset_id).join("normalized_events.parquet");
                 if ds_path.exists() {
                     info!("Found dataset at {:?}", ds_path);
                     return Some(ds_path);
                 }
            }
        } else {
            error!("Failed to read runs_dir: {:?}", self.runs_dir);
        }
        warn!("Dataset {} not found in any run under {:?}", dataset_id, self.runs_dir);
        None
    }
}

#[tonic::async_trait]
impl PaperService for PaperServiceImpl {
    async fn start_paper(
        &self,
        request: Request<StartPaperRequest>,
    ) -> Result<Response<StartPaperResponse>, Status> {
        let req = request.into_inner();
        let dataset_path = self.find_dataset(&req.dataset_id)
            .ok_or_else(|| Status::not_found("Dataset not found"))?;

        let timestamp = chrono::Utc::now().format("%H%M").to_string();
        let paper_id = format!("{}_PAPER_{}", req.dataset_id.replace("_DS", ""), timestamp);
        let (stop_tx, mut stop_rx) = mpsc::channel(1);
        let (update_tx, _) = broadcast::channel(100);
        
        let config = ExecutionConfig {
            base_capital_usdt: req.initial_capital,
            ..Default::default() 
        };
        
        let engine = ExecutionEngine::new(config);
        let engine_arc = Arc::new(Mutex::new(engine)); // std::sync::Mutex
        let engine_clone = engine_arc.clone();
        
        let _update_tx_clone = update_tx.clone();
        let paper_id_clone = paper_id.clone();
        
        let should_stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let should_stop_clone = should_stop.clone();
        
        // Async Stop Monitor
        tokio::spawn(async move {
             let _ = stop_rx.recv().await;
             should_stop_clone.store(true, std::sync::atomic::Ordering::Relaxed);
        });

        // Blocking Simulation Loop
        tokio::task::spawn_blocking(move || {
             info!("Starting Paper Simulation (Blocking) {} speed={}", paper_id_clone, req.replay_speed);
             let mut replay_cfg = ReplayConfig::default();
             if req.replay_speed > 0.0 {
                 replay_cfg.speed = req.replay_speed;
             }
             let speed = replay_cfg.speed;
             let mut replay = match ReplayEngine::new(dataset_path.clone(), replay_cfg) {
                Ok(r) => {
                    info!("ReplayEngine initialized successfully from {:?}", dataset_path);
                    r
                },
                Err(e) => {
                    error!("Failed to start replay from {:?}: {}", dataset_path, e);
                    return;
                }
             };
             
             let mut virtual_start_ts: i64 = 0;
             let mut real_start_ts = std::time::Instant::now();

             
             loop {
                 if should_stop.load(std::sync::atomic::Ordering::Relaxed) {
                     info!("Paper Simulation Stopping...");
                     break;
                 }
                 
                 match replay.next_event() {
                     Some(event) => {

                         let evt_ts = event.ts_canonical;
                         if virtual_start_ts == 0 {
                             virtual_start_ts = evt_ts;
                             real_start_ts = std::time::Instant::now();
                         }
                         
                         if speed > 0.0 {
                             let offset_ms = evt_ts.saturating_sub(virtual_start_ts);
                             let target_offset = std::time::Duration::from_millis(
                                 (offset_ms as f64 / speed) as u64
                             );
                             
                             let elapsed = real_start_ts.elapsed();
                             if target_offset > elapsed {
                                 let mut remaining = target_offset - elapsed;
                                 // Sleep in 100ms chunks so we can check should_stop
                                 let chunk = std::time::Duration::from_millis(100);
                                 while remaining > chunk {
                                     std::thread::sleep(chunk);
                                     remaining -= chunk;
                                     if should_stop.load(std::sync::atomic::Ordering::Relaxed) {
                                         info!("Paper Simulation Stopping (during throttle)...");
                                         return;
                                     }
                                 }
                                 if !remaining.is_zero() {
                                     std::thread::sleep(remaining);
                                 }
                             }
                         }

                         // Synchronous Update
                         {
                             if let Ok(mut eng) = engine_clone.lock() {
                                 
                                 let norm_event = bot_data::normalization::schema::NormalizedMarketEvent {
                                     schema_version: 1,
                                     run_id: "?".to_string(),
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
                                     payload_json: event.payload_json.clone().unwrap_or_default(),
                                     open_interest: event.open_interest,
                                     open_interest_value: event.open_interest_value,
                                     };
                                 
                                 eng.update(&norm_event);
                             } else {
                                 break; // Poisoned
                             }
                         }
                     },
                     None => {
                         info!("Paper Simulation Completed");
                         break;
                     }
                 }
             }
        });

        let session = PaperSession {
            _id: paper_id.clone(),
            status: "RUNNING".to_string(),
            engine: engine_arc,
            stop_sender: stop_tx,
            update_sender: update_tx,
        };

        self.sessions.lock().await.insert(paper_id.clone(), session);

        Ok(Response::new(StartPaperResponse {
            paper_id,
        }))
    }

    async fn stop_paper(
        &self,
        request: Request<StopPaperRequest>,
    ) -> Result<Response<StopPaperResponse>, Status> {
        let req = request.into_inner();
        let mut sessions = self.sessions.lock().await;
        
        if let Some(session) = sessions.get_mut(&req.paper_id) {
            let _ = session.stop_sender.send(()).await;
            session.status = "STOPPED".to_string();
            Ok(Response::new(StopPaperResponse { status: "STOPPED".to_string() }))
        } else {
            Err(Status::not_found("Paper session not found"))
        }
    }

    async fn get_portfolio_status(
        &self,
        request: Request<GetPortfolioStatusRequest>,
    ) -> Result<Response<PortfolioStatus>, Status> {
        let req = request.into_inner();
        let sessions = self.sessions.lock().await;
        
        if let Some(session) = sessions.get(&req.paper_id) {
            // Lock std::sync::Mutex (blocking call on async thread, but okay for fast ops)
            let eng = match session.engine.lock() {
                Ok(guard) => guard,
                Err(_) => return Err(Status::internal("Engine lock poisoned")),
            };
            
            let state = &eng.portfolio.state;
            
            let positions = state.positions.values().map(|p| ProtoPosition {
                symbol: p.symbol.clone(),
                side: format!("{:?}", p.side),
                qty: p.qty,
                entry_price: p.entry_vwap,
                unrealized_pnl: p.unrealized_pnl,
                realized_pnl: p.realized_pnl,
                realized_fees: p.realized_fees,
                realized_funding: p.realized_funding,
                liquidation_price: p.liquidation_price,
            }).collect();
            
            Ok(Response::new(PortfolioStatus {
                 paper_id: req.paper_id,
                 cash: state.cash_usdt,
                 equity: state.equity_usdt,
                 margin_used: state.margin_used,
                 available_margin: state.available_margin,
                 positions,
                 active_order_count: state.active_orders.len() as i32,
                 state: session.status.clone(),
             }))
        } else {
            Err(Status::not_found("Session not found"))
        }
    }

    type StreamPortfolioUpdatesStream = std::pin::Pin<Box<dyn tokio_stream::Stream<Item = Result<PortfolioUpdate, Status>> + Send + Sync + 'static>>;

    async fn stream_portfolio_updates(
        &self,
        request: Request<StreamPortfolioUpdatesRequest>,
    ) -> Result<Response<Self::StreamPortfolioUpdatesStream>, Status> {
        let req = request.into_inner();
        let sessions = self.sessions.lock().await;
             
        if let Some(session) = sessions.get(&req.paper_id) {
            let rx = session.update_sender.subscribe();
            let stream = tokio_stream::wrappers::BroadcastStream::new(rx)
                .map(|result| match result {
                    Ok(update) => Ok(update),
                    Err(err) => Err(Status::data_loss(format!("Stream lagged: {:?}", err))),
                });
            
            Ok(Response::new(Box::pin(stream)))
        } else {
             Err(Status::not_found("Session not found"))
        }
    }

    async fn submit_order(
        &self,
        request: Request<bot_core::proto::SubmitOrderRequest>,
    ) -> Result<Response<bot_core::proto::SubmitOrderResponse>, Status> {
        let req = request.into_inner();
        let sessions = self.sessions.lock().await;

        if let Some(session) = sessions.get(&req.paper_id) {
            let mut eng = match session.engine.lock() {
                Ok(guard) => guard,
                Err(_) => return Err(Status::internal("Engine lock poisoned")),
            };
            
            // Map side and type
            let side = match req.side.to_lowercase().as_str() {
                "buy" => Side::Buy,
                "sell" => Side::Sell,
                _ => return Err(Status::invalid_argument("Invalid side")),
            };
            
            let order_type = match req.order_type.to_lowercase().as_str() {
                "limit" => OrderType::Limit,
                "market" => OrderType::Market,
                _ => return Err(Status::invalid_argument("Invalid order type")),
            };
            
            let order_id = eng.submit_order(
                &req.symbol,
                side,
                req.price,
                req.qty,
                order_type
            );
            
            info!("Paper Order Submitted: {} {} {} @ {}", order_id, req.side, req.qty, req.price);
            
            Ok(Response::new(bot_core::proto::SubmitOrderResponse {
                order_id,
                success: true,
                message: "Order queued".to_string(),
            }))
        } else {
            Err(Status::not_found("Session not found"))
        }
    }

    async fn cancel_order(
        &self,
        request: Request<bot_core::proto::CancelOrderRequest>,
    ) -> Result<Response<bot_core::proto::CancelOrderResponse>, Status> {
        let req = request.into_inner();
        let sessions = self.sessions.lock().await;
        
        if let Some(session) = sessions.get(&req.paper_id) {
            let mut eng = match session.engine.lock() {
                Ok(guard) => guard,
                Err(_) => return Err(Status::internal("Engine lock poisoned")),
            };
            
            if eng.portfolio.state.active_orders.contains_key(&req.order_id) {
                eng.portfolio.state.active_orders.remove(&req.order_id);
                info!("Paper Order Cancelled: {}", req.order_id);
                Ok(Response::new(bot_core::proto::CancelOrderResponse {
                    success: true,
                    message: "Order cancelled".to_string(),
                }))
            } else {
                Err(Status::not_found("Order not found"))
            }
        } else {
             Err(Status::not_found("Session not found"))
        }
    }
}
