use serde::{Deserialize, Serialize};
use rust_decimal::Decimal;

#[derive(Debug, Deserialize, Serialize, Clone)]
#[serde(tag = "e")]
pub enum BinanceEvent {
    #[serde(rename = "aggTrade")]
    AggTrade(AggTrade),
    #[serde(rename = "depthUpdate")]
    DepthUpdate(DepthUpdate),
    #[serde(rename = "bookTicker")]
    BookTicker(BookTicker),
    #[serde(rename = "markPriceUpdate")]
    MarkPriceUpdate(MarkPriceUpdate),
    #[serde(rename = "forceOrder")]
    ForceOrder(ForceOrder),
    #[serde(rename = "openInterest")]
    OpenInterest(OpenInterestEvent),
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct OpenInterestEvent {
    #[serde(rename = "symbol")]
    pub symbol: String,
    #[serde(rename = "sumOpenInterest")]
    pub open_interest: String, // String from API
    #[serde(rename = "sumOpenInterestValue")]
    pub open_interest_value: String,
    #[serde(rename = "timestamp")]
    pub time: i64,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct AggTrade {
    #[serde(rename = "E")]
    pub event_time: i64,
    #[serde(rename = "s")]
    pub symbol: String,
    #[serde(rename = "a")]
    pub agg_trade_id: i64,
    #[serde(rename = "p")]
    pub price: Decimal,
    #[serde(rename = "q")]
    pub quantity: Decimal,
    #[serde(rename = "f")]
    pub first_trade_id: i64,
    #[serde(rename = "l")]
    pub last_trade_id: i64,
    #[serde(rename = "T")]
    pub trade_time: i64,
    #[serde(rename = "m")]
    pub is_buyer_maker: bool,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct DepthUpdate {
    #[serde(rename = "E")]
    pub event_time: i64,
    #[serde(rename = "T")]
    pub transaction_time: i64,
    #[serde(rename = "s")]
    pub symbol: String,
    #[serde(rename = "U")]
    pub first_update_id: i64,
    #[serde(rename = "u")]
    pub final_update_id: i64,
    #[serde(rename = "pu")]
    pub prev_update_id: i64,
    #[serde(rename = "b")]
    pub bids: Vec<(Decimal, Decimal)>, // [Price, Quantity]
    #[serde(rename = "a")]
    pub asks: Vec<(Decimal, Decimal)>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct BookTicker {
    #[serde(rename = "u")]
    pub update_id: i64,
    #[serde(rename = "s")]
    pub symbol: String,
    #[serde(rename = "b")]
    pub best_bid_price: Decimal,
    #[serde(rename = "B")]
    pub best_bid_qty: Decimal,
    #[serde(rename = "a")]
    pub best_ask_price: Decimal,
    #[serde(rename = "A")]
    pub best_ask_qty: Decimal,
    #[serde(rename = "T")]
    pub transaction_time: i64,
    #[serde(rename = "E")]
    pub event_time: i64,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct MarkPriceUpdate {
    #[serde(rename = "E")]
    pub event_time: i64,
    #[serde(rename = "s")]
    pub symbol: String,
    #[serde(rename = "p")]
    pub mark_price: Decimal,
    #[serde(rename = "i")]
    pub index_price: Decimal,
    #[serde(rename = "P")]
    pub estimated_settle_price: Decimal,
    #[serde(rename = "r")]
    pub funding_rate: Decimal,
    #[serde(rename = "T")]
    pub next_funding_time: i64,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ForceOrder {
    #[serde(rename = "E")]
    pub event_time: i64,
    #[serde(rename = "s")]
    pub symbol: String,
    #[serde(rename = "o")]
    pub order: ForceOrderData,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ForceOrderData {
    #[serde(rename = "S")]
    pub side: String, // SELL or BUY
    #[serde(rename = "q")]
    pub original_quantity: Decimal,
    #[serde(rename = "p")]
    pub price: Decimal,
    #[serde(rename = "f")]
    pub time_in_force: String,
    #[serde(rename = "o")]
    pub order_type: String,
    #[serde(rename = "T")]
    pub trade_time: i64,
    #[serde(rename = "X")]
    pub order_status: String,
}
