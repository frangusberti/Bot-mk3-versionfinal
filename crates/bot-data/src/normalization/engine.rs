use super::schema::NormalizedMarketEvent;
use super::manifest::QualityReport;
use parquet::file::reader::{FileReader, SerializedFileReader};
use parquet::arrow::ArrowWriter;
use parquet::file::properties::WriterProperties;
use arrow::array::{
    StringArray, Int64Array, Float64Array, UInt16Array
};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use std::fs::File;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use anyhow::{Result, anyhow};
// use sha2::{Sha256, Digest};
// use hex;
use serde_json;
use chrono;
use log::{info, warn, error};

pub struct Normalizer {
    run_id: String,
    data_dir: PathBuf,
}

impl Normalizer {
    pub fn new(run_id: String, data_dir: PathBuf) -> Self {
        Self { run_id, data_dir }
    }

    fn find_events_dir(&self) -> Option<PathBuf> {
        // Try multiple structures
        // 1. data_dir/runs/<run_id>/events
        // 2. data_dir/runs/runs/<run_id>/events (observed double nesting)
        
        let path1 = self.data_dir.join("runs").join(&self.run_id).join("events");
        if path1.exists() { return Some(path1); }

        let path2 = self.data_dir.join("runs").join("runs").join(&self.run_id).join("events");
        if path2.exists() { return Some(path2); }
        
        None
    }

