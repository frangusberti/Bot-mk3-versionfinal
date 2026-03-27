use tonic::{Request, Response, Status};
use bot_core::proto::control_service_server::ControlService;
use bot_core::proto::{RecorderConfig, Response as ProtoResponse, StopRequest, SystemStatus, Empty};
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;
use log::{info, error};
use bot_data::storage::manager::RunManager;
use std::path::PathBuf;

use bot_data::health::HealthMonitor;

use bot_core::proto::MarketSnapshot;
use tokio::sync::broadcast;
use bot_data::normalization::engine::Normalizer;

fn normalize_run_helper(run_id: String, data_dir: PathBuf) {
    let normalizer = Normalizer::new(run_id.clone(), data_dir.clone());
    match normalizer.build_dataset() {
        Ok((dataset_id, report)) => {
            let coverage_pct = report.coverage_pct;
            info!("Run {} normalized successfully. Dataset ID: {}, Quality: {:.2}%", 
                run_id, dataset_id, coverage_pct);
            
            // Auto-indexing
            let index_path = data_dir.join("index/datasets_index.json");
            let mut index = bot_data::dataset_index::DatasetIndex::load(&index_path);
            
            let entry = bot_data::dataset_index::DatasetIndexEntry {
                dataset_id: dataset_id.clone(),
                symbol: report.symbol.clone(),
                start_ts: report.start_ts,
                end_ts: report.end_ts,
                usable_for_backtest: report.usable_for_backtest && report.coverage_pct > 80.0,
                coverage: report.coverage_pct,
                file_size_bytes: 0, // todo: get file size
                run_id: run_id.clone(),
            };
            
            index.append(entry);
            if let Err(e) = index.save(&index_path) {
                log::warn!("Failed to save dataset index: {}", e);
            }
        },
        Err(e) => {
            log::error!("Normalization/Dataset build failed for run {}: {}", run_id, e);
        }
    }
}

type RecorderHandle = (String, mpsc::Sender<()>);

pub struct ControlServiceImpl {
    recorder_handle: Arc<Mutex<Option<RecorderHandle>>>, 
    health_monitor: Arc<HealthMonitor>,
    snapshot_tx: broadcast::Sender<MarketSnapshot>,
    data_dir: PathBuf,
    config: crate::config::RecorderConfig,
    websocket_config: crate::config::WebSocketConfig,
}

impl ControlServiceImpl {
    pub fn new(
        health_monitor: Arc<HealthMonitor>, 
        snapshot_tx: broadcast::Sender<MarketSnapshot>,
        data_dir: PathBuf,
        config: crate::config::RecorderConfig,
        websocket_config: crate::config::WebSocketConfig,
    ) -> Self {
        Self {
            recorder_handle: Arc::new(Mutex::new(None)),
            health_monitor,
            snapshot_tx,
            data_dir,
            config,
            websocket_config,
        }
    }
}

