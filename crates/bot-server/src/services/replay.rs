use tonic::{Request, Response, Status};
use tokio::sync::{broadcast, Mutex, RwLock}; // Removed Notify
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};
use std::path::PathBuf;
use std::time::{Duration, Instant};
use log::info; // Removed error/warn if unused, or keep if needed

use bot_core::proto::replay_service_server::ReplayService;
use bot_core::proto::{
    StartReplayRequest, StartReplayResponse, StopReplayRequest, 
    GetReplayStatusRequest, StreamReplayEventsRequest, ControlReplayRequest,
    ReplayStatus, ReplayEvent as ProtoReplayEvent, Empty
};

use bot_data::replay::engine::ReplayEngine;
use bot_data::replay::types::{ReplayConfig, ClockMode};

pub struct ReplayServiceImpl {
    active_replay: Arc<Mutex<Option<ActiveReplay>>>,
    data_dir: PathBuf,
}

struct ActiveReplay {
    replay_id: String,
    stop_tx: broadcast::Sender<()>,
    event_tx: broadcast::Sender<ProtoReplayEvent>,
    
    // Control
    paused: Arc<AtomicBool>,
    step_requested: Arc<AtomicBool>, // Replaced Notify
    speed: Arc<RwLock<f64>>,
    
    // Status tracking (shared with runner)
    current_ts: Arc<std::sync::atomic::AtomicI64>,
    events_emitted: Arc<std::sync::atomic::AtomicI64>,
    _start_time: Instant,
}

impl ReplayServiceImpl {
    pub fn new(data_dir: PathBuf) -> Self {
        Self {
            active_replay: Arc::new(Mutex::new(None)),
            data_dir,
        }
    }

    fn find_dataset(&self, dataset_id: &str) -> Option<PathBuf> {
        let path = PathBuf::from(dataset_id);
        if path.exists() {
            return Some(path);
        }

        // self.data_dir is passed as "./runs" from main.rs
        if let Ok(entries) = std::fs::read_dir(&self.data_dir) {
             for entry in entries.flatten() {
                 let p = entry.path();
                 if p.is_dir() {
                     // Check for datasets/{id}/normalized_events.parquet
                     let candidate = p.join("datasets").join(dataset_id).join("normalized_events.parquet");
                     if candidate.exists() {
                         return Some(candidate);
                     }
                     // Fallback: check just the folder
                     let candidate_folder = p.join("datasets").join(dataset_id);
                     if candidate_folder.exists() {
                         return Some(candidate_folder);
                     }
                 }
            }
        }
        
        None
    }
}

#[tonic::async_trait]
impl ReplayService for ReplayServiceImpl {
    type StreamReplayEventsStream = tokio_stream::wrappers::ReceiverStream<Result<ProtoReplayEvent, Status>>;

