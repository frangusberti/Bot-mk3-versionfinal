use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ClockMode {
    Exchange = 0,
    Local = 1,
    Canonical = 2,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayConfig {
    pub speed: f64,
    pub clock_mode: ClockMode,
    pub start_ts: Option<i64>,
    pub end_ts: Option<i64>,
    pub allow_bad_quality: bool,
    pub ui_sample_every_n: i32,
    pub ui_max_events_per_sec: i32,
    pub debug_include_raw: bool,
}

impl Default for ReplayConfig {
    fn default() -> Self {
        Self {
            speed: 1.0,
            clock_mode: ClockMode::Exchange,
            start_ts: None,
            end_ts: None,
            allow_bad_quality: false,
            ui_sample_every_n: 50,
            ui_max_events_per_sec: 200,
            debug_include_raw: false,
        }
    }
}
