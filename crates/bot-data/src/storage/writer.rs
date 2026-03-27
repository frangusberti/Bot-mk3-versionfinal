use anyhow::Result;
use arrow::array::{Float64Array, Int64Array, StringArray, BooleanArray};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use parquet::arrow::ArrowWriter;
use parquet::file::properties::WriterProperties;
use std::fs::File;
use std::path::PathBuf;
use std::sync::Arc;
 // Or a unified event

// A unified event structure for storage
pub struct MarketEvent {
    pub local_timestamp: i64,
    pub exchange_timestamp: i64,
    pub symbol: String,
    pub event_type: String,
    pub price: Option<f64>,
    pub quantity: Option<f64>,
    pub bid_price: Option<f64>,
    pub ask_price: Option<f64>,
    pub is_buyer_maker: Option<bool>,
    pub payload: String, // New field for raw JSON
}

pub struct ParquetWriter {
    file_path: PathBuf,
    writer: Option<ArrowWriter<File>>,
    schema: Arc<Schema>,
    batch_buffer: Vec<MarketEvent>,
    buffer_limit: usize,
}

impl ParquetWriter {
    pub fn new(path: PathBuf) -> Self {
        let schema = Arc::new(Schema::new(vec![
            Field::new("local_timestamp", DataType::Int64, false),
            Field::new("exchange_timestamp", DataType::Int64, false),
            Field::new("symbol", DataType::Utf8, false),
            Field::new("event_type", DataType::Utf8, false),
            Field::new("price", DataType::Float64, true),
            Field::new("quantity", DataType::Float64, true),
            Field::new("bid_price", DataType::Float64, true),
            Field::new("ask_price", DataType::Float64, true),
            Field::new("is_buyer_maker", DataType::Boolean, true),
            Field::new("payload", DataType::Utf8, false),
        ]));

        Self {
            file_path: path,
            writer: None,
            schema,
            batch_buffer: Vec::new(),
            buffer_limit: 1000,
        }
    }

    fn init_writer(&mut self) -> Result<()> {
        if self.writer.is_none() {
            let file = File::create(&self.file_path)?;
            let props = WriterProperties::builder()
                .set_compression(parquet::basic::Compression::ZSTD(Default::default()))
                .build();
            let writer = ArrowWriter::try_new(file, self.schema.clone(), Some(props))?;
            self.writer = Some(writer);
        }
        Ok(())
    }

    pub fn write(&mut self, event: MarketEvent) -> Result<()> {
        self.batch_buffer.push(event);
        if self.batch_buffer.len() >= self.buffer_limit {
            self.flush()?;
        }
        Ok(())
    }

    pub fn flush(&mut self) -> Result<()> {
        if self.batch_buffer.is_empty() {
            return Ok(());
        }

        self.init_writer()?;

        let local_ts: Vec<i64> = self.batch_buffer.iter().map(|e| e.local_timestamp).collect();
        let ex_ts: Vec<i64> = self.batch_buffer.iter().map(|e| e.exchange_timestamp).collect();
        let symbols: Vec<String> = self.batch_buffer.iter().map(|e| e.symbol.clone()).collect();
        let event_types: Vec<String> = self.batch_buffer.iter().map(|e| e.event_type.clone()).collect();
        let prices: Vec<Option<f64>> = self.batch_buffer.iter().map(|e| e.price).collect();
        let quantities: Vec<Option<f64>> = self.batch_buffer.iter().map(|e| e.quantity).collect();
        let bids: Vec<Option<f64>> = self.batch_buffer.iter().map(|e| e.bid_price).collect();
        let asks: Vec<Option<f64>> = self.batch_buffer.iter().map(|e| e.ask_price).collect();
        let is_buyer_makers: Vec<Option<bool>> = self.batch_buffer.iter().map(|e| e.is_buyer_maker).collect();
        let payloads: Vec<String> = self.batch_buffer.iter().map(|e| e.payload.clone()).collect();

        let batch = RecordBatch::try_new(
            self.schema.clone(),
            vec![
                Arc::new(Int64Array::from(local_ts)),
                Arc::new(Int64Array::from(ex_ts)),
                Arc::new(StringArray::from(symbols)),
                Arc::new(StringArray::from(event_types)),
                Arc::new(Float64Array::from(prices)),
                Arc::new(Float64Array::from(quantities)),
                Arc::new(Float64Array::from(bids)),
                Arc::new(Float64Array::from(asks)),
                Arc::new(BooleanArray::from(is_buyer_makers)),
                Arc::new(StringArray::from(payloads)),
            ],
        )?;

        if let Some(writer) = &mut self.writer {
            writer.write(&batch)?;
        }
        
        self.batch_buffer.clear();
        Ok(())
    }

    pub fn close(&mut self) -> Result<()> {
        self.flush()?;
        if let Some(writer) = self.writer.take() {
            writer.close()?;
        }
        Ok(())
    }

    pub fn current_file_size(&self) -> u64 {
        if self.file_path.exists() {
            std::fs::metadata(&self.file_path).map(|m| m.len()).unwrap_or(0)
        } else {
            0
        }
    }
}
