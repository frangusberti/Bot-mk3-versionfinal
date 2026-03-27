use serde::{Serialize, Deserialize};
use std::path::Path;
use log::{info, warn};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DatasetIndexEntry {
    pub dataset_id: String,
    pub symbol: String,
    pub start_ts: i64,
    pub end_ts: i64,
    pub usable_for_backtest: bool,
    pub coverage: f64,
    pub file_size_bytes: u64,
    pub run_id: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct DatasetIndex {
    pub entries: Vec<DatasetIndexEntry>,
}

impl DatasetIndex {
    pub fn load(path: &Path) -> Self {
        if path.exists() {
            match std::fs::read_to_string(path) {
                Ok(content) => {
                    match serde_json::from_str::<Vec<DatasetIndexEntry>>(&content) {
                        Ok(entries) => {
                            info!("Loaded dataset index with {} entries", entries.len());
                            return Self { entries };
                        }
                        Err(e) => {
                            warn!("Failed to parse dataset index: {}", e);
                        }
                    }
                }
                Err(e) => {
                    warn!("Failed to read dataset index: {}", e);
                }
            }
        }
        Self { entries: Vec::new() }
    }

    pub fn append(&mut self, entry: DatasetIndexEntry) {
        info!("Appending dataset entry: {} / {} ({}-{})", 
            entry.dataset_id, entry.symbol, entry.start_ts, entry.end_ts);
        self.entries.push(entry);
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let json = serde_json::to_string_pretty(&self.entries)
            .map_err(|e| format!("Serialize error: {}", e))?;
        std::fs::write(path, json)
            .map_err(|e| format!("Write error: {}", e))?;
        info!("Saved dataset index ({} entries) to {:?}", self.entries.len(), path);
        Ok(())
    }

    pub fn find_by_symbol(&self, symbol: &str) -> Vec<&DatasetIndexEntry> {
        self.entries.iter()
            .filter(|e| e.symbol == symbol && e.usable_for_backtest)
            .collect()
    }
}
