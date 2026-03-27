use super::engine::FeatureEngine;
use super::schema::FeatureVector;
use super::manifest::{FeatureConfig, FeatureManifest};
use super::profiles::{FeatureProfile, FeatureSetVersion};
use crate::normalization::schema::NormalizedMarketEvent;
use std::fs::File;
use std::path::PathBuf;
use std::sync::Arc;
use parquet::file::reader::{FileReader, SerializedFileReader};
use parquet::arrow::ArrowWriter;
use parquet::file::properties::WriterProperties;
use arrow::datatypes::{DataType, Field, Schema};
use arrow::array::{
    Int64Array, Float64Array
};
use arrow::record_batch::RecordBatch;
use sha2::{Sha256, Digest};
use hex;
use chrono::Utc;
use log::{info, warn};
use anyhow::{Result, anyhow};

pub struct FeatureBuilder {
    dataset_path: PathBuf,
    output_dir: PathBuf,
    profile: FeatureProfile,
    config: FeatureConfig,
}

impl FeatureBuilder {
    pub fn new(dataset_path: PathBuf, output_dir: PathBuf, profile: FeatureProfile, config: FeatureConfig) -> Self {
        Self {
            dataset_path,
            output_dir,
            profile,
            config,
        }
    }

    pub fn run(&self) -> Result<FeatureManifest> {
        info!("Starting Feature Build: Profile={:?}", self.profile);

        // 4. Generate ID (before building, assuming ID depends on config and inputs)
        // User rq #8: features_id = SHA256(dataset_id + profile + sampling + partial + versions...)
        // We know dataset_id but we might not trust it from builder input?
        // We will read it from input metadata if possible, else "unknown".
        // Let's assume we read 1st event to get dataset_id?
        // NormalizedMarketEvent has `run_id` but maybe not `dataset_id`.
        // We'll use the passed dataset_path's parent folder name or explicit string.
        let dataset_name = self.dataset_path.parent()
            .and_then(|p| p.file_name())
            .and_then(|s| s.to_str())
            .unwrap_or("unknown_dataset").to_string();

        let features_id = self.generate_id(&dataset_name);

        // 1. Initialize Engine
        let mut engine = FeatureEngine::new(
            self.profile, 
            self.config.clone(),
            dataset_name.clone(),
            features_id.clone()
        );
        
        // 2. Scan Input
        let input_file = File::open(&self.dataset_path)?;
        let reader = SerializedFileReader::new(input_file)?;
        let mut row_iter = reader.get_row_iter(None)?;

        // 3. Transform Loop
        let mut features = Vec::new();
        let mut event_count = 0;
        
        while let Some(Ok(row)) = row_iter.next() {
            if let Some(event) = row_to_normalized_event(row) {
                engine.validate_dependency(&event.stream_name).ok();
                
                engine.update(&event);
                
                // Check if we need to emit (possibly multiple times)
                // "Even if no events occur... emit at multiples"
                // The engine holds state. `maybe_emit` checks next_emit_ts.
                
                while let Some(fv) = engine.maybe_emit(event.time_canonical) {
                     if engine.is_warmed_up() {
                         features.push(fv);
                     }
                }
                event_count += 1;
            }
        }
        
        if let Err(e) = engine.check_ready() {
             return Err(anyhow!("Missing required streams for profile {:?}: {}", self.profile, e));
        }

        info!("Computed {} feature vectors from {} events.", features.len(), event_count);
        
        if features.is_empty() {
            // It's possible to have 0 features if dataset shorter than warmup or interval
             warn!("No features generated. Dataset might be too short for warmup.");
        }

        // 4. Output Directory
        let final_output_dir = self.output_dir.join(&features_id);
        
        if !final_output_dir.exists() {
            std::fs::create_dir_all(&final_output_dir)?;
        }

        // 5. Write Output Parquet
        let output_path = final_output_dir.join("features.parquet");
        let file = File::create(&output_path)?;
        
        let schema = create_feature_schema();
        let batch = features_to_batch(&features, schema.clone())?;
        
        let props = WriterProperties::builder()
            .set_compression(parquet::basic::Compression::SNAPPY)
            .build();
        let mut writer = ArrowWriter::try_new(file, batch.schema(), Some(props))?;
        writer.write(&batch)?;
        writer.close()?;
        
        // 5b. Compute Signature (Engine knows it)
        let signature_hash = engine.compute_signature();
        
        // 6. Write Manifest
        let manifest = FeatureManifest {
            features_id: features_id.clone(),
            dataset_id: dataset_name,
            profile: self.profile,
            featureset_version: FeatureSetVersion::default(),
            signature_hash,
            config: self.config.clone(),
            engine_version: "1.0.0".to_string(),
            schema_version: 1,
            feature_count: features.len(),
            build_timestamp: Utc::now().to_rfc3339(),
            file_path: "features.parquet".to_string(),
            quality_status_reference: "TODO".to_string(),
        };
        
        let manifest_path = final_output_dir.join("feature_manifest.json");
        let manifest_file = File::create(&manifest_path)?;
        serde_json::to_writer_pretty(manifest_file, &manifest)?;
        
        Ok(manifest)
    }
    
