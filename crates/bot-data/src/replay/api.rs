use tokio::sync::mpsc;
use tokio::sync::broadcast;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::Mutex;
use log::{info, error, warn};

use crate::replay::types::{ReplayConfig, ReplayEvent};
use crate::replay::cursor::ReplayCursor;
use crate::replay::scheduler::ReplayScheduler;

pub struct ReplayEngine {
    dataset_id: String,
    config: ReplayConfig,
    cursor: Option<ReplayCursor>,
    scheduler: ReplayScheduler,
    event_tx: broadcast::Sender<ReplayEvent>, 
    stop_rx: broadcast::Receiver<()>,
    is_running: bool,
}

impl ReplayEngine {
    pub fn new(
        dataset_id: String,
        config: ReplayConfig,
        event_tx: broadcast::Sender<ReplayEvent>,
        stop_rx: broadcast::Receiver<()>,
    ) -> Self {
        Self {
            dataset_id,
            config: config.clone(),
            cursor: None,
            scheduler: ReplayScheduler::new(config),
            event_tx,
            stop_rx,
            is_running: false,
        }
    }

    pub async fn initialize(&mut self, base_dir: PathBuf) -> anyhow::Result<()> {
        // 1. Locate dataset
        let dataset_path = base_dir.join(&self.dataset_id);
        let manifest_path = dataset_path.join("dataset_manifest.json");
        
        // 2. Load Manifest (TODO: Add proper error handling/check existence)
        // let manifest_file = std::fs::File::open(&manifest_path)?;
        // let manifest: DatasetManifest = serde_json::from_reader(manifest_file)?;
        
        // 3. Find usage parts in normalized_events directory
        let events_dir = dataset_path.join("normalized_events");
        let mut parts = Vec::new();
        
        // Simple scan for .parquet files
        if events_dir.exists() {
            for entry in std::fs::read_dir(events_dir)? {
                let entry = entry?;
                let path = entry.path();
                if path.extension().and_then(|e| e.to_str()) == Some("parquet") {
                    parts.push(path);
                }
            }
        }
        // Sort to ensure deterministic load order if filenames matter
        parts.sort(); // Very important for determinism if multiple parts cover same time range (unlikely but safe)

        if parts.is_empty() {
            return Err(anyhow::anyhow!("No parquet files found for dataset {}", self.dataset_id));
        }

        // 4. Initialize Cursor
        let cursor = ReplayCursor::new(parts, self.config.clock_mode)?;
        self.cursor = Some(cursor);
        
        info!("ReplayEngine initialized for dataset: {}", self.dataset_id);
        Ok(())
    }

    pub async fn run(&mut self) {
        self.is_running = true;
        info!("Replay started: {}", self.dataset_id);
        
        loop {
            // Check for stop signal
            // Using try_recv so we don't block if no stop signal
            if let Ok(_) = self.stop_rx.try_recv() {
                info!("Replay stop signal received.");
                break;
            }

            // Get next event from cursor
            let event = if let Some(cursor) = &mut self.cursor {
                cursor.next()
            } else {
                break; // Should not happen if initialized
            };

            if let Some(evt) = event {
                // Wait for scheduled time
                self.scheduler.wait_for_event(&evt).await;
                
                // Broadcast event
                if let Err(e) = self.event_tx.send(evt) {
                    // Receiver dropped, stop replay? or just log?
                    // Typically means no active clients, which might be fine, but we should probably stop if strict.
                    // For now, just log trace.
                    // warn!("Failed to broadcast event: {}", e);
                }
            } else {
                info!("Replay finished (EOF).");
                break;
            }
        }
        
        self.is_running = false;
    }
}
