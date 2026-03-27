use tonic::{Request, Response, Status};
use bot_core::proto::dataset_service_server::DatasetService;
use bot_core::proto::{
    BuildDatasetRequest, BuildDatasetResponse, ListDatasetsRequest, ListDatasetsResponse,
    GetDatasetStatusRequest, DatasetStatus, GetQualityReportRequest, QualityReport as ProtoQualityReport, StreamQuality as ProtoStreamQuality,
    DeleteDatasetRequest, DeleteResponse
};
use bot_data::normalization::engine::Normalizer;
// use bot_data::normalization::manifest::DatasetManifest;
use std::sync::{Arc, Mutex};
use std::path::PathBuf;
use std::collections::HashMap;
use log::info;

type JobState = (String, f32, String, Option<String>);

pub struct DatasetServiceImpl {
    data_dir: PathBuf,
    // Simple in-memory job tracker for MVP
    // Map<job_id, JobState>
    jobs: Arc<Mutex<HashMap<String, JobState>>>,
}

impl DatasetServiceImpl {
    pub fn new(data_dir: PathBuf) -> Self {
        Self {
            data_dir,
            jobs: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

#[tonic::async_trait]
impl DatasetService for DatasetServiceImpl {
    async fn build_dataset(
        &self,
        request: Request<BuildDatasetRequest>,
    ) -> Result<Response<BuildDatasetResponse>, Status> {
        let req = request.into_inner();
        let run_id = req.run_id;
        
        // TODO: Validate run_id exists
        
        let job_id = format!("{}_{}", run_id, chrono::Utc::now().timestamp()); // Serves as job ID
        let jobs = self.jobs.clone();
        let data_dir = self.data_dir.clone();
        let job_id_clone = job_id.clone();
        let run_id_clone = run_id.clone();

        // Spawn blocking task for normalization
        tokio::task::spawn_blocking(move || {
            {
                let mut jobs = jobs.lock().unwrap();
                jobs.insert(job_id_clone.clone(), ("BUILDING".to_string(), 0.0, "Starting normalization...".to_string(), None));
            }

            let normalizer = Normalizer::new(run_id_clone, data_dir);
            match normalizer.build_dataset() {
                Ok((actual_id, _report)) => {
                    let mut jobs = jobs.lock().unwrap();
                    jobs.insert(job_id_clone, ("COMPLETED".to_string(), 1.0, "Dataset built successfully".to_string(), Some(actual_id)));
                },
                Err(e) => {
                    let mut jobs = jobs.lock().unwrap();
                    jobs.insert(job_id_clone, ("FAILED".to_string(), 0.0, format!("Error: {}", e), None));
                }
            }
        });

        Ok(Response::new(BuildDatasetResponse {
            dataset_id: job_id,
            status: "QUEUED".to_string(),
        }))
    }

    async fn list_datasets(
        &self,
        _request: Request<ListDatasetsRequest>,
    ) -> Result<Response<ListDatasetsResponse>, Status> {
        info!("Listing datasets request received");
        let mut datasets = Vec::new();
        
        let possible_roots = vec![
            self.data_dir.join("runs").join("runs"),
            self.data_dir.join("runs"),
        ];

        for root in possible_roots {
            info!("Scanning root: {:?}", root);
            if let Ok(entries) = std::fs::read_dir(&root) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_dir() {
                        // Check for datasets dir
                        let datasets_dir = path.join("datasets");
                        if let Ok(ds_entries) = std::fs::read_dir(&datasets_dir) {
                            for ds_entry in ds_entries.flatten() {
                                let ds_path = ds_entry.path();
                                if ds_path.is_dir() {
                                    let id = ds_path.file_name().unwrap_or_default().to_string_lossy().to_string();
                                    let manifest_path = ds_path.join("dataset_manifest.json");

                                    // Try to read manifest
                                    let (created_at, run_id, _size) = if manifest_path.exists() {
                                        if let Ok(file) = std::fs::File::open(&manifest_path) {
                                            use serde_json::Value;
                                            if let Ok(v) = serde_json::from_reader::<_, Value>(file) {
                                                 let c = v["created_at"].as_str().unwrap_or("").to_string();
                                                 let r = v["source_run_id"].as_str().unwrap_or("").to_string();
                                                 (c, r, 0) // size todo
                                            } else {
                                                ("".to_string(), "".to_string(), 0)
                                            }
                                        } else {
                                             ("".to_string(), "".to_string(), 0)
                                        }
                                    } else {
                                         // Fallback: use directory mtime
                                         let metadata = std::fs::metadata(&ds_path).ok();
                                         let created = metadata.and_then(|m| m.created().ok())
                                            .map(|t| chrono::DateTime::<chrono::Utc>::from(t).to_rfc3339())
                                            .unwrap_or_default();
                                         (created, "".to_string(), 0)
                                    };

                                    // Get File Size
                                    let parquet_path = ds_path.join("normalized_events.parquet");
                                    let file_size = std::fs::metadata(&parquet_path).map(|m| m.len()).unwrap_or(0);
                                    
                                    // Hack: Append size to created_at or run_id since Proto doesn't have size field yet?
                                    // Actually, let's put it in run_id if it's empty, or append to it?
                                    // Better: The user asked for "rich metadata".
                                    // Let's format `created_at` as a JSON string or combined string if strict proto?
                                    // Proto has `created_at` string. We can put "2024-02-16T... | 1.2GB" there?
                                    // Or just return the raw string and let GUI format.
                                    // Let's return the ISO string in `created_at` and put size in `run_id` as "RunID | Size"?
                                    // No, that's messy.
                                    // Let's just put size in `status`? No.
                                    // Let's abuse `run_id` to hold "RunID|SizeBytes". 
                                    // The GUI features.py splits strings easily.
                                    
                                    let run_id_field = if !run_id.is_empty() {
                                        format!("{}|{}", run_id, file_size) 
                                    } else {
                                        format!("unknown|{}", file_size)
                                    };

                                    datasets.push(bot_core::proto::DatasetSummary {
                                        dataset_id: id,
                                        run_id: run_id_field, 
                                        status: "READY".to_string(),
                                        created_at,
                                        quality_summary: None,
                                        feature_profile: "simple".to_string(), // Default or read from meta if available
                                    });
                                }
                            }
                        }
                    }
                }
            }
        }
        
        // Dedup by ID
        datasets.sort_by(|a, b| a.dataset_id.cmp(&b.dataset_id));
        datasets.dedup_by(|a, b| a.dataset_id == b.dataset_id);
        
        info!("Returning {} datasets", datasets.len());

        Ok(Response::new(ListDatasetsResponse { datasets }))
    }

    async fn get_dataset_status(
        &self,
        request: Request<GetDatasetStatusRequest>,
    ) -> Result<Response<DatasetStatus>, Status> {
        let req = request.into_inner();
        let jobs = self.jobs.lock().unwrap();
        
        if let Some((state, progress, message, actual_id)) = jobs.get(&req.dataset_id) {
            // If we have an actual_id (completed), return that as the dataset_id
            // This allows the client to switch from JobID to DatasetID transparently.
            let effective_id = actual_id.clone().unwrap_or(req.dataset_id.clone());
            
            // Include actual ID in message for now, or just return existing info
            let msg = if let Some(id) = actual_id {
                format!("{} (ID: {})", message, id)
            } else {
                message.clone()
            };

            Ok(Response::new(DatasetStatus {
                dataset_id: effective_id,
                state: state.clone(),
                progress: *progress,
                message: msg,
            }))
        } else {
             Ok(Response::new(DatasetStatus {
                dataset_id: req.dataset_id,
                state: "UNKNOWN".to_string(),
                progress: 0.0,
                message: "Dataset job not found".to_string(),
            }))
        }
    }

    async fn get_quality_report(
        &self,
        request: Request<GetQualityReportRequest>,
    ) -> Result<Response<ProtoQualityReport>, Status> {
        let req = request.into_inner();
        let mut target_dataset_id = req.dataset_id.clone();
        
        // Resolve job_id to actual_dataset_id if possible
        {
            let jobs = self.jobs.lock().unwrap();
            if let Some((_, _, _, Some(actual_id))) = jobs.get(&req.dataset_id) {
                target_dataset_id = actual_id.clone();
            }
        }
        
        // Check multiple possible locations for runs
        let possible_roots = vec![
            self.data_dir.join("runs").join("runs"),
            self.data_dir.join("runs"),
        ];

        let mut report_path = None;
        
        for root in possible_roots {
            if let Ok(entries) = std::fs::read_dir(&root) {
                for entry in entries.flatten() {
                     let path = entry.path();
                     if path.is_dir() {
                         let candidate = path.join("datasets").join(&target_dataset_id).join("quality_report.json");
                         if candidate.exists() {
                             report_path = Some(candidate);
                             break;
                         }
                         // Also check if target_dataset_id IS the run_id (legacy mode or direct map)
                         let candidate_alt = path.join("datasets").join("quality_report.json"); // Unlikely but possible in old structure
                         if candidate_alt.exists() {
                             // verify ID inside? Skip for now.
                         }
                     }
                }
            }
            if report_path.is_some() { break; }
        }
        
        if let Some(path) = report_path {
             if let Ok(file) = std::fs::File::open(path) {
                 use bot_data::normalization::manifest::QualityReport as LocalReport;
                 if let Ok(report) = serde_json::from_reader::<_, LocalReport>(file) {
                     // Map to Proto
                     let mut streams = HashMap::new();
                     for (k, v) in report.streams {
                         streams.insert(k, ProtoStreamQuality {
                             coverage_pct: v.coverage_pct,
                             lag_p99_ms: v.lag_p99_ms,
                             events_per_sec: v.events_per_sec,
                             gap_count: v.gap_count,
                             drift_ms_avg: v.drift_ms_avg,
                             status: v.status,
                         });
                     }
                     
                     return Ok(Response::new(ProtoQualityReport {
                         overall_status: report.overall_status,
                         coverage_pct: report.coverage_pct,
                         total_gaps: report.total_gaps,
                         missing_streams: report.missing_streams,
                         usable_for_training: report.usable_for_training,
                         usable_for_backtest: report.usable_for_backtest,
                         streams,
                     }));
                 }
             }
        }

         // Return empty/unknown if not found
         Ok(Response::new(ProtoQualityReport {
             overall_status: "NOT_FOUND".to_string(),
             coverage_pct: 0.0,
             total_gaps: 0,
             missing_streams: vec![],
             usable_for_training: false,
             usable_for_backtest: false,
             streams: HashMap::new(),
         }))
    }

    async fn delete_dataset(
        &self,
        request: Request<DeleteDatasetRequest>,
    ) -> Result<Response<DeleteResponse>, Status> {
        let req = request.into_inner();
        let dataset_id = req.dataset_id;
        info!("Received DeleteDataset request for ID: {}", dataset_id);
        
        let mut deleted_path = None;
        let mut deleted_status = false;

        // 1. Find Dataset Directory
        let possible_roots = vec![
            self.data_dir.join("runs").join("runs"),
            self.data_dir.join("runs"),
        ];

        for root in possible_roots {
            if let Ok(entries) = std::fs::read_dir(&root) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.is_dir() {
                        let candidate = path.join("datasets").join(&dataset_id);
                        if candidate.exists() && candidate.is_dir() {
                            deleted_path = Some(candidate);
                            break;
                        }
                    }
                }
            }
            if deleted_path.is_some() { break; }
        }

        if let Some(path) = deleted_path {
             if let Err(e) = std::fs::remove_dir_all(&path) {
                 return Ok(Response::new(DeleteResponse {
                     success: false,
                     message: format!("Failed to delete directory: {}", e),
                 }));
             }
             info!("Deleted dataset directory: {:?}", path);
             deleted_status = true;
        }

        // 2. Remove from Index (Always try, even if dir not found, to clean up stales)
        let index_path = self.data_dir.join("index/datasets_index.json");
        if index_path.exists() {
             let mut index = bot_data::dataset_index::DatasetIndex::load(&index_path);
             let original_len = index.entries.len();
             index.entries.retain(|e| e.dataset_id != dataset_id);
             
             if index.entries.len() < original_len {
                 if let Err(e) = index.save(&index_path) {
                     log::warn!("Failed to save updated index: {}", e);
                 } else {
                     info!("Removed dataset from index.");
                     deleted_status = true;
                 }
             }
         }

        if deleted_status {
             Ok(Response::new(DeleteResponse {
                 success: true,
                 message: "Dataset deleted successfully".to_string(),
             }))
        } else {
            Ok(Response::new(DeleteResponse {
                success: false,
                message: "Dataset not found".to_string(),
            }))
        }
    }
}
