use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum TimeMode {
    #[default]
    EventTimeOnly,
    RecvTimeAware,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NormalizedMarketEvent {
    pub schema_version: u16,
    pub run_id: String,
    pub exchange: String, // "binance"
    pub market_type: String, // "future"
    pub symbol: String,
    pub stream_name: String, // "aggTrade", "depthUpdate", etc.
    pub event_type: String, // "trade", "depth", "bookTicker"
    pub time_exchange: i64, // UTC ms
    pub time_local: i64,    // UTC ms
    pub time_canonical: i64, // UTC ms - Deterministic monotonic time
    pub recv_time: Option<i64>, // UTC ms - Time when the event was actually received (for anti-leakage)
    pub price: Option<f64>,
    pub qty: Option<f64>,
    pub side: Option<String>,
    pub best_bid: Option<f64>,
    pub best_ask: Option<f64>,
    pub mark_price: Option<f64>,
    pub funding_rate: Option<f64>,
    pub liquidation_price: Option<f64>,
    pub liquidation_qty: Option<f64>,
    pub open_interest: Option<f64>,
    pub open_interest_value: Option<f64>,
    pub update_id_first: Option<i64>,
    pub update_id_final: Option<i64>,
    pub update_id_prev: Option<i64>,
    pub payload_json: String, // Full original payload
}

// DatasetManifest and QualitySummary moved to manifest.rs

impl Default for NormalizedMarketEvent {
    fn default() -> Self {
        Self {
            schema_version: 1,
            run_id: String::new(),
            exchange: String::new(),
            market_type: String::new(),
            symbol: String::new(),
            stream_name: String::new(),
            event_type: String::new(),
            time_exchange: 0,
            time_local: 0,
            time_canonical: 0,
            recv_time: None,
            price: None,
            qty: None,
            side: None,
            best_bid: None,
            best_ask: None,
            mark_price: None,
            funding_rate: None,
            liquidation_price: None,
            liquidation_qty: None,
            open_interest: None,
            open_interest_value: None,
            update_id_first: None,
            update_id_final: None,
            update_id_prev: None,
            payload_json: String::new(),
        }
    }
}