    async fn start_replay(&self, request: Request<StartReplayRequest>) -> Result<Response<StartReplayResponse>, Status> {
        let req = request.into_inner();
        let dataset_id = req.dataset_id;
        
        let mut active_guard = self.active_replay.lock().await;
        if active_guard.is_some() {
            return Err(Status::already_exists("A replay is already running. Stop it first."));
        }

        let dataset_path = self.find_dataset(&dataset_id)
            .ok_or_else(|| Status::not_found(format!("Dataset {} not found", dataset_id)))?;

        let proto_conf = req.config.ok_or(Status::invalid_argument("Missing config"))?;
        
        let clock_mode = match proto_conf.clock_mode {
            0 => ClockMode::Exchange,
            1 => ClockMode::Local,
            2 => ClockMode::Canonical,
            _ => ClockMode::Exchange,
        };

        let config = ReplayConfig {
            speed: if proto_conf.speed < 0.0 { 0.0 } else { proto_conf.speed },
            clock_mode,
            start_ts: if proto_conf.start_ts > 0 { Some(proto_conf.start_ts) } else { None },
            end_ts: if proto_conf.end_ts > 0 { Some(proto_conf.end_ts) } else { None },
            allow_bad_quality: proto_conf.allow_bad_quality,
            ui_sample_every_n: proto_conf.ui_sample_every_n,
            ui_max_events_per_sec: proto_conf.ui_max_events_per_sec,
            debug_include_raw: proto_conf.debug_include_raw,
        };

        let mut engine = ReplayEngine::new(dataset_path.clone(), config.clone())
            .map_err(|e| Status::internal(format!("Failed to init engine: {}", e)))?;

        let run_id = uuid::Uuid::new_v4().to_string();
        
        let (event_tx, _) = broadcast::channel(2048);
        let (stop_tx, mut stop_rx) = broadcast::channel(1);
        
        let paused = Arc::new(AtomicBool::new(config.speed == 0.0));
        let step_requested = Arc::new(AtomicBool::new(false));
        let speed = Arc::new(RwLock::new(config.speed));
        
        let current_ts = Arc::new(std::sync::atomic::AtomicI64::new(0));
        let events_emitted = Arc::new(std::sync::atomic::AtomicI64::new(0));

        let task_event_tx = event_tx.clone();
        let task_paused = paused.clone();
        let task_step = step_requested.clone();
        let task_speed = speed.clone();
        let task_current_ts = current_ts.clone();
        let task_emitted = events_emitted.clone();
        let task_run_id = run_id.clone();

        // RUNNER TASK
        tokio::task::spawn_blocking(move || {
            let mut virtual_start_ts = 0;
            let mut real_start_ts = Instant::now();

            info!("Replay {} started", task_run_id);

            loop {
                // Check stop
                if stop_rx.try_recv().is_ok() {
                    info!("Replay {} stopped by signal", task_run_id);
                    break;
                }

                if task_paused.load(Ordering::SeqCst) {
                     // Check if step was requested
                     if task_step.swap(false, Ordering::SeqCst) {
                         // Proceed to process ONE event
                     } else {
                         std::thread::sleep(Duration::from_millis(50));
                         continue;
                     }
                }
                
                if let Some(event) = engine.next_event() {
                    let evt_ts = event.ts_exchange;
                    
                    if virtual_start_ts == 0 {
                        virtual_start_ts = evt_ts;
                        real_start_ts = Instant::now();
                    }
                    
                    let current_speed = { *task_speed.blocking_read() };
                    
                    if current_speed > 0.0 {
                        let offset_ms = evt_ts.saturating_sub(virtual_start_ts);
                        let target_offset = Duration::from_millis((offset_ms as f64 / current_speed) as u64);
                        let elapsed = real_start_ts.elapsed();
                        
                        if target_offset > elapsed {
                            std::thread::sleep(target_offset - elapsed);
                        }
                    }

                    task_current_ts.store(evt_ts, Ordering::Relaxed);
                    task_emitted.fetch_add(1, Ordering::Relaxed);

                    let proto_event = ProtoReplayEvent {
                        replay_id: task_run_id.clone(),
                        symbol: event.symbol,
                        stream_name: event.stream_name,
                        event_type: event.event_type,
                        ts_exchange: event.ts_exchange,
                        ts_local: event.ts_local,
                        ts_canonical: event.ts_canonical,
                        price: event.price.unwrap_or(0.0),
                        quantity: event.quantity.unwrap_or(0.0),
                        side: event.side.unwrap_or_default(),
                        best_bid: event.best_bid.unwrap_or(0.0),
                        best_ask: event.best_ask.unwrap_or(0.0),
                        mark_price: event.mark_price.unwrap_or(0.0),
                        funding_rate: event.funding_rate.unwrap_or(0.0),
                        liquidation_price: event.liquidation_price.unwrap_or(0.0),
                        liquidation_qty: event.liquidation_qty.unwrap_or(0.0),
                        open_interest: event.open_interest.unwrap_or(0.0),
                        payload_json: event.payload_json.unwrap_or_default(),
                    };

                    let _ = task_event_tx.send(proto_event);
                    
                } else {
                     info!("Replay {} finished (EOF)", task_run_id);
                     break; 
                }
            }
        });

        *active_guard = Some(ActiveReplay {
            replay_id: run_id.clone(),
            stop_tx,
            event_tx,
            paused,
            step_requested,
            speed,
            current_ts,
            events_emitted,
            _start_time: Instant::now(),
        });

        Ok(Response::new(StartReplayResponse {
            replay_id: run_id,
        }))
    }
    