#[tonic::async_trait]
impl ControlService for ControlServiceImpl {
    async fn start_recorder(
        &self,
        request: Request<RecorderConfig>,
    ) -> Result<Response<ProtoResponse>, Status> {
        let config = request.into_inner();
        let symbols = if !config.symbols.is_empty() {
             config.symbols.clone()
        } else {
             vec![config.symbol.clone()]
        };

        info!("Received StartRecorder request for symbols: {:?}", symbols);

        let mut handle = self.recorder_handle.lock().unwrap();
        if handle.is_some() {
            return Ok(Response::new(ProtoResponse {
                success: false,
                message: "Recorder already running".to_string(),
                run_id: handle.as_ref().map(|(id, _)| id.clone()).unwrap_or_default(),
            }));
        }

        // Initialize RunManager
        let data_dir_path = if config.data_dir.is_empty() {
             self.data_dir.clone() 
        } else {
             PathBuf::from(config.data_dir)
        };

        let tag = symbols.first().map(|s| s.as_str()).unwrap_or("multi");
        let run_manager = RunManager::new(data_dir_path.clone(), Some(tag));
        let run_id = run_manager.run_id.clone();
        
        let mut stop_txs = Vec::new();

        // Spawn one recorder task per symbol
        for sym in &symbols {
            let (stop_tx, stop_rx) = mpsc::channel(1);
            let run_id_clone = run_id.clone();
            let symbol_clone = sym.clone();
            let dir_clone = data_dir_path.clone();
            let monitor = self.health_monitor.clone();
            let snapshot_tx = self.snapshot_tx.clone();
            let config_clone = self.config.clone();
            let stall_threshold = self.websocket_config.stall_threshold_sec;

            tokio::spawn(async move {
                crate::engine::run_recorder(run_id_clone, symbol_clone, dir_clone, stop_rx, monitor, snapshot_tx, config_clone, stall_threshold).await;
            });
            stop_txs.push(stop_tx);
        }

        // Create aggregate stop mechanism
        let (agg_stop_tx, mut agg_stop_rx) = mpsc::channel(1);

        // Clone variables for the manager task
        let run_id_clone = run_id.clone();
        let symbols_clone = symbols.clone();
        let dir_clone = data_dir_path.clone();
        let monitor_clone = self.health_monitor.clone();
        let snapshot_tx_clone = self.snapshot_tx.clone();
        
        let rotation_min = config.rotation_interval_minutes;
        let auto_normalize = config.auto_normalize;
        let stall_threshold = self.websocket_config.stall_threshold_sec;
        
        // Spawn manager task that handles rotation and stop signals
        let manager_config = self.config.clone();
        tokio::spawn(async move {
            let mut stop_txs = stop_txs;
            let mut current_run_id = run_id_clone;
            
            loop {
                // Infinite wait if rotation is disabled (0)
                let timeout_duration = if rotation_min > 0 {
                    tokio::time::Duration::from_secs(rotation_min as u64 * 60)
                } else {
                    tokio::time::Duration::from_secs(365 * 24 * 3600 * 10) 
                };

                tokio::select! {
                    _ = agg_stop_rx.recv() => {
                        info!("Recorder manager received stop signal");
                        for tx in &stop_txs {
                            let _ = tx.send(()).await;
                        }
                        
                        if auto_normalize {
                            info!("Triggering auto-normalization for {}", current_run_id);
                            tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
                            normalize_run_helper(current_run_id, dir_clone.clone());
                        }
                        break;
                    }
                    _ = tokio::time::sleep(timeout_duration) => {
                        if rotation_min > 0 { // Should be true if we woke up here, but safety check
                            info!("Rotation triggered for run {}", current_run_id);
                             for tx in &stop_txs {
                                let _ = tx.send(()).await;
                            }
                            
                            if auto_normalize {
                                let rid = current_run_id.clone();
                                let d = dir_clone.clone();
                                tokio::spawn(async move {
                                    tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
                                    normalize_run_helper(rid, d);
                                });
                            }

                            // Start NEW run
                            let tag = symbols_clone.first().map(|s| s.as_str()).unwrap_or("rotated");
                            let new_run_manager = RunManager::new(dir_clone.clone(), Some(tag));
                            current_run_id = new_run_manager.run_id.clone();
                            info!("Starting new rotated run: {}", current_run_id);
                            
                            // Respawn recorders
                            stop_txs.clear();
                            for sym in &symbols_clone {
                                let (stop_tx, stop_rx) = mpsc::channel(1);
                                let rid = current_run_id.clone();
                                let s = sym.clone();
                                let d = dir_clone.clone();
                                let m = monitor_clone.clone();
                                let snap = snapshot_tx_clone.clone();
                                let conf = manager_config.clone();
                                let threshold = stall_threshold;
                                
                                tokio::spawn(async move {
                                    crate::engine::run_recorder(rid, s, d, stop_rx, m, snap, conf, threshold).await;
                                });
                                stop_txs.push(stop_tx);
                            }
                        }
                    }
                }
            }
        });

        *handle = Some((run_id.clone(), agg_stop_tx));

        Ok(Response::new(ProtoResponse {
            success: true,
            message: format!("Started recording {} symbols", symbols.len()),
            run_id,
        }))
    }

    async fn stop_recorder(
        &self,
        _request: Request<StopRequest>,
    ) -> Result<Response<ProtoResponse>, Status> {
        info!("Received StopRecorder request");
        
        let (run_id, stop_tx) = {
            let mut handle = self.recorder_handle.lock().unwrap();
            if let Some((rid, tx)) = handle.take() {
                (rid, tx)
            } else {
                return Ok(Response::new(ProtoResponse {
                    success: false,
                    message: "Recorder not running".to_string(),
                    run_id: "".to_string(),
                }));
            }
        };

        // Send stop signal
        let _ = stop_tx.send(()).await;
        
        info!("Recorder stopped. Triggering normalization for run {}", run_id);
        let normalizer_run_id = run_id.clone();
        let data_dir = self.data_dir.clone(); // Use stored data_dir
        
        tokio::spawn(async move {
             // giving some time for file handles to close
             tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
             normalize_run_helper(normalizer_run_id, data_dir);
        });

        Ok(Response::new(ProtoResponse {
            success: true,
            message: "Recorder stopped".to_string(),
            run_id,
        }))
    }

