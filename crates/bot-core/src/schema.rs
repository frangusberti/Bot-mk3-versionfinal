use serde::{Deserialize, Serialize};
use chrono::{DateTime, Utc};
use rust_decimal::Decimal;
use uuid::Uuid;

// ============================================================================
//  Time Model
// ============================================================================

/// Deterministic event timestamp in UTC milliseconds.
/// Implements Ord for total ordering. For tie-breaking during replay,
/// use (EventTime, sequence_number).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct EventTime(pub i64);

impl EventTime {
    pub fn from_millis(ms: i64) -> Self { Self(ms) }
    pub fn as_millis(&self) -> i64 { self.0 }
    pub fn from_datetime(dt: DateTime<Utc>) -> Self { Self(dt.timestamp_millis()) }
}

impl std::fmt::Display for EventTime {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}ms", self.0)
    }
}

// ============================================================================
//  Exchange & Side
// ============================================================================

/// Represents the source of a market event.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Exchange {
    Binance,
    Bybit,
    Okx,
    Backtest, // For simulation
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Buy,
    Sell,
}

// ============================================================================
//  Market Data Schemas
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum MarketEvent {
    Trade(Trade),
    AggTrade(AggTrade),
    BookSnapshot(BookSnapshot),
    BookDelta(BookDelta),
    BookTicker(BookTicker),
    FundingRate(FundingRate),
    Liquidation(Liquidation),
    Ticker(Ticker),
    OpenInterest(OpenInterest),
    Heartbeat { exchange: Exchange, timestamp: DateTime<Utc> },
}

impl MarketEvent {
    /// Extract the canonical event timestamp from any variant.
    pub fn event_time(&self) -> EventTime {
        match self {
            Self::Trade(t) => EventTime::from_datetime(t.timestamp),
            Self::AggTrade(t) => EventTime::from_datetime(t.timestamp),
            Self::BookSnapshot(b) => EventTime::from_datetime(b.timestamp),
            Self::BookDelta(b) => EventTime::from_datetime(b.timestamp),
            Self::BookTicker(b) => EventTime::from_datetime(b.timestamp),
            Self::FundingRate(f) => EventTime::from_datetime(f.timestamp),
            Self::Liquidation(l) => EventTime::from_datetime(l.timestamp),
            Self::Ticker(t) => EventTime::from_datetime(t.timestamp),
            Self::OpenInterest(o) => EventTime::from_datetime(o.timestamp),
            Self::Heartbeat { timestamp, .. } => EventTime::from_datetime(*timestamp),
        }
    }

    /// Extract the symbol from any variant.
    pub fn symbol(&self) -> &str {
        match self {
            Self::Trade(t) => &t.symbol,
            Self::AggTrade(t) => &t.symbol,
            Self::BookSnapshot(b) => &b.symbol,
            Self::BookDelta(b) => &b.symbol,
            Self::BookTicker(b) => &b.symbol,
            Self::FundingRate(f) => &f.symbol,
            Self::Liquidation(l) => &l.symbol,
            Self::Ticker(t) => &t.symbol,
            Self::OpenInterest(o) => &o.symbol,
            Self::Heartbeat { .. } => "",
        }
    }
}

/// A normalized trade event from an exchange.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Trade {
    pub exchange: Exchange,
    pub symbol: String,
    pub trade_id: String,
    pub price: Decimal,
    pub quantity: Decimal,
    pub side: Side,
    pub is_liquidation: bool,
    pub timestamp: DateTime<Utc>,
}

/// A full L2 Order Book Snapshot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BookSnapshot {
    pub exchange: Exchange,
    pub symbol: String,
    pub last_update_id: u64,
    pub bids: Vec<Level>,
    pub asks: Vec<Level>,
    pub timestamp: DateTime<Utc>,
}

/// L2 Order Book Updates (Deltas).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BookDelta {
    pub exchange: Exchange,
    pub symbol: String,
    pub first_update_id: u64,
    pub final_update_id: u64,
    pub prev_update_id: u64,
    /// Bids to update. Qty=0 means delete.
    pub bids: Vec<Level>, 
    /// Asks to update. Qty=0 means delete.
    pub asks: Vec<Level>,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Level {
    pub price: Decimal,
    pub quantity: Decimal,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct FundingRate {
    pub exchange: Exchange,
    pub symbol: String,
    pub rate: Decimal,
    pub mark_price: Decimal,
    pub timestamp: DateTime<Utc>,
    pub next_funding_time: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Liquidation {
    pub exchange: Exchange,
    pub symbol: String,
    pub side: Side,
    pub price: Decimal,
    pub quantity: Decimal,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Ticker {
    pub exchange: Exchange,
    pub symbol: String,
    pub best_bid: Decimal,
    pub best_ask: Decimal,
    pub last_price: Decimal,
    pub timestamp: DateTime<Utc>,
}

/// Binance Aggregated Trade — includes is_buyer_maker for taker flow direction.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AggTrade {
    pub exchange: Exchange,
    pub symbol: String,
    pub agg_trade_id: u64,
    pub price: Decimal,
    pub quantity: Decimal,
    /// If true, the buyer is the maker (sell-side aggressor / taker sell).
    /// If false, the seller is the maker (buy-side aggressor / taker buy).
    pub is_buyer_maker: bool,
    pub first_trade_id: u64,
    pub last_trade_id: u64,
    pub timestamp: DateTime<Utc>,
}

/// Best Bid/Ask with quantities — from Binance bookTicker stream.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BookTicker {
    pub exchange: Exchange,
    pub symbol: String,
    pub best_bid: Decimal,
    pub best_bid_qty: Decimal,
    pub best_ask: Decimal,
    pub best_ask_qty: Decimal,
    pub timestamp: DateTime<Utc>,
}

/// Open Interest snapshot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OpenInterest {
    pub exchange: Exchange,
    pub symbol: String,
    pub open_interest: Decimal,
    pub timestamp: DateTime<Utc>,
}

// ----------------------------------------------------------------------------
// Control & Execution Schemas
// ----------------------------------------------------------------------------

/// The "Brain" output - a desired action.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Signal {
    pub id: Uuid,
    pub created_at: DateTime<Utc>,
    pub symbol: String,
    pub direction: SignalDirection,
    pub confidence: f64, // 0.0 to 1.0
    /// Estimated Profit Margin (Net of Fees)
    pub est_net_pnl_pct: Option<Decimal>, 
    pub metadata: String, // JSON payload or Model ID
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SignalDirection {
    Long,
    Short,
    Flat,
    Hold,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OrderRequest {
    pub id: Uuid,
    pub symbol: String,
    pub exchange: Exchange,
    pub side: Side,
    pub order_type: OrderType,
    pub quantity: Decimal,
    pub price: Option<Decimal>,
    pub reduce_only: bool,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderType {
    Market,
    Limit,
    StopMarket,
    StopLimit,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExecutionReport {
    pub order_id: Uuid,
    pub exchange_order_id: String,
    pub symbol: String,
    pub status: OrderStatus,
    pub filled_quantity: Decimal,
    pub avg_price: Decimal,
    pub fee: Decimal,
    pub fee_currency: String,
    pub timestamp: DateTime<Utc>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderStatus {
    New,
    PartiallyFilled,
    Filled,
    Canceled,
    Rejected,
    Expired,
}