    pub fn normalize(&self) -> Result<Vec<NormalizedMarketEvent>> {
        let events_path = self.find_events_dir()
            .ok_or_else(|| anyhow!("Could not find events directory for run {}", self.run_id))?;
        
        info!("Normalizing run {} from {:?}", self.run_id, events_path);

        let mut normalization_buffer = Vec::new();
        let mut files_scanned = 0;

        // Scan all parquet files
        if let Ok(entries) = std::fs::read_dir(events_path) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().and_then(|s| s.to_str()) == Some("parquet") {
                    files_scanned += 1;
                    if let Err(e) = self.process_file(&path, &mut normalization_buffer) {
                        warn!("Failed to process raw file {:?}: {}", path, e);
                    }
                }
            }
        }
        
        info!("Scanned {} files, extracted {} events", files_scanned, normalization_buffer.len());

        // Sort by time
        normalization_buffer.sort_by_key(|e| e.time_exchange);
        
        Ok(normalization_buffer)
    }
    
    fn process_file(&self, path: &Path, buffer: &mut Vec<NormalizedMarketEvent>) -> Result<()> {
        let file = File::open(path)?;
        let reader = SerializedFileReader::new(file)?;
        let mut row_iter = reader.get_row_iter(None)?;

        while let Some(Ok(row)) = row_iter.next() {
            if let Some(event) = row_to_normalized_event(row, &self.run_id) {
                buffer.push(event);
            }
        }
        Ok(())
    }

    pub fn build_dataset(&self) -> Result<(String, QualityReport)> {
        info!("Starting dataset build for run {}", self.run_id);
        
        let events = self.normalize()?;
        
        if events.is_empty() {
            error!("Normalization produced 0 events. Aborting dataset creation.");
            return Err(anyhow!("No events found for run {}", self.run_id));
        }

        let report = super::quality::QualityAnalyzer::analyze(&events);
        
        // Deterministic Dataset ID logic
        // User requested Readable ID: SYMBOL_DATE_SIZE_SERIAL
        // We use run_id (which usually contains timestamp) as base if possible, or generate new one.
        
        let dataset_id = format!("{}_DS", self.run_id);
        
        // Log the change
        info!("Generated Readable Dataset ID: {}", dataset_id);

        // 1. Determine Output Directory (Handle nested runs if needed)
        // We write to data_dir/runs/runs/<run_id>/datasets/<dataset_id> to match existing structure
        // Or cleaner: data_dir/datasets/<dataset_id> ?
        // User requested maintaining "windows runs/runs" alignment if recorder uses it.
        // Let's use the parent of `events` dir if found, or strictly `runs/runs`.
        // To be safe and compatible with Replay Service search logic (which checks nested), we stick to the discovered structure.
        
        let active_events_dir = self.find_events_dir()
            .ok_or_else(|| anyhow!("Run directory invalid"))?;
            
        // Go up one level from 'events' -> run_root
        let run_root = active_events_dir.parent().unwrap(); 
        let dataset_dir = run_root.join("datasets").join(&dataset_id);
        
        std::fs::create_dir_all(&dataset_dir)?;
        info!("Creating dataset in {:?}", dataset_dir);

        // 2. Write Normalized Parquet (Arrow)
        let parquet_path = dataset_dir.join("normalized_events.parquet");
        let file = File::create(&parquet_path)?;
        
        let schema = create_arrow_schema();
        
        let props = WriterProperties::builder()
            .set_compression(parquet::basic::Compression::ZSTD(Default::default()))
            .build();
            
        // Initialize writer with schema
        let mut writer = ArrowWriter::try_new(file, schema.clone(), Some(props))?;
        
        // Write in chunks to avoid Arrow array overflow (2GB limit)
        // 500k events * ~100-500 bytes ~= 50MB - 250MB per chunk. Safe.
        const CHUNK_SIZE: usize = 500_000;
        
        for (i, chunk) in events.chunks(CHUNK_SIZE).enumerate() {
            let batch = events_to_record_batch(chunk, schema.clone())?;
            writer.write(&batch)?;
            if i % 10 == 0 {
                info!("Written chunk {}/{}", i + 1, events.len().div_ceil(CHUNK_SIZE));
            }
        }
        
        writer.close()?; // CRITICAL: Ensure footer is written
        
        // Verify Size
        let metadata = std::fs::metadata(&parquet_path)?;
        let size = metadata.len();
        info!("Written normalized_events.parquet: {} rows, {} bytes", events.len(), size);
        
        if size == 0 {
             return Err(anyhow!("Generated parquet file is 0 bytes!"));
        }

        // 3. Write Quality Report
        let report_file = File::create(dataset_dir.join("quality_report.json"))?;
        serde_json::to_writer_pretty(report_file, &report)?;

        // 4. Write Manifest
        let quality_summary = super::manifest::QualitySummary {
            overall_status: report.overall_status.clone(),
            coverage_pct: report.coverage_pct,
            total_gaps: report.total_gaps,
            missing_streams: report.missing_streams.clone(),
        };

        let start_time = events.first().map(|e| e.time_canonical).unwrap_or(0);
        let end_time = events.last().map(|e| e.time_canonical).unwrap_or(0);

        let manifest = super::manifest::DatasetManifest {
            dataset_id: dataset_id.clone(),
            source_run_id: self.run_id.clone(),
            time_range: (start_time, end_time),
            schema_version: 1,
            streams_present: report.streams.keys().cloned().collect(),
            quality_summary,
            created_at: chrono::Utc::now().to_rfc3339(),
            file_index: vec!["normalized_events.parquet".to_string(), "quality_report.json".to_string()],
        };
        let manifest_file = File::create(dataset_dir.join("dataset_manifest.json"))?;
        serde_json::to_writer_pretty(manifest_file, &manifest)?;
        
        Ok((dataset_id, report))
    }
}


