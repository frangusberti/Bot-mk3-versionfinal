use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use tokio::sync::mpsc;
use log::{info, error};
use flate2::write::GzEncoder;
use flate2::Compression;

#[derive(Debug, Clone)]
pub struct ParitySnapshot {
    pub recv_time: i64,
    pub exchange_time: i64,
    pub step_seq: u64,
    pub symbol: String,
    pub obs: Vec<f32>,
    pub action: String,
}

pub struct LiveCaptureWriter {
    tx: mpsc::Sender<ParitySnapshot>,
}

impl LiveCaptureWriter {
    pub fn new(run_id: &str) -> Self {
        let (tx, mut rx) = mpsc::channel::<ParitySnapshot>(10000);
        let path = PathBuf::from(format!("runs/{}/parity", run_id));
        std::fs::create_dir_all(&path).ok();
        
        let path_file = path.join("live_obs.jsonl.gz");
        
        // Spawn asynchronous background writer
        tokio::spawn(async move {
            let file = match File::create(&path_file) {
                Ok(f) => f,
                Err(e) => {
                    error!("Failed to create live_obs parity file: {}", e);
                    return;
                }
            };
            let mut encoder = GzEncoder::new(file, Compression::default());

            while let Some(snapshot) = rx.recv().await {
                // Formatting tuple to JSON
                let json = serde_json::json!({
                    "recv_time": snapshot.recv_time,
                    "exchange_time": snapshot.exchange_time,
                    "step_seq": snapshot.step_seq,
                    "symbol": snapshot.symbol,
                    "obs": snapshot.obs,
                    "action": snapshot.action,
                });
                
                let line = format!("{}\n", json.to_string());
                if let Err(e) = encoder.write_all(line.as_bytes()) {
                    error!("Parity LiveCapture Write EOF Error: {}", e);
                    break;
                }
            }
            
            // Channel closed
            let _ = encoder.finish();
            info!("LiveCapture parity logger shutdown safely.");
        });

        Self { tx }
    }

    pub fn send(&self, recv_time: i64, exchange_time: i64, step_seq: u64, symbol: String, obs: Vec<f32>, action: String) {
        let snap = ParitySnapshot {
            recv_time,
            exchange_time,
            step_seq,
            symbol,
            obs,
            action,
        };
        // Non-blocking try_send
        if let Err(e) = self.tx.try_send(snap) {
            error!("LiveCapture queue full or dropped: {}", e);
        }
    }
}

use crate::replay::engine::ReplayEngine;
use crate::replay::types::ReplayConfig;
use crate::features_v2::{FeatureEngineV2, FeatureEngineV2Config};
use crate::normalization::schema::{NormalizedMarketEvent, TimeMode};

pub struct ReplayRecompute;

impl ReplayRecompute {
    /// Re-runs the `FeatureEngineV2` across an identical run dataset and generates an offline validation vector.
    pub fn run_recompute(run_id: &str, symbol: &str) -> std::io::Result<()> {
        let dataset_path = PathBuf::from(format!("runs/{}/datasets/{}_{}", run_id, symbol, "dataset")); // Adapting for parity layout

        // We assume valid layout. Let's create an offline encoder
        let out_path = PathBuf::from(format!("runs/{}/parity/replay_obs.jsonl.gz", run_id));
        std::fs::create_dir_all(out_path.parent().unwrap())?;
        let file = File::create(&out_path)?;
        let mut encoder = GzEncoder::new(file, Compression::default());

        let replay_cfg = ReplayConfig {
            speed: 0.0, // MAX SPEED
            allow_bad_quality: true,
            ..Default::default()
        };

        // Note: Using a basic config matcher for feature engine. 
        // In reality, this should parse the run's metadata.json to perfectly sync initial config (e.g., TF)
        let feature_cfg = FeatureEngineV2Config {
            interval_ms: 1000, 
            symbol: symbol.to_string(),
            time_mode: TimeMode::EventTimeOnly, 
            recv_time_lag_ms: 0,
            micro_strict: false,
            tape_zscore_clamp: (-5.0, 5.0),
            slow_tf: "1s".to_string(),
            telemetry_enabled: false,
            telemetry_window_ms: 60_000,
            ..Default::default()
        };

        let mut replay = match ReplayEngine::new(dataset_path, replay_cfg) {
            Ok(r) => r,
            Err(e) => {
                error!("ReplayRecompute missing dataset: {}", e);
                return Err(std::io::Error::new(std::io::ErrorKind::NotFound, e.to_string()));
            }
        };

        let mut engine = FeatureEngineV2::new(feature_cfg);
        let mut step_seq = 0u64;

        info!("Starting ReplayRecompute for run {}...", run_id);

        while let Some(event) = replay.next_event() {
            let norm = NormalizedMarketEvent {
                schema_version: 1,
                run_id: run_id.to_string(),
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

            engine.update(&norm);

            // Recreate identical timing behavior:
            if let Some(features) = engine.maybe_emit(norm.time_canonical) {
                step_seq += 1;
                let (obs_vec, _clamped) = features.to_obs_vec();
                let json = serde_json::json!({
                    "recv_time": norm.time_canonical,
                    "exchange_time": norm.time_exchange,
                    "step_seq": step_seq,
                    "symbol": symbol.to_string(),
                    "obs": obs_vec,
                    "action": "REPLAY" // Ignored offline
                });
                
                let line = format!("{}\n", json.to_string());
                encoder.write_all(line.as_bytes())?;
            }
        }
        
        encoder.finish()?;
        info!("ReplayRecompute finished rendering for run {}.", run_id);
Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_live_capture_init() {
        let run_id = "test_run_124";
        let logger = LiveCaptureWriter::new(run_id);
        
        let mut obs = vec![1.0; 76];
        obs[1] = 0.0;
        
        logger.send(1000, 1000, 1, "BTCUSDT".to_string(), obs.clone(), "OPEN_LONG".to_string());
        
        let path = PathBuf::from(format!("runs/{}/parity/live_obs.jsonl.gz", run_id));
        assert!(path.parent().unwrap().exists());
    }
}