    async fn get_status(
        &self,
        _request: Request<Empty>,
    ) -> Result<Response<SystemStatus>, Status> {
        let handle = self.recorder_handle.lock().unwrap();
        let active = handle.is_some();
        let run_id = handle.as_ref().map(|(id, _)| id.clone()).unwrap_or_default();

        Ok(Response::new(SystemStatus {
            recorder_active: active,
            current_run_id: run_id,
            events_recorded: 0, // Connect to metrics later
            uptime_seconds: 0.0,
        }))
    }

    async fn delete_run(
        &self,
        request: Request<bot_core::proto::DeleteRunRequest>,
    ) -> Result<Response<bot_core::proto::DeleteResponse>, Status> {
        let req = request.into_inner();
        let run_id = req.run_id;
        info!("Received DeleteRun request for ID: {}", run_id);

        let mut deleted = false;
        
        let possible_roots = vec![
            self.data_dir.join("runs").join("runs"),
            self.data_dir.join("runs"),
            self.data_dir.clone(),
        ];

        for root in possible_roots {
            let run_path = root.join(&run_id);
            if run_path.exists() && run_path.is_dir() {
                if let Err(e) = std::fs::remove_dir_all(&run_path) {
                    return Ok(Response::new(bot_core::proto::DeleteResponse {
                        success: false,
                        message: format!("Failed to delete run directory: {}", e),
                    }));
                }
                deleted = true;
                info!("Deleted run directory: {:?}", run_path);
                break;
            }
        }

        if deleted {
            Ok(Response::new(bot_core::proto::DeleteResponse {
                success: true,
                message: "Run deleted successfully".to_string(),
            }))
        } else {
            Ok(Response::new(bot_core::proto::DeleteResponse {
                success: false,
                message: "Run not found".to_string(),
            }))
        }
    }
}

pub async fn run_retention_policy(config: crate::config::RetentionConfig, data_dir: PathBuf) {
    let check_interval = std::time::Duration::from_secs(config.check_interval_hours * 3600);
    info!("Retention Policy started. Hot: {}d, Warm: {}d, DryRun: {}", 
          config.hot_window_days, config.warm_window_days, config.dry_run);

    loop {
        info!("Running tiered retention check...");
        let runs_dir = data_dir.join("runs");
        if runs_dir.exists() {
             match std::fs::read_dir(&runs_dir) {
                 Ok(entries) => {
                     for entry in entries.flatten() {
                         let path = entry.path();
                         if let Ok(meta) = entry.metadata() {
                             if meta.is_dir() {
                                 if let Ok(modified) = meta.modified() {
                                     if let Ok(age) = modified.elapsed() {
                                         let days = age.as_secs() / 86400;
                                         
                                         if days > config.warm_window_days {
                                             // OLD: Delete entire run
                                             let log_json = format!(
                                                 r#"{{"event": "retention_delete_old", "path": "{:?}", "age_days": {}, "dry_run": {}}}"#,
                                                 path, days, config.dry_run
                                             );
                                             info!("{}", log_json);
                                             
                                             if !config.dry_run {
                                                 if let Err(e) = std::fs::remove_dir_all(&path) {
                                                     error!(r#"{{"event": "retention_error", "path": "{:?}", "error": "{}"}}"#, path, e);
                                                 }
                                             }
                                         } else if days > config.hot_window_days {
                                             // WARM: Cleanup events, keep experience
                                             let events_dir = path.join("events");
                                             if events_dir.exists() && events_dir.is_dir() {
                                                 let log_json = format!(
                                                     r#"{{"event": "retention_cleanup_warm", "path": "{:?}", "age_days": {}, "dry_run": {}}}"#,
                                                     events_dir, days, config.dry_run
                                                 );
                                                 info!("{}", log_json);
                                                 
                                                 if !config.dry_run {
                                                     if let Err(e) = std::fs::remove_dir_all(&events_dir) {
                                                         error!(r#"{{"event": "retention_error", "path": "{:?}", "error": "{}"}}"#, events_dir, e);
                                                     }
                                                 }
                                             }
                                         }
                                     }
                                 }
                             }
                         }
                     }
                 },
                 Err(e) => log::error!("Failed to read runs directory: {}", e)
             }
        }
        
        tokio::time::sleep(check_interval).await;
    }
}