fn create_arrow_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("schema_version", DataType::UInt16, false),
        Field::new("run_id", DataType::Utf8, false),
        Field::new("exchange", DataType::Utf8, false),
        Field::new("market_type", DataType::Utf8, false),
        Field::new("symbol", DataType::Utf8, false),
        Field::new("stream_name", DataType::Utf8, false),
        Field::new("event_type", DataType::Utf8, false),
        Field::new("time_exchange", DataType::Int64, false),
        Field::new("time_local", DataType::Int64, false),
        Field::new("time_canonical", DataType::Int64, false),
        Field::new("price", DataType::Float64, true),
        Field::new("qty", DataType::Float64, true),
        Field::new("side", DataType::Utf8, true),
        Field::new("best_bid", DataType::Float64, true),
        Field::new("best_ask", DataType::Float64, true),
        Field::new("mark_price", DataType::Float64, true),
        Field::new("funding_rate", DataType::Float64, true),
        Field::new("liquidation_price", DataType::Float64, true),
        Field::new("liquidation_qty", DataType::Float64, true),
        Field::new("update_id_first", DataType::Int64, true),
        Field::new("update_id_final", DataType::Int64, true),
        Field::new("update_id_prev", DataType::Int64, true),
        Field::new("payload_json", DataType::Utf8, false),
    ]))
}

fn events_to_record_batch(events: &[NormalizedMarketEvent], schema: Arc<Schema>) -> Result<RecordBatch> {
    let _len = events.len();
    
    // Builders or direct Vec map? Direct Vec map is faster/simpler for fixed arrays
    let schema_ver: UInt16Array = events.iter().map(|e| Some(e.schema_version)).collect();
    let run_id: StringArray = events.iter().map(|e| Some(e.run_id.as_str())).collect();
    let exchange: StringArray = events.iter().map(|e| Some(e.exchange.as_str())).collect();
    let market_type: StringArray = events.iter().map(|e| Some(e.market_type.as_str())).collect();
    let symbol: StringArray = events.iter().map(|e| Some(e.symbol.as_str())).collect();
    let stream_name: StringArray = events.iter().map(|e| Some(e.stream_name.as_str())).collect();
    let event_type: StringArray = events.iter().map(|e| Some(e.event_type.as_str())).collect();
    
    let ts_ex: Int64Array = events.iter().map(|e| Some(e.time_exchange)).collect();
    let ts_loc: Int64Array = events.iter().map(|e| Some(e.time_local)).collect();
    let ts_can: Int64Array = events.iter().map(|e| Some(e.time_canonical)).collect();
    
    let price: Float64Array = events.iter().map(|e| e.price).collect();
    let qty: Float64Array = events.iter().map(|e| e.qty).collect();
    let side: StringArray = events.iter().map(|e| e.side.as_deref()).collect(); 
    
    let best_bid: Float64Array = events.iter().map(|e| e.best_bid).collect();
    let best_ask: Float64Array = events.iter().map(|e| e.best_ask).collect();
    let mark_price: Float64Array = events.iter().map(|e| e.mark_price).collect();
    let funding: Float64Array = events.iter().map(|e| e.funding_rate).collect();
    let liq_price: Float64Array = events.iter().map(|e| e.liquidation_price).collect();
    let liq_qty: Float64Array = events.iter().map(|e| e.liquidation_qty).collect();
    
    let update_id_first: Int64Array = events.iter().map(|e| e.update_id_first).collect();
    let update_id_final: Int64Array = events.iter().map(|e| e.update_id_final).collect();
    let update_id_prev: Int64Array = events.iter().map(|e| e.update_id_prev).collect();

    let payload: StringArray = events.iter().map(|e| Some(e.payload_json.as_str())).collect();

    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(schema_ver),
        Arc::new(run_id),
        Arc::new(exchange),
        Arc::new(market_type),
        Arc::new(symbol),
        Arc::new(stream_name),
        Arc::new(event_type),
        Arc::new(ts_ex),
        Arc::new(ts_loc),
        Arc::new(ts_can),
        Arc::new(price),
        Arc::new(qty),
        Arc::new(side),
        Arc::new(best_bid),
        Arc::new(best_ask),
        Arc::new(mark_price),
        Arc::new(funding),
        Arc::new(liq_price),
        Arc::new(liq_qty),
        Arc::new(update_id_first),
        Arc::new(update_id_final),
        Arc::new(update_id_prev),
        Arc::new(payload),
    ])?;
    
    Ok(batch)
}

