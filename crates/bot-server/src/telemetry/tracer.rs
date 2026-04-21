use std::fs::File;
use std::path::{Path, PathBuf};
use chrono::{DateTime, Utc};
use serde::{Serialize, Deserialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct DecisionLogEntry {
    pub timestamp: i64,
    pub mid_price: f64,
    pub action: String,
    pub action_source: String, // none | policy | synthetic | veto_blocked
    pub veto_type: Option<String>,
    pub features_summary: String, // Short JSON or string
    pub observation_full: Option<String>, // Large JSON blob for key events
    pub health_ws: String,
}

pub struct PersistentTracer {
    log_dir: PathBuf,
    current_hourly_file: Option<File>,
}

impl PersistentTracer {
    pub fn new(base_dir: &Path) -> Self {
        let log_dir = base_dir.join("shadow");
        std::fs::create_dir_all(&log_dir).unwrap_or_default();
        Self {
            log_dir,
            current_hourly_file: None,
        }
    }

    pub fn log_market_event(&mut self, event: &bot_data::normalization::schema::NormalizedMarketEvent) {
        let filename = Utc::now().format("%Y%m%d_%H00_market_trace.jsonl").to_string();
        let path = self.log_dir.join(filename);
        if let Ok(mut file) = std::fs::OpenOptions::new().append(true).create(true).open(path) {
            use std::io::Write;
            let _ = writeln!(file, "{}", serde_json::to_string(&event).unwrap_or_default());
        }
    }

    pub fn log_decision(&mut self, entry: DecisionLogEntry) {
        let filename = Utc::now().format("%Y%m%d_%H00_decision_trace.jsonl").to_string();
        let path = self.log_dir.join(filename);
        if let Ok(mut file) = std::fs::OpenOptions::new().append(true).create(true).open(path) {
            use std::io::Write;
            let _ = writeln!(file, "{}", serde_json::to_string(&entry).unwrap_or_default());
        }
    }
}
