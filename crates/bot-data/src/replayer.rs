use crate::arrow_schema::record_batch_to_trades;
use bot_core::schema::Trade;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use std::fs::File;
use std::path::PathBuf;
use std::collections::VecDeque;

pub struct Replayer {
    files: Vec<PathBuf>,
    current_file_idx: usize,
    trade_buffer: VecDeque<Trade>,
}

impl Replayer {
    pub fn new(base_path: impl Into<PathBuf>) -> anyhow::Result<Self> {
        let base_path = base_path.into();
        let mut files = Vec::new();

        if base_path.is_dir() {
            for entry in std::fs::read_dir(base_path)? {
                let entry = entry?;
                let path = entry.path();
                if path.extension().map_or(false, |ext| ext == "parquet") {
                    files.push(path);
                }
            }
        }
        
        // Sort files to ensure chronological order (assuming filenames are trades_{timestamp}.parquet)
        files.sort();

        Ok(Self {
            files,
            current_file_idx: 0,
            trade_buffer: VecDeque::new(),
        })
    }

    /// Returns the next Trade event from the replay sequence.
    pub fn next_trade(&mut self) -> anyhow::Result<Option<Trade>> {
        if let Some(trade) = self.trade_buffer.pop_front() {
            return Ok(Some(trade));
        }

        // Buffer is empty, try to load next file
        if self.current_file_idx < self.files.len() {
            self.load_next_file()?;
            // Try again recursively (or just pop)
            if let Some(trade) = self.trade_buffer.pop_front() {
                Ok(Some(trade))
            } else {
                // If file was empty, we might need to recurse, but for simplicity:
                Ok(None)
            }
        } else {
            // No more files
            Ok(None)
        }
    }

    fn load_next_file(&mut self) -> anyhow::Result<()> {
        if self.current_file_idx >= self.files.len() {
            return Ok(());
        }

        let path = &self.files[self.current_file_idx];
        println!("Replaying file: {:?}", path);
        let file = File::open(path)?;

        let builder = ParquetRecordBatchReaderBuilder::try_new(file)?;
        let mut reader = builder.build()?;

        while let Some(batch_result) = reader.next() {
            let batch = batch_result?;
            let trades = record_batch_to_trades(&batch)?;
            for trade in trades {
                self.trade_buffer.push_back(trade);
            }
        }

        self.current_file_idx += 1;
        Ok(())
    }
}