fn parse_payload_metadata(event_type: &str, stream_name: &str, payload_json: &str) -> (Option<f64>, Option<f64>, Option<f64>, Option<f64>, Option<f64>, Option<f64>, Option<i64>, Option<i64>, Option<i64>) {
    let mut mark_price = None;
    let mut funding_rate = None;
    let mut liquidation_price = None;
    let mut liquidation_qty = None;
    let mut open_interest = None;
    let mut open_interest_value = None;

    if event_type == "markPrice" || event_type == "markPriceUpdate" || stream_name.contains("markPrice") {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&payload_json) {
            if let Some(p) = value.get("p").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok()) {
                mark_price = Some(p);
            }
            if let Some(r) = value.get("r").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok()) {
                funding_rate = Some(r);
            }
        }
    }

    if event_type == "liquidation" || event_type == "forceOrder" || stream_name.contains("forceOrder") {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&payload_json) {
            if let Some(o) = value.get("o") {
                if let Some(p) = o.get("p").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok()) {
                    liquidation_price = Some(p);
                }
                if let Some(q) = o.get("q").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok()) {
                    liquidation_qty = Some(q);
                }
            }
        }
    }

    if event_type == "openInterest" || stream_name.contains("openInterest") {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&payload_json) {
            if let Some(oi) = value.get("sumOpenInterest").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok()) {
                open_interest = Some(oi);
            }
            if let Some(oiv) = value.get("sumOpenInterestValue").and_then(|v| v.as_str()).and_then(|s| s.parse::<f64>().ok()) {
                open_interest_value = Some(oiv);
            }
        }
    }

    if event_type == "depthUpdate" || stream_name.contains("depth") {
        if let Ok(value) = serde_json::from_str::<serde_json::Value>(&payload_json) {
            let u_first = value.get("U").and_then(|v| v.as_i64());
            let u_final = value.get("u").and_then(|v| v.as_i64());
            let u_prev = value.get("pu").and_then(|v| v.as_i64());
            
            return (mark_price, funding_rate, liquidation_price, liquidation_qty, open_interest, open_interest_value, u_first, u_final, u_prev);
        }
    }

    (mark_price, funding_rate, liquidation_price, liquidation_qty, open_interest, open_interest_value, None, None, None)
}

