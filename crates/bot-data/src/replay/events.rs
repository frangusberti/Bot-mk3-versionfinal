use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum StreamPriority {
    Depth = 0,
    BookTicker = 1,
    AggTrade = 2,
    Trade = 3,
    MarkPrice = 4,
    Funding = 5,
    Liquidation = 6,
    OpenInterest = 7,
    Other = 8,
}

impl StreamPriority {
    pub fn from_stream_name(name: &str) -> Self {
        match name {
            "depthUpdate" | "depth" => StreamPriority::Depth,
            "bookTicker" => StreamPriority::BookTicker,
            "aggTrade" => StreamPriority::AggTrade,
            "trade" => StreamPriority::Trade,
            "markPriceUpdate" | "markPrice" => StreamPriority::MarkPrice,
            "funding" => StreamPriority::Funding,
            "liquidation" => StreamPriority::Liquidation,
            "openInterest" => StreamPriority::OpenInterest,
            _ => StreamPriority::Other,
        }
    }
}

/// Lightweight event structure for replay
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReplayEvent {
    pub symbol: String,
    pub stream_name: String,
    pub event_type: String,
    
    // Timestamps
    pub ts_exchange: i64,
    pub ts_local: i64,
    pub ts_canonical: i64,
    
    // Identifiers for stable sorting
    pub sequence_id: i64,
    pub file_part: u32,
    pub row_index: u32,
    
    // Core Data
    pub price: Option<f64>,
    pub quantity: Option<f64>,
    pub side: Option<String>,
    pub best_bid: Option<f64>,
    pub best_ask: Option<f64>,
    
    // Extended Data
    pub mark_price: Option<f64>,
    pub funding_rate: Option<f64>,
    pub liquidation_price: Option<f64>,
    pub liquidation_qty: Option<f64>,
    pub open_interest: Option<f64>,
    pub open_interest_value: Option<f64>,
    
    // Optional raw payload
    pub payload_json: Option<String>,
}
