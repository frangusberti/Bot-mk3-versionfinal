use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeatureConfig {
    pub sampling_interval_ms: u64,
    pub emit_partial: bool,
    pub allow_mock: bool, 
}

impl Default for FeatureConfig {
    fn default() -> Self {
        Self {
            sampling_interval_ms: 1000,
            emit_partial: false,
            allow_mock: false,
        }
    }
}
// Removed specific window params as they are effectively fixed by requirements 
// (log_return_1, log_return_5, vol_10 implied by sampling_interval?)
// User said: "log_return_1 = ln(mid_price_t / mid_price_{t-1})" where t is interval index.
// So windows are defined in "intervals", not ms, in the engine logic?
// User Requirements: "Fixed Interval Sampling... log_return_1... log_return_5... realized_vol_10 over last 10 intervals"
// So distinct window_ms params are likely redundant if strictly following "1 interval, 5 intervals".
// Or we keep them if user wants to tune "1 interval = ? ms". No, "interval" is sampling_interval.

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeatureManifest {
    pub features_id: String,
    pub dataset_id: String,
    pub profile: crate::features::profiles::FeatureProfile,
    pub featureset_version: crate::features::profiles::FeatureSetVersion,
    pub signature_hash: String,
    pub config: FeatureConfig,
    pub engine_version: String,
    pub schema_version: u16,
    pub feature_count: usize,
    pub build_timestamp: String,
    pub file_path: String,
    pub quality_status_reference: String, // from source dataset
}