fn row_to_normalized_event(row: parquet::record::Row, run_id: &str) -> Option<NormalizedMarketEvent> {
    let mut time_local = 0;
    let mut time_exchange = 0;
    let mut symbol = String::new();
    let mut event_type = String::new();
    let mut stream_name = String::new();
    let mut price = None;
    let mut qty = None;
    let mut side = None;
    let mut best_bid = None;
    let mut best_ask = None;
    let mut payload_json = "{}".to_string();
    let mut u_first_raw = None;
    let mut u_final_raw = None;
    let mut u_prev_raw = None;

    for (name, field) in row.get_column_iter() {
        match name.as_str() {
            "local_timestamp" => if let parquet::record::Field::Long(v) = field { time_local = *v; },
            "exchange_timestamp" => if let parquet::record::Field::Long(v) = field { time_exchange = *v; },
            "symbol" => if let parquet::record::Field::Str(v) = field { symbol = v.clone(); },
            "event_type" => if let parquet::record::Field::Str(v) = field { event_type = v.clone(); },
            "stream" => if let parquet::record::Field::Str(v) = field { stream_name = v.clone(); },
            "price" => if let parquet::record::Field::Double(v) = field { price = Some(*v); },
            "quantity" => if let parquet::record::Field::Double(v) = field { qty = Some(*v); },
            "bid_price" => if let parquet::record::Field::Double(v) = field { best_bid = Some(*v); },
            "ask_price" => if let parquet::record::Field::Double(v) = field { best_ask = Some(*v); },
            "is_buyer_maker" => if let parquet::record::Field::Bool(v) = field { 
                side = Some(if *v { "sell".to_string() } else { "buy".to_string() }); 
            },
            "update_id_first" => if let parquet::record::Field::Long(v) = field { u_first_raw = Some(*v); },
            "update_id_final" => if let parquet::record::Field::Long(v) = field { u_final_raw = Some(*v); },
            "update_id_prev" => if let parquet::record::Field::Long(v) = field { u_prev_raw = Some(*v); },
            "payload" => if let parquet::record::Field::Str(v) = field { payload_json = v.clone(); },
             _ => {}
        }
    }

    if symbol.is_empty() { return None; }
    
    // Fallback if stream_name not in parquet (backward compat)
    if stream_name.is_empty() {
        stream_name = event_type.clone(); 
    }

    let (mark_price, funding_rate, liquidation_price, liquidation_qty, open_interest, open_interest_value, u_first, u_final, u_prev) = parse_payload_metadata(&event_type, &stream_name, &payload_json);

    Some(NormalizedMarketEvent {
        schema_version: 1,
        run_id: run_id.to_string(),
        exchange: "binance".to_string(),
        market_type: "future".to_string(),
        symbol: symbol.clone(),
        stream_name,
        event_type,
        time_exchange,
        time_local,
        time_canonical: time_exchange, // Simple canonical for now
        recv_time: None,
        price,
        qty,
        side,
        best_bid,
        best_ask,
        mark_price,
        funding_rate, 
        liquidation_price, 
        liquidation_qty, 
        open_interest,
        open_interest_value,
        update_id_first: u_first.or(u_first_raw),
        update_id_final: u_final.or(u_final_raw),
        update_id_prev: u_prev.or(u_prev_raw),
        payload_json, 
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalizer_preserves_mark_and_funding() {
        let payload_json = r#"{"e":"markPriceUpdate","s":"BTCUSDT","p":"60000.50","r":"0.0001"}"#;
        
        let (mark_price, funding_rate, liq_p, liq_q, oi, oiv, _uf, _ul, _up) = parse_payload_metadata("markPriceUpdate", "markPrice", payload_json);
        
        assert_eq!(mark_price, Some(60000.50));
        assert_eq!(funding_rate, Some(0.0001));
        assert_eq!(liq_p, None);
        assert_eq!(liq_q, None);
        assert_eq!(oi, None);
        assert_eq!(oiv, None);
        
        // Also test liquidation payload
        let liq_payload = r#"{"e":"forceOrder","o":{"p":"50000.0","q":"2.5"}}"#;
        let (mp2, fr2, lp2, lq2, oi2, oiv2, _uf2, _ul2, _up2) = parse_payload_metadata("forceOrder", "forceOrder", liq_payload);
        
        assert_eq!(mp2, None);
        assert_eq!(fr2, None);
        assert_eq!(lp2, Some(50000.0));
        assert_eq!(lq2, Some(2.5));
        
        // Test Open Interest
        let oi_payload = r#"{"symbol":"BTCUSDT","sumOpenInterest":"1500.5","sumOpenInterestValue":"75000000.0","timestamp":1600000000000}"#;
        let (mp3, fr3, lp3, lq3, oi3, oiv3, _uf3, _ul3, _up3) = parse_payload_metadata("openInterest", "openInterest", oi_payload);
        
        assert_eq!(mp3, None);
        assert_eq!(oi3, Some(1500.5));
        assert_eq!(oiv3, Some(75000000.0));
    }
    #[test]
    fn test_normalizer_preserves_depth_updates() {
        let payload_json = r#"{"e":"depthUpdate","E":1600000000000,"T":1600000000001,"s":"BTCUSDT","U":500,"u":600,"pu":499,"b":[],"a":[]}"#;
        let (_, _, _, _, _, _, uf, ul, up) = parse_payload_metadata("depthUpdate", "depth", payload_json);
        
        assert_eq!(uf, Some(500));
        assert_eq!(ul, Some(600));
        assert_eq!(up, Some(499));
    }
}
