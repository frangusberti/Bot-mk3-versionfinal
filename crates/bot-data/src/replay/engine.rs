use std::path::PathBuf;
use std::collections::BinaryHeap;
use std::cmp::Reverse;
use std::fs::File;
use std::io::BufReader;
use serde_json::Value;

use crate::replay::cursor::ReplayRow;
use crate::replay::reader::BatchedReplayReader;
use crate::replay::types::ReplayConfig;
use crate::replay::events::ReplayEvent;

pub struct ReplayEngine {
    readers: Vec<BatchedReplayReader>,
    // Min-heap for merge sort. Stores (ReplayRow, reader_index)
    // We use Reverse because BinaryHeap is a MaxHeap
    merge_queue: BinaryHeap<Reverse<(ReplayRow, usize)>>,
    _config: ReplayConfig,
    _dataset_path: PathBuf,
}

impl ReplayEngine {
    pub fn new(dataset_path: PathBuf, config: ReplayConfig) -> anyhow::Result<Self> {
        // 1. Quality Gating
        Self::check_quality(&dataset_path, &config)?;

        // 2. Discover files
        // Assuming dataset_path contains .parquet files
        // We'll look for all .parquet files in the directory
        let mut files = Vec::new();
        if dataset_path.is_dir() {
            for entry in std::fs::read_dir(&dataset_path)? {
                let entry = entry?;
                let path = entry.path();
                if path.extension().and_then(|s| s.to_str()) == Some("parquet") {
                    files.push(path);
                }
            }
        } else {
            // single file
            files.push(dataset_path.clone());
        }
        
        // Sort files for deterministic "file_part_index" assignment if needed, 
        // though typically they are part-0000.parquet etc.
        files.sort();

        // 3. Initialize Readers and Merge Queue
        let mut readers = Vec::with_capacity(files.len());
        let mut merge_queue = BinaryHeap::with_capacity(files.len());

        for (i, file_path) in files.into_iter().enumerate() {
             let mut reader = BatchedReplayReader::new(
                 file_path, 
                 i as u32, 
                 config.clock_mode,
                 config.debug_include_raw
             )?;
             
             // Prime the queue
             if let Some(row) = reader.next() {
                 merge_queue.push(Reverse((row, i)));
             }
             
             readers.push(reader);
        }

        let mut engine = Self {
            readers,
            merge_queue,
            _config: config,
            _dataset_path: dataset_path,
        };

        // Fast-forward if start_ts is set
        if let Some(start_ts) = engine._config.start_ts {
             while let Some(std::cmp::Reverse((row, _))) = engine.merge_queue.peek() {
                 if row.event.ts_exchange < start_ts {
                     engine.next_event();
                 } else {
                     break;
                 }
             }
        }

        Ok(engine)
    }

    fn check_quality(dataset_path: &std::path::Path, config: &ReplayConfig) -> anyhow::Result<()> {
        let report_path = dataset_path.join("quality_report.json");
        if report_path.exists() {
            let file = File::open(report_path)?;
            let reader = BufReader::new(file);
            let json: Value = serde_json::from_reader(reader)?;
            
            // Check usable_for_backtest (default to true if missing for legacy datasets)
            let usable = json.get("usable_for_backtest")
                .and_then(|v| v.as_bool())
                .unwrap_or(true);
                
            if !usable && !config.allow_bad_quality {
                let reason = json.get("reject_reason")
                    .and_then(|s| s.as_str())
                    .unwrap_or("Unknown quality issues");
                return Err(anyhow::anyhow!(
                    "Dataset Quality Check Failed: {}. Set allow_bad_quality=true to enforce replay.", 
                    reason
                ));
            }
        }
        Ok(())
    }

    pub fn next_event(&mut self) -> Option<ReplayEvent> {
        // Pop the smallest item (min-heap via Reverse)
        // Reverse<(Row, idx)> -> .0 is (Row, idx)
        if let Some(Reverse((row, reader_idx))) = self.merge_queue.pop() {
            let event = row.event.clone();
            
            // Refill from the same reader
            if let Some(next_row) = self.readers[reader_idx].next() {
                self.merge_queue.push(Reverse((next_row, reader_idx)));
            }
            
            return Some(event);
        }
        
        None
    }
}
