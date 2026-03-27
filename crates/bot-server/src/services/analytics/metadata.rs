use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RunMetadata {
    pub run_id: String,
    pub git_commit: String, 
    pub config_hash: String, 
    pub schema_version: u32,
    pub started_at: i64,
    pub environment: String, 
    pub simulator_mode: String,
}

impl RunMetadata {
    pub fn generate(
        run_id: String, 
        environment: String, 
        config_hash: String, 
        simulator_mode: String,
        started_at: i64,
    ) -> Self {
        // Attempt to read git commit if available via env var, else "UNKNOWN"
        let git_commit = std::env::var("BOTMK3_GIT_COMMIT").unwrap_or_else(|_| "UNKNOWN".to_string());
        
        Self {
            run_id,
            git_commit,
            config_hash,
            schema_version: 6, // Hardcoded schema version
            started_at,
            environment,
            simulator_mode,
        }
    }
}
