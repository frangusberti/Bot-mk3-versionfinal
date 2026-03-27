use std::path::PathBuf;
use std::fs;
use serde_json;
use crate::services::analytics::engine::TradeRecord;
use crate::services::analytics::metrics::{SessionMetrics, EquityPoint};

pub struct AnalyticsPersistence {
    base_dir: PathBuf,
}

impl AnalyticsPersistence {
    pub fn new(base_dir: &str) -> Self {
        let path = PathBuf::from(base_dir);
        if !path.exists() {
            fs::create_dir_all(&path).ok();
        }
        Self { base_dir: path }
    }

    pub async fn save_session(
        &self, 
        session_id: &str, 
        metadata: Option<&crate::services::analytics::metadata::RunMetadata>,
        trades: &[TradeRecord], 
        trips: &[crate::services::analytics::engine::RoundTripRecord],
        equity: &[EquityPoint], 
        metrics: &SessionMetrics,
        divergence: &[crate::services::analytics::engine::SimVsRealDivergence]
    ) -> Result<(), String> {
        let session_dir = self.base_dir.join(session_id);
        fs::create_dir_all(&session_dir).map_err(|e| e.to_string())?;
        
        // Save Metadata JSON if available
        if let Some(meta) = metadata {
            let meta_json = serde_json::to_string_pretty(meta).map_err(|e| e.to_string())?;
            fs::write(session_dir.join("metadata.json"), meta_json).map_err(|e| e.to_string())?;
        }

        // Save Metrics JSON
        let metrics_json = serde_json::to_string_pretty(metrics).map_err(|e| e.to_string())?;
        fs::write(session_dir.join("metrics.json"), metrics_json).map_err(|e| e.to_string())?;
        
        // Save Trades
        let trades_json = serde_json::to_string(trades).map_err(|e| e.to_string())?;
        fs::write(session_dir.join("trades.json"), trades_json).map_err(|e| e.to_string())?;

        // Save Round Trips
        let trips_json = serde_json::to_string(trips).map_err(|e| e.to_string())?;
        fs::write(session_dir.join("roundtrips.json"), trips_json).map_err(|e| e.to_string())?;
        
        // Save Equity Curve JSON (MVP)
        let equity_json = serde_json::to_string(equity).map_err(|e| e.to_string())?;
        fs::write(session_dir.join("equity.json"), equity_json).map_err(|e| e.to_string())?;
        
        // Save Divergence Tracking
        let div_json = serde_json::to_string(divergence).map_err(|e| e.to_string())?;
        fs::write(session_dir.join("divergence.json"), div_json).map_err(|e| e.to_string())?;

        Ok(())
    }

    pub fn append_candidate_record(&self, session_id: &str, record: &crate::services::analytics::candidate::CandidateDecisionRecord) {
        let session_dir = self.base_dir.join(session_id);
        if !session_dir.exists() {
            fs::create_dir_all(&session_dir).ok();
        }
        
        let path = session_dir.join("candidates.jsonl");
        if let Ok(json_str) = serde_json::to_string(record) {
            use std::io::Write;
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
                let _ = writeln!(f, "{}", json_str);
            }
        }
    }

    pub fn append_config_change(&self, session_id: &str, change: &crate::services::analytics::engine::ConfigChangeEvent) {
        let session_dir = self.base_dir.join(session_id);
        if !session_dir.exists() {
            fs::create_dir_all(&session_dir).ok();
        }
        
        let path = session_dir.join("events.jsonl");
        if let Ok(json_str) = serde_json::to_string(change) {
            use std::io::Write;
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
                let _ = writeln!(f, "{}", json_str);
            }
        }
    }
}
