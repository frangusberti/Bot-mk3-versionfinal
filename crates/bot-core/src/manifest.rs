use serde::{Deserialize, Serialize};
use chrono::{DateTime, Utc};
use std::collections::HashMap;
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunManifest {
    pub run_id: Uuid,
    pub schema_version: String, // "1.0.0"
    pub git_hash: String,
    pub start_time: DateTime<Utc>,
    pub config_snapshot: serde_json::Value,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatasetManifest {
    pub dataset_id: Uuid,
    pub created_at: DateTime<Utc>,
    pub symbol: String,
    pub timeframe: String, // "1m"
    pub date_range: (DateTime<Utc>, DateTime<Utc>),
    pub file_paths: Vec<String>,
    pub stats: HashMap<String, f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrainManifest {
    pub model_id: Uuid,
    pub version: String,
    pub architecture: String,
    pub train_dataset_id: Uuid,
    pub created_at: DateTime<Utc>,
    pub metrics: HashMap<String, f64>, // Accuracy, Sharpe, etc.
}
