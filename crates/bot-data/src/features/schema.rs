use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeatureVector {
    pub schema_version: u16,
    pub dataset_id: String,
    pub features_id: String,
    pub ts_feature: i64,
    pub mid_price: Option<f64>,
    pub log_return_1: Option<f64>,
    pub log_return_5: Option<f64>,
    pub realized_vol_10: Option<f64>,
    pub bid_ask_spread: Option<f64>,
    pub relative_spread: Option<f64>,
    pub imbalance: Option<f64>,
    pub mark_price_distance: Option<f64>,
    pub funding_rate: Option<f64>,
}

impl FeatureVector {
    pub fn new(ts: i64, dataset_id: String, features_id: String) -> Self {
        Self {
            schema_version: 1,
            dataset_id,
            features_id,
            ts_feature: ts,
            mid_price: None,
            log_return_1: None,
            log_return_5: None,
            realized_vol_10: None,
            bid_ask_spread: None,
            relative_spread: None,
            imbalance: None,
            mark_price_distance: None,
            funding_rate: None,
        }
    }
}
