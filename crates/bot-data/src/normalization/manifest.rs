use serde::{Serialize, Deserialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]

pub struct DatasetManifest {
    pub dataset_id: String,
    pub source_run_id: String,
    pub time_range: (i64, i64), // Start, End canonical
    pub schema_version: u16,
    pub streams_present: Vec<String>,
    pub quality_summary: QualitySummary,
    pub created_at: String, // ISO 8601
    pub file_index: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QualitySummary {
    pub overall_status: String,
    pub coverage_pct: f64,
    pub total_gaps: i64,
    pub missing_streams: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct QualityReport {
    pub symbol: String,
    pub start_ts: i64,
    pub end_ts: i64,
    pub overall_status: String,
    pub coverage_pct: f64,
    pub total_gaps: i64,
    pub missing_streams: Vec<String>,
    pub usable_for_training: bool,
    pub usable_for_backtest: bool,
    pub streams: HashMap<String, StreamQuality>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StreamQuality {
    pub coverage_pct: f64,
    pub lag_p99_ms: f64,
    pub events_per_sec: f64,
    pub gap_count: i64,
    pub drift_ms_avg: f64,
    pub status: String,
}
