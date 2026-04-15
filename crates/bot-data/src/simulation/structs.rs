use serde::{Serialize, Deserialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Buy,
    Sell,
}

impl Side {
    pub fn opposite(&self) -> Self {
        match self {
            Side::Buy => Side::Sell,
            Side::Sell => Side::Buy,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderType {
    Market,
    Limit,
    StopLoss,
    TakeProfit,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderStatus {
    New,
    Penned, // Latency simulation
    Open,
    PartiallyFilled,
    Filled,
    Cancelling, // Waiting for cancellation latency
    Cancelled,
    Rejected,
    Expired,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum LiquidityFlag {
    Maker,
    Taker,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CostSource {
    ExchangeRealized,
    Simulated,
    Estimated,
    Inferred,
}

/// How the simulated slippage is computed.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SlippageModel {
    /// Fixed slippage in bps applied to every fill.
    Flat(f64),
    /// Walk top-N levels of the L2 book to compute VWAP fill price.
    TopN(usize),
    /// Conservative maker execution simulation incorporating queue position.
    ConservativeMaker(MakerQueueConfig),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MakerFillModel {
    Conservative,   // Strict queue modeling
    SemiOptimistic, // Scaled queue (10% of standard)
    Optimistic,     // Fill on price touch
}

impl Default for MakerFillModel {
    fn default() -> Self { Self::Conservative }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MakerQueueConfig {
    pub default_latency_ms: i64,
    pub assume_half_queue: bool, // If entering at BBO, assume we are behind 50% of the standing volume.
}

impl Default for SlippageModel {
    fn default() -> Self { Self::TopN(5) }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderState {
    pub id: String,
    pub symbol: String,
    pub side: Side,
    pub order_type: OrderType,
    pub price: f64,
    pub qty: f64,
    pub remaining: f64,
    pub status: OrderStatus,
    pub created_ts: i64,
    pub active_from_ts: i64, // For latency simulation
    pub pending_cancel_ts: i64, // When cancellation becomes effective
    pub expires_ts: Option<i64>,
    pub queue_state: Option<QueueState>, // Track position in queue for Maker orders
    pub was_marketable_on_arrival: bool,
    pub accepted_as_passive: bool,
    pub resting_since_ts: Option<i64>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct QueueState {
    pub position_ahead: f64, // Quantity of resting orders in front of us
    pub original_price: f64, // Price level we are queued at
}

/// A single fill event produced by the execution engine.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FillEvent {
    pub order_id: String,
    pub symbol: String,
    pub side: Side,
    pub qty_filled: f64,
    pub price: f64,          // actual fill price (VWAP if walking L2)
    pub fee_paid: f64,
    pub liquidity_flag: LiquidityFlag,
    pub slippage_bps: f64,   // vs mid price at time of fill
    pub event_time: i64,
    pub cost_source: CostSource,
    pub is_toxic: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PositionState {
    pub symbol: String,
    pub side: Side,
    pub qty: f64,
    pub entry_vwap: f64,
    pub realized_pnl: f64,
    pub unrealized_pnl: f64,
    pub realized_fees: f64,
    pub realized_funding: f64,
    pub open_ts: i64,
    pub last_update_ts: i64,
    pub liquidation_price: f64,
    pub margin_used: f64,
    pub notional_value: f64,
    pub leverage: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortfolioStats {
    pub value_at_risk: f64,
    pub max_drawdown_daily: f64,
    pub total_trades: u64,
    pub win_rate: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortfolioState {
    pub cash_usdt: f64,
    pub equity_usdt: f64,
    pub margin_used: f64,
    pub available_margin: f64, 
    pub positions: HashMap<String, PositionState>,
    pub active_orders: HashMap<String, OrderState>,
    pub stats: PortfolioStats,
    pub cumulative_fees: HashMap<String, f64>,
    pub cumulative_funding: HashMap<String, f64>,
    pub cumulative_pnl: HashMap<String, f64>,
    pub trading_fees_entry: f64,
    pub trading_fees_exit: f64,
    pub funding_pnl: f64,
    pub slippage_cost: f64,
    pub leverage_map: HashMap<String, f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExecutionConfig {
    pub base_capital_usdt: f64,
    pub leverage_cap: f64,
    pub maker_fee_bps: f64,
    pub taker_fee_bps: f64,
    pub latency_ms: i64,
    pub exit_timeout_ms: i64,
    pub disaster_stop_dd_daily_pct: f64,
    pub allow_taker_for_disaster_exit: bool,
    pub allow_mock_fills: bool,
    pub slip_bps: f64,
    pub symbol_whitelist: Vec<String>,
    #[serde(default = "default_max_retries")]
    pub max_retries: u32,
    #[serde(default = "default_retry_backoff_ms")]
    pub retry_backoff_ms: u64,
    /// Slippage model: Flat(bps) or TopN(depth) for L2 book simulation.
    #[serde(default)]
    pub slippage_model: SlippageModel,
    #[serde(default)]
    pub maker_fill_model: MakerFillModel,
}

fn default_max_retries() -> u32 { 3 }
fn default_retry_backoff_ms() -> u64 { 100 }

impl Default for ExecutionConfig {
    fn default() -> Self {
        Self {
            base_capital_usdt: 1500.0,
            leverage_cap: 5.0,
            maker_fee_bps: 2.0,
            taker_fee_bps: 5.0,
            latency_ms: 50,
            exit_timeout_ms: 5000,
            disaster_stop_dd_daily_pct: 5.0,
            allow_taker_for_disaster_exit: true,
            allow_mock_fills: false,
            slip_bps: 1.0,
            symbol_whitelist: vec!["BTCUSDT".to_string(), "ETHUSDT".to_string()],
            max_retries: 3,
            retry_backoff_ms: 100,
            slippage_model: SlippageModel::default(),
            maker_fill_model: MakerFillModel::default(),
        }
    }
}
