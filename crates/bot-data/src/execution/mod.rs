use async_trait::async_trait;

// Use primitive types or shared types.

#[async_trait]
pub trait ExecutionInterface: Send + Sync {
    async fn submit_order(&mut self, symbol: &str, side: &str, qty: f64, price: f64, order_type: &str) -> Result<String, String>;
    async fn cancel_order(&mut self, symbol: &str, order_id: &str) -> Result<(), String>;
    async fn get_position(&self, symbol: &str) -> Result<PositionInfo, String>;
    async fn get_equity(&self) -> Result<f64, String>;
    async fn set_leverage(&mut self, symbol: &str, leverage: f64) -> Result<(), String>;
}

#[derive(Debug, Clone)]
pub struct PositionInfo {
    pub symbol: String,
    pub side: String,
    pub qty: f64,
    pub entry_price: f64,
    pub unrealized_pnl: f64,
    pub realized_fees: f64,
    pub realized_funding: f64,
    pub realized_pnl: f64,
    pub margin_used: f64,
    pub notional_value: f64,
}