    fn generate_id(&self, dataset_id: &str) -> String {
        let mut hasher = Sha256::new();
        hasher.update(dataset_id.as_bytes());
        hasher.update(format!("{:?}", self.profile).as_bytes());
        hasher.update(self.config.sampling_interval_ms.to_le_bytes());
        hasher.update([self.config.emit_partial as u8]);
        hasher.update(b"v1.0.0"); // engine_version
        hasher.update(b"1"); // schema_version
        hex::encode(hasher.finalize())[0..16].to_string()
    }
}

// Helper to construct Arrow Schema
fn create_feature_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("schema_version", DataType::UInt16, false),
        Field::new("dataset_id", DataType::Utf8, false),
        Field::new("features_id", DataType::Utf8, false),
        Field::new("ts_feature", DataType::Int64, false),
        Field::new("mid_price", DataType::Float64, true),
        Field::new("log_return_1", DataType::Float64, true),
        Field::new("log_return_5", DataType::Float64, true),
        Field::new("realized_vol_10", DataType::Float64, true),
        Field::new("bid_ask_spread", DataType::Float64, true),
        Field::new("relative_spread", DataType::Float64, true),
        Field::new("imbalance", DataType::Float64, true),
        Field::new("mark_price_distance", DataType::Float64, true),
        Field::new("funding_rate", DataType::Float64, true),
    ]))
}

// Convert FeatureVector slice to RecordBatch
fn features_to_batch(features: &[FeatureVector], schema: Arc<Schema>) -> Result<RecordBatch> {
    // We iterate fields to build arrays
    let version: arrow::array::UInt16Array = features.iter().map(|f| Some(f.schema_version)).collect();
    let dataset_id: arrow::array::StringArray = features.iter().map(|f| Some(f.dataset_id.clone())).collect();
    let features_id: arrow::array::StringArray = features.iter().map(|f| Some(f.features_id.clone())).collect();
    let ts: Int64Array = features.iter().map(|f| Some(f.ts_feature)).collect();
    let mid: Float64Array = features.iter().map(|f| f.mid_price).collect();
    let ret1: Float64Array = features.iter().map(|f| f.log_return_1).collect();
    let ret5: Float64Array = features.iter().map(|f| f.log_return_5).collect();
    let vol: Float64Array = features.iter().map(|f| f.realized_vol_10).collect();
    let spread: Float64Array = features.iter().map(|f| f.bid_ask_spread).collect();
    let rel_spread: Float64Array = features.iter().map(|f| f.relative_spread).collect();
    let imbalance: Float64Array = features.iter().map(|f| f.imbalance).collect();
    let mark_dist: Float64Array = features.iter().map(|f| f.mark_price_distance).collect();
    let funding: Float64Array = features.iter().map(|f| f.funding_rate).collect();

    let batch = RecordBatch::try_new(schema, vec![
        Arc::new(version),
        Arc::new(dataset_id),
        Arc::new(features_id),
        Arc::new(ts),
        Arc::new(mid),
        Arc::new(ret1),
        Arc::new(ret5),
        Arc::new(vol),
        Arc::new(spread),
        Arc::new(rel_spread),
        Arc::new(imbalance),
        Arc::new(mark_dist),
        Arc::new(funding),
    ])?;
    Ok(batch)
}

// Duplicate logic from Reader/Normalizer to get Event from Row
// Ideally this should be a shared utility.
fn row_to_normalized_event(row: parquet::record::Row) -> Option<NormalizedMarketEvent> {
    // Simplified mapping for Feature generation
    let mut time_exchange = 0;
    let mut time_canonical = 0;
    let mut price = None;
    let mut best_bid = None;
    let mut best_ask = None;
    let mut stream_name = String::new();
    
    for (name, field) in row.get_column_iter() {
        match name.as_str() {
            "exchange_timestamp" => if let parquet::record::Field::Long(v) = field { time_exchange = *v; },
            "time_canonical" => if let parquet::record::Field::Long(v) = field { time_canonical = *v; },
            "price" => if let parquet::record::Field::Double(v) = field { price = Some(*v); },
            "best_bid" => if let parquet::record::Field::Double(v) = field { best_bid = Some(*v); },
            "best_ask" => if let parquet::record::Field::Double(v) = field { best_ask = Some(*v); },
            "stream_name" => if let parquet::record::Field::Str(v) = field { stream_name = v.clone(); },
            
            // Backwards compat if needed, but NormalizedMarketEvent should have consistent schema
             _ => {}
        }
    }

    if time_canonical == 0 { time_canonical = time_exchange; }

    Some(NormalizedMarketEvent {
        time_canonical,
        time_exchange,
        price,
        best_bid,
        best_ask,
        stream_name,
        // Fill others with defaults/dummy as Engine primarily uses Price/Time/Bid/Ask
        schema_version: 1,
        run_id: "".to_string(),
        exchange: "".to_string(),
        market_type: "".to_string(),
        symbol: "".to_string(),
        event_type: "".to_string(),
        time_local: 0,
        recv_time: None,
        qty: None,
        side: None,
        mark_price: None,
        funding_rate: None,
        liquidation_price: None,
        liquidation_qty: None,
        open_interest: None,
        open_interest_value: None,
        update_id_first: None,
        update_id_final: None,
        update_id_prev: None,
        payload_json: "{}".to_string(),
    })
}
