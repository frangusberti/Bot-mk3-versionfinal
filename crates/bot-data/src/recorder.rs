use crate::arrow_schema::trades_to_record_batch;
use bot_core::schema::Trade;
use parquet::arrow::ArrowWriter;
use parquet::file::properties::WriterProperties;
use std::fs::File;
use std::path::PathBuf;
// use std::sync::{Arc, Mutex};

pub struct Recorder {
    base_path: PathBuf,
    trade_buffer: Vec<Trade>,
    buffer_limit: usize,
}

impl Recorder {
    pub fn new(base_path: impl Into<PathBuf>, buffer_limit: usize) -> Self {
        Self {
            base_path: base_path.into(),
            trade_buffer: Vec::with_capacity(buffer_limit),
            buffer_limit,
        }
    }

    pub fn record_trade(&mut self, trade: Trade) -> anyhow::Result<()> {
        self.trade_buffer.push(trade);
        if self.trade_buffer.len() >= self.buffer_limit {
            self.flush()?;
        }
        Ok(())
    }

    pub fn flush(&mut self) -> anyhow::Result<()> {
        if self.trade_buffer.is_empty() {
            return Ok(());
        }

        // Generate filename based on timestamp of first trade (or current time)
        // For simplicity: trades_{timestamp}.parquet
        let timestamp = self.trade_buffer[0].timestamp.timestamp_millis();
        let filename = format!("trades_{}.parquet", timestamp);
        let file_path = self.base_path.join(filename);

        println!("Flushing {} trades to {:?}", self.trade_buffer.len(), file_path);

        let batch = trades_to_record_batch(&self.trade_buffer)?;
        let file = File::create(file_path)?;

        let props = WriterProperties::builder().build();
        let mut writer = ArrowWriter::try_new(file, batch.schema(), Some(props))?;

        writer.write(&batch)?;
        writer.close()?;

        self.trade_buffer.clear();
        Ok(())
    }
}
