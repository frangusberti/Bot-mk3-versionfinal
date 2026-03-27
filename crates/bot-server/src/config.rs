use serde::Deserialize;

#[derive(Debug, Deserialize, Clone, Default)]
pub struct ServerConfig {
    #[serde(default)]
    pub recorder: RecorderConfig,
    #[serde(default)]
    pub retention: RetentionConfig,
    #[serde(default)]
    pub websocket: WebSocketConfig,
    #[serde(default)]
    pub auto_train: AutoTrainConfig,
}



#[derive(Debug, Deserialize, Clone, Default)]
pub struct RecorderConfig {
    #[serde(default)]
    pub debug_mode: bool,
    #[serde(default)]
    pub payload: PayloadConfig,
}



#[derive(Debug, Deserialize, Clone, Default)]
pub struct PayloadConfig {
    #[serde(default = "default_none")]
    pub agg_trade: String,
    #[serde(default = "default_none")]
    pub book_ticker: String,
    #[serde(default = "default_sample")]
    pub depth: String,
    #[serde(default = "default_sample")]
    pub mark_price: String,
}



fn default_none() -> String { "none".to_string() }
fn default_sample() -> String { "sample".to_string() }

#[derive(Debug, Deserialize, Clone, Default)]
pub struct RetentionConfig {
    #[serde(default = "default_hot_window")]
    pub hot_window_days: u64,
    #[serde(default = "default_warm_window")]
    pub warm_window_days: u64,
    #[serde(default = "default_check_interval")]
    pub check_interval_hours: u64,
    #[serde(default = "default_dry_run")]
    pub dry_run: bool,
}



fn default_hot_window() -> u64 { 3 }
fn default_warm_window() -> u64 { 30 }
fn default_check_interval() -> u64 { 24 }
fn default_dry_run() -> bool { true }

#[derive(Debug, Deserialize, Clone)]
pub struct WebSocketConfig {
    #[serde(default = "default_stall_threshold_sec")]
    pub stall_threshold_sec: u64,
}

impl Default for WebSocketConfig {
    fn default() -> Self {
        Self {
            stall_threshold_sec: 5,
        }
    }
}

fn default_stall_threshold_sec() -> u64 { 5 }

#[derive(Debug, Deserialize, Clone, Default)]
pub struct AutoTrainConfig {
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default = "default_60")]
    pub interval_minutes: u64,
    #[serde(default = "default_5")]
    pub min_new_files: u32,
    #[serde(default = "default_24")]
    pub train_window_hours: u32,
    #[serde(default = "default_steps")]
    pub max_steps_per_cycle: u32,
    #[serde(default = "default_false")]
    pub dry_run: bool,
    #[serde(default = "default_12")]
    pub evaluation_window_hours: u32,
    #[serde(default = "default_symbol")]
    pub symbol: String,
    #[serde(default = "default_3")]
    pub max_models_per_day: u32,
}



fn default_true() -> bool { true }
fn default_false() -> bool { false }
fn default_60() -> u64 { 60 }
fn default_5() -> u32 { 5 }
fn default_24() -> u32 { 24 }
fn default_12() -> u32 { 12 }
fn default_3() -> u32 { 3 }
fn default_steps() -> u32 { 100000 }
fn default_symbol() -> String { "BTCUSDT".to_string() }