    async fn stop_replay(&self, _request: Request<StopReplayRequest>) -> Result<Response<Empty>, Status> {
         let mut active_guard = self.active_replay.lock().await;
         if let Some(active) = active_guard.take() {
             let _ = active.stop_tx.send(());
         }
         Ok(Response::new(Empty {}))
    }
    
    async fn get_replay_status(&self, _request: Request<GetReplayStatusRequest>) -> Result<Response<ReplayStatus>, Status> {
         let active_guard = self.active_replay.lock().await;
         if let Some(active) = &*active_guard {
             Ok(Response::new(ReplayStatus {
                 replay_id: active.replay_id.clone(),
                 state: if active.paused.load(Ordering::SeqCst) { "PAUSED".to_string() } else { "RUNNING".to_string() },
                 current_ts: active.current_ts.load(Ordering::Relaxed),
                 speed: *active.speed.read().await,
                 progress: 0.0,
                 events_emitted: active.events_emitted.load(Ordering::Relaxed),
                 quality_status: "OK".to_string(),
                 usable_for_backtest: true,
                 reject_reason: "".to_string(),
             }))
         } else {
             Ok(Response::new(ReplayStatus {
                 replay_id: "".to_string(),
                 state: "STOPPED".to_string(),
                 current_ts: 0,
                 speed: 0.0,
                 progress: 0.0,
                 events_emitted: 0,
                 quality_status: "".to_string(),
                 usable_for_backtest: false,
                 reject_reason: "".to_string(),
             }))
         }
    }
    
    async fn stream_replay_events(&self, request: Request<StreamReplayEventsRequest>) -> Result<Response<Self::StreamReplayEventsStream>, Status> {
          let req = request.into_inner();
          let active_guard = self.active_replay.lock().await;
          
          let sample_n = 10; // TODO: active.config.ui_sample_every_n

          if let Some(active) = &*active_guard {
              if active.replay_id != req.replay_id && !req.replay_id.is_empty() {
                  return Err(Status::not_found("Replay ID mismatch or not active"));
              }
              
              let mut rx = active.event_tx.subscribe();
              let (tx, rx_stream) = tokio::sync::mpsc::channel(200);
              
              tokio::spawn(async move {
                  let mut i = 0;
                  while let Ok(event) = rx.recv().await {
                      i += 1;
                      if i % sample_n == 0 && tx.send(Ok(event)).await.is_err() { break; }
                  }
              });
              Ok(Response::new(tokio_stream::wrappers::ReceiverStream::new(rx_stream)))
          } else {
              Err(Status::failed_precondition("No replay"))
          }
    }
    
    async fn control_replay(&self, request: Request<ControlReplayRequest>) -> Result<Response<ReplayStatus>, Status> {
        let req = request.into_inner();
        let active_guard = self.active_replay.lock().await;
         if let Some(active) = &*active_guard {
             match req.action {
                0 => active.paused.store(true, Ordering::SeqCst),
                1 => active.paused.store(false, Ordering::SeqCst),
                2 => { // STEP
                    active.paused.store(true, Ordering::SeqCst);
                    active.step_requested.store(true, Ordering::SeqCst);
                },
                3 => { *active.speed.write().await = req.speed; },
                _ => {}
             }
             Ok(Response::new(ReplayStatus {
                 replay_id: active.replay_id.clone(),
                 state: "OK".to_string(), // Simplified response
                 current_ts: active.current_ts.load(Ordering::Relaxed),
                 speed: *active.speed.read().await,
                 progress: 0.0,
                 events_emitted: active.events_emitted.load(Ordering::Relaxed),
                 quality_status: "".to_string(),
                 usable_for_backtest: true,
                 reject_reason: "".to_string(),
             })) 
         } else {
             Err(Status::not_found("No active replay"))
         }
    }
}
