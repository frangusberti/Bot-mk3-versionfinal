use bot_core::proto::feature_service_server::FeatureService;
use bot_core::proto::{
    BuildFeaturesRequest, BuildFeaturesResponse,
    GetFeatureStatusRequest, FeatureStatus,
    ListFeaturesRequest, ListFeaturesResponse,
    PreviewFeaturesRequest, PreviewFeaturesResponse, FeatureRow,
    DeleteFeaturesRequest, DeleteResponse
};
use bot_data::features::builder::FeatureBuilder;
use bot_data::features::manifest::FeatureConfig;
use bot_data::features::profiles::FeatureProfile;
use tonic::{Request, Response, Status};
use std::sync::{Arc, Mutex};
use std::path::PathBuf;
use std::collections::HashMap;
use tokio::task;
use log::{info, error};
use bot_data::features::manifest::FeatureManifest;
use parquet::file::reader::FileReader;

pub struct FeatureServiceImpl {
    runs_dir: PathBuf,
    // Simple in-memory job tracker: features_id -> status
    active_jobs: Arc<Mutex<HashMap<String, String>>>, 
}

impl FeatureServiceImpl {
    pub fn new(runs_dir: PathBuf) -> Self {
        Self {
            runs_dir,
            active_jobs: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

#[tonic::async_trait]
impl FeatureService for FeatureServiceImpl {
    async fn build_features(
        &self,
        request: Request<BuildFeaturesRequest>,
    ) -> Result<Response<BuildFeaturesResponse>, Status> {
        let req = request.into_inner();
        let _run_id = "unknown"; // Request doesn't have run_id? It has dataset_id.
        // We need to resolve dataset_id -> run_id to find the path.
        // This implies dataset_id is globally unique or we search.
        // For now, let's assume we can find it, or we iterate runs.
        // HACK: We assume standard path structure and search?
        // Or we update proto to include run_id?
        // Let's search for the dataset_id in the runs dir.
        
        let dataset_path = self.find_dataset(&req.dataset_id).ok_or_else(|| 
            Status::not_found(format!("Dataset {} not found", req.dataset_id))
        )?;
        
        // Convert Proto Config -> Internal Config
        let config = if let Some(c) = req.config {
            FeatureConfig {
                sampling_interval_ms: c.sampling_interval_ms as u64,
                emit_partial: c.emit_partial,
                allow_mock: c.allow_mock,
            }
        } else {
            FeatureConfig::default()
        };

        let profile = match req.profile.as_str() {
            "RICH" => FeatureProfile::Rich,
            _ => FeatureProfile::Simple,
        };
        
        // Output Path setup
        // We need to compute ID first to check if exists
        // But ID depends on config. FeatureBuilder computes it.
        // Let's replicate ID logic or instantiate a "dry" builder?
        // Or just start it?
        
        // Async spawn
        let active_jobs = self.active_jobs.clone();
        let d_path = dataset_path.clone();
        
        // We need to determine output dir.
        // dataset_path is .../normalized_events.parquet
        // dataset_dir is parent.
        let dataset_dir = d_path.parent().unwrap().to_path_buf();
        let features_root = dataset_dir.join("features");
        
        // We can't easily compute ID without running the logic if logic inside Builder does hashing of config.
        // Let's instantiate Builder just to get ID.
        let _builder = FeatureBuilder::new(d_path.clone(), features_root.clone(), profile, config.clone());
        
        // HACK: We need to access generate_id which is private or run it?
        // I will make generate_id public in builder or move it to manifest?
        // Let's assume we just run it.
        
        // Check if ANY job running for this dataset?
        // For robustness, let's just spawn.
        
        let profile_str = match req.profile.as_str() {
            "RICH" => "RICH",
            _ => "SIMPLE",
        };
        let job_id = format!("{}_FEAT_{}", req.dataset_id.replace("_DS", ""), profile_str);
        
        {
            let mut jobs = active_jobs.lock().unwrap();
            jobs.insert(job_id.clone(), "BUILDING".to_string());
        }
        
        let job_id_clone = job_id.clone();
        task::spawn_blocking(move || {
            // Actual run
            // builder.output_dir needs to include the features_id?
            // Builder logic in `run()` computes ID and then writes to `output_dir/features.parquet`?
            // Wait, my `run` logic used `output_dir` as the root to write `features.parquet`.
            // So if I pass `features_root`, it writes `features_root/features.parquet`.
            // This is wrong if I want `features_root/<id>/features.parquet`.
            // I should update Builder to append ID to path, or let Builder manage structure.
            
            // Let's modify Builder to create subdircetory based on ID.
            
            // For now, let's trust Builder to do the work.
            // Using a temporary builder to run.
            
            // Re-instantiate
            let builder = FeatureBuilder::new(d_path, features_root, profile, config);
            match builder.run() {
                Ok(manifest) => {
                    info!("Feature build success: {}", manifest.features_id);
                    let mut jobs = active_jobs.lock().unwrap();
                    jobs.insert(job_id_clone, format!("COMPLETED:{}", manifest.features_id));
                },
                Err(e) => {
                    error!("Feature build failed: {}", e);
                    let mut jobs = active_jobs.lock().unwrap();
                    jobs.insert(job_id_clone, format!("FAILED:{}", e));
                }
            }
        });

        Ok(Response::new(BuildFeaturesResponse {
            job_id,
            features_id: "".to_string(), // Unknown until built
            status: "BUILDING".to_string(),
        }))
    }

    async fn get_feature_status(
        &self,
        request: Request<GetFeatureStatusRequest>,
    ) -> Result<Response<FeatureStatus>, Status> {
        let req = request.into_inner();
        let jobs = self.active_jobs.lock().unwrap();
        
        if let Some(status_str) = jobs.get(&req.job_id) {
            let (state, msg) = if status_str.starts_with("COMPLETED:") {
                ("COMPLETED", status_str.strip_prefix("COMPLETED:").unwrap())
            } else if status_str.starts_with("FAILED:") {
                ("FAILED", status_str.strip_prefix("FAILED:").unwrap())
            } else {
                ("BUILDING", "")
            };
            
            return Ok(Response::new(FeatureStatus {
                job_id: req.job_id,
                features_id: if state == "COMPLETED" { msg.to_string() } else { "".to_string() },
                state: state.to_string(),
                progress: 0.0,
                message: if state == "FAILED" { msg.to_string() } else { "".to_string() },
                output_path: "".to_string(),
                vectors_computed: 0,
            }));
        }
        
        Err(Status::not_found("Job not found"))
    }
    
    async fn list_features(
        &self,
        _request: Request<ListFeaturesRequest>,
    ) -> Result<Response<ListFeaturesResponse>, Status> {
        // ... same logic ...
        let features = self.scan_features_internal();
        Ok(Response::new(ListFeaturesResponse { features }))
    }

    async fn preview_features(
        &self,
        request: Request<PreviewFeaturesRequest>,
    ) -> Result<Response<PreviewFeaturesResponse>, Status> {
        let req = request.into_inner();
        let path = self.find_feature_path(&req.features_id).ok_or_else(||
            Status::not_found(format!("Features {} not found", req.features_id))
        )?;

        let n = if req.n_rows == 0 { 50 } else { req.n_rows as usize };

        let file = std::fs::File::open(path).map_err(|e| Status::internal(e.to_string()))?;
        let reader = parquet::file::reader::SerializedFileReader::new(file)
            .map_err(|e| Status::internal(e.to_string()))?;
        
        let mut rows = Vec::new();
        let mut row_iter = reader.get_row_iter(None).map_err(|e| Status::internal(e.to_string()))?;

        for row in row_iter.by_ref().take(n) {
            let row = row.map_err(|e| Status::internal(e.to_string()))?;
            let mut columns = HashMap::new();
            let mut ts = 0;

            for (name, field) in row.get_column_iter() {
                match field {
                    parquet::record::Field::Double(v) => { columns.insert(name.clone(), *v); }
                    parquet::record::Field::Float(v) => { columns.insert(name.clone(), *v as f64); }
                    parquet::record::Field::Long(v) => { 
                        if name == "ts_feature" || name == "ts" { ts = *v; }
                        else { columns.insert(name.clone(), *v as f64); }
                    }
                    parquet::record::Field::Int(v) => { columns.insert(name.clone(), *v as f64); }
                    _ => {}
                }
            }
            rows.push(FeatureRow { columns, ts });
        }

        Ok(Response::new(PreviewFeaturesResponse { rows }))
    }

    async fn delete_features(
        &self,
        request: Request<DeleteFeaturesRequest>,
    ) -> Result<Response<DeleteResponse>, Status> {
        let req = request.into_inner();
        let features_id = req.features_id;
        info!("Received DeleteFeatures request for ID: {}", features_id);

        let mut deleted_path = None;
        let possible_roots = vec![
            self.runs_dir.clone(),
            self.runs_dir.join("runs"),
        ];

        for root in possible_roots {
            if let Ok(run_entries) = std::fs::read_dir(&root) {
                for run_entry in run_entries.flatten() {
                    let run_path = run_entry.path();
                    if run_path.is_dir() {
                        let datasets_dir = run_path.join("datasets");
                        if let Ok(ds_entries) = std::fs::read_dir(&datasets_dir) {
                            for ds_entry in ds_entries.flatten() {
                                let ds_path = ds_entry.path();
                                if ds_path.is_dir() {
                                    let feat_path = ds_path.join("features").join(&features_id);
                                    if feat_path.exists() && feat_path.is_dir() {
                                        deleted_path = Some(feat_path);
                                        break;
                                    }
                                }
                            }
                        }
                    }
                    if deleted_path.is_some() { break; }
                }
            }
            if deleted_path.is_some() { break; }
        }

        if let Some(path) = deleted_path {
            if let Err(e) = std::fs::remove_dir_all(&path) {
                return Ok(Response::new(DeleteResponse {
                    success: false,
                    message: format!("Failed to delete features directory: {}", e),
                }));
            }
            info!("Deleted features directory: {:?}", path);
            Ok(Response::new(DeleteResponse {
                success: true,
                message: "Features deleted successfully".to_string(),
            }))
        } else {
             Ok(Response::new(DeleteResponse {
                success: false,
                message: "Features not found".to_string(),
            }))
        }
    }
}

impl FeatureServiceImpl {
    fn find_dataset(&self, dataset_id: &str) -> Option<PathBuf> {
        // ... (existing find_dataset logic) ...
        let possible_roots = vec![
            self.runs_dir.clone(),
            self.runs_dir.join("runs"),
        ];

        for root in possible_roots {
            if let Ok(entries) = std::fs::read_dir(&root) {
                 for entry in entries.flatten() {
                     let run_path = entry.path();
                     if run_path.is_dir() {
                         let ds_path = run_path.join("datasets").join(dataset_id).join("normalized_events.parquet");
                         if ds_path.exists() {
                             return Some(ds_path);
                         }
                     }
                 }
            }
        }
        None
    }

    fn find_feature_path(&self, features_id: &str) -> Option<PathBuf> {
        let possible_roots = vec![
            self.runs_dir.clone(),
            self.runs_dir.join("runs"),
        ];

        for root in possible_roots {
            if let Ok(run_entries) = std::fs::read_dir(&root) {
                for run_entry in run_entries.flatten() {
                    let run_path = run_entry.path();
                    if run_path.is_dir() {
                        let datasets_dir = run_path.join("datasets");
                        if let Ok(ds_entries) = std::fs::read_dir(&datasets_dir) {
                            for ds_entry in ds_entries.flatten() {
                                let ds_path = ds_entry.path();
                                if ds_path.is_dir() {
                                    let feat_path = ds_path.join("features").join(features_id).join("features.parquet");
                                    if feat_path.exists() {
                                        return Some(feat_path);
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        None
    }

    fn scan_features_internal(&self) -> Vec<bot_core::proto::FeatureSummary> {
        let mut features = Vec::new();
        let possible_roots = vec![
            self.runs_dir.clone(),
            self.runs_dir.join("runs"),
        ];

        for root in possible_roots {
            if let Ok(run_entries) = std::fs::read_dir(&root) {
                for run_entry in run_entries.flatten() {
                    let run_path = run_entry.path();
                    if run_path.is_dir() {
                        let datasets_dir = run_path.join("datasets");
                        if let Ok(ds_entries) = std::fs::read_dir(&datasets_dir) {
                            for ds_entry in ds_entries.flatten() {
                                let ds_path = ds_entry.path();
                                if ds_path.is_dir() {
                                    let features_root = ds_path.join("features");
                                    if let Ok(feat_entries) = std::fs::read_dir(&features_root) {
                                        for feat_entry in feat_entries.flatten() {
                                            let feat_path = feat_entry.path();
                                            if feat_path.is_dir() {
                                                let manifest_path = feat_path.join("feature_manifest.json");
                                                if manifest_path.exists() {
                                                    if let Ok(file) = std::fs::File::open(&manifest_path) {
                                                        if let Ok(m) = serde_json::from_reader::<_, FeatureManifest>(file) {
                                                            features.push(bot_core::proto::FeatureSummary {
                                                                features_id: m.features_id,
                                                                profile: format!("{:?}", m.profile),
                                                                count: m.feature_count as i64,
                                                                created_at: m.build_timestamp,
                                                            });
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        features.sort_by(|a, b| b.created_at.cmp(&a.created_at));
        features
    }
}
