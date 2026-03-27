use crate::experience::schema::ExperienceRow;
use std::path::PathBuf;
use std::fs::File;
use parquet::arrow::ArrowWriter;
use parquet::file::properties::WriterProperties;
use log::{info, error};

pub struct ExperienceWriter {
    base_dir: PathBuf,
    current_writer: Option<ArrowWriter<File>>,
    current_file_path: Option<PathBuf>,
    rows_buffer: Vec<ExperienceRow>,
    buffer_size_limit: usize,
    
    // Rotation config
    max_kbytes_per_file: usize,
    bytes_written_approx: usize,
}

impl ExperienceWriter {
    pub fn new(base_dir: PathBuf) -> Self {
        // Ensure dir exists
        let _ = std::fs::create_dir_all(&base_dir);
        
        Self {
            base_dir,
            current_writer: None,
            current_file_path: None,
            rows_buffer: Vec::with_capacity(1000),
            buffer_size_limit: 100, // Flush every 100 rows
            max_kbytes_per_file: 50 * 1024, // 50MB rotation
            bytes_written_approx: 0,
        }
    }

    pub fn write(&mut self, row: ExperienceRow) {
        self.rows_buffer.push(row);
        
        if self.rows_buffer.len() >= self.buffer_size_limit {
            self.flush_buffer();
        }
    }

    pub fn flush_buffer(&mut self) {
        if self.rows_buffer.is_empty() {
            return;
        }
        
        if self.current_writer.is_none() || self.should_rotate() {
            self.rotate_file();
        }

        if let Some(writer) = &mut self.current_writer {
            let batch = match ExperienceRow::to_record_batch(&self.rows_buffer) {
                Ok(b) => b,
                Err(e) => {
                    error!("Failed to convert buffer to batch: {}", e);
                    self.rows_buffer.clear();
                    return;
                }
            };
            
            // Approximate size tracking (very rough)
            let batch_size_bytes = 200 * self.rows_buffer.len(); // Estimate 200 bytes per row
            self.bytes_written_approx += batch_size_bytes;

            if let Err(e) = writer.write(&batch) {
                error!("Failed to write batch to parquet: {}", e);
            }
        }
        
        self.rows_buffer.clear();
    }
    
    fn should_rotate(&self) -> bool {
        // Rotation check
        self.bytes_written_approx / 1024 > self.max_kbytes_per_file
    }

    fn rotate_file(&mut self) {
        // Close current
        if let Some(writer) = self.current_writer.take() {
            if let Err(e) = writer.close() {
                error!("Failed to close parquet writer: {}", e);
            }
            info!("Closed experience file: {:?}", self.current_file_path);
        }
        
        // Open new
        let filename = format!("experience_{}.parquet", chrono::Utc::now().format("%Y%m%d_%H%M%S"));
        let path = self.base_dir.join(filename);
        
        match File::create(&path) {
            Ok(file) => {
                // Determine schema from empty batch or schema def? 
                // We need a dummy row or schema definition.
                // Hack: Create dummy batch if buffer is not empty to get schema? 
                // Use schema from first buffer item.
                // If buffer empty, we can't determine schema easily without static def.
                // ExperienceRow has static schema method we can use? 
                // We did define it in to_record_batch.
                
                // Let's create an empty batch to get schema
                let empty_rows = vec![self.default_row()]; 
                let batch = ExperienceRow::to_record_batch(&empty_rows).unwrap();
                let schema = batch.schema();
                
                let props = WriterProperties::builder()
                    .set_compression(parquet::basic::Compression::SNAPPY)
                    .build();
                    
                match ArrowWriter::try_new(file, schema, Some(props)) {
                    Ok(writer) => {
                        self.current_writer = Some(writer);
                        self.current_file_path = Some(path.clone());
                        self.bytes_written_approx = 0;
                        info!("Opened new experience file: {:?}", path);
                    },
                    Err(e) => error!("Failed to create ArrowWriter: {}", e),
                }
            },
            Err(e) => error!("Failed to create file: {}", e),
        }
    }
    
    // Helper for schema generation
    fn default_row(&self) -> ExperienceRow {
         ExperienceRow {
            episode_id: "".to_string(),
            symbol: "".to_string(),
            decision_ts: 0,
            step_index: 0,
            obs: vec![0.0; 12],
            action: 0,
            reward: 0.0,
            equity_before: 0.0,
            equity_after: 0.0,
            pos_qty_before: 0.0,
            pos_side_before: "".to_string(),
            fees_step: 0.0,
            done: false,
            done_reason: "".to_string(),
            info_json: "".to_string(),
            log_prob: 0.0,
            value_estimate: 0.0,
         }
    }

    pub fn close(&mut self) {
        self.flush_buffer();
        if let Some(writer) = self.current_writer.take() {
             let _ = writer.close();
        }
    }
}

impl Drop for ExperienceWriter {
    fn drop(&mut self) {
        self.close();
    }
}
