use async_trait::async_trait;
use crate::execution::{ExecutionInterface, PositionInfo};
use super::client::BinanceClient;
use std::sync::Arc;
use tokio::time::{sleep, Duration};
use log::{info, warn, error};

pub struct LiveExecutionAdapter {
    client: Arc<BinanceClient>,
    max_retries: u32,
    retry_backoff_ms: u64,
    exchange_info_loaded: bool,
}

impl LiveExecutionAdapter {
    pub fn new(api_key: String, secret_key: String, max_retries: u32, retry_backoff_ms: u64) -> Self {
        Self {
            client: Arc::new(BinanceClient::new(api_key, secret_key)),
            max_retries,
            retry_backoff_ms,
            exchange_info_loaded: false,
        }
    }

    pub fn new_testnet(api_key: String, secret_key: String, max_retries: u32, retry_backoff_ms: u64) -> Self {
        Self {
            client: Arc::new(BinanceClient::new_testnet(api_key, secret_key)),
            max_retries,
            retry_backoff_ms,
            exchange_info_loaded: false,
        }
    }

    /// Get a clone of the underlying BinanceClient for starting the User Data Stream.
    pub fn client(&self) -> Arc<BinanceClient> {
        self.client.clone()
    }

    /// Ensure exchange info is loaded (called lazily on first order).
    async fn ensure_exchange_info(&mut self) {
        if !self.exchange_info_loaded {
            match self.client.load_exchange_info().await {
                Ok(()) => {
                    self.exchange_info_loaded = true;
                }
                Err(e) => {
                    error!("[LIVE] Failed to load exchange info: {}. Using default precision.", e);
                }
            }
        }
    }
}

#[async_trait]
impl ExecutionInterface for LiveExecutionAdapter {
    async fn submit_order(&mut self, symbol: &str, side: &str, qty: f64, price: f64, order_type: &str) -> Result<String, String> {
        // Ensure we have exchange info for correct precision
        self.ensure_exchange_info().await;

        let mut attempt = 0;
        loop {
            match self.client.post_order(symbol, side, qty, price, order_type).await {
                Ok(order_id) => {
                    if attempt > 0 {
                        info!("Order submitted successfully after {} retries: {}", attempt, order_id);
                    }
                    return Ok(order_id);
                },
                Err(e) => {
                    let err_msg = e.to_string();

                    if attempt >= self.max_retries {
                        error!("Failed to submit order after {} attempts: {}", attempt, err_msg);
                        return Err(err_msg);
                    }

                    warn!("Order submission failed (attempt {}/{}): {}. Retrying...", attempt + 1, self.max_retries, err_msg);

                    sleep(Duration::from_millis(self.retry_backoff_ms * 2u64.pow(attempt))).await;
                    attempt += 1;
                }
            }
        }
    }

    async fn cancel_order(&mut self, symbol: &str, order_id: &str) -> Result<(), String> {
        self.client.cancel_order(symbol, order_id).await
            .map_err(|e| e.to_string())
    }

    async fn get_position(&self, symbol: &str) -> Result<PositionInfo, String> {
        match self.client.get_position(symbol).await {
            Ok(Some(pos)) => Ok(pos),
            Ok(None) => Ok(PositionInfo {
                symbol: symbol.to_string(),
                side: "Flat".to_string(),
                qty: 0.0,
                entry_price: 0.0,
                unrealized_pnl: 0.0,
                realized_fees: 0.0,
                realized_funding: 0.0,
                realized_pnl: 0.0,
                margin_used: 0.0,
                notional_value: 0.0,
            }),
            Err(e) => Err(e.to_string()),
        }
    }

    async fn get_equity(&self) -> Result<f64, String> {
        self.client.get_balance().await
            .map_err(|e| e.to_string())
    }

    async fn set_leverage(&mut self, symbol: &str, leverage: f64) -> Result<(), String> {
        let lev_u32 = leverage as u32;
        match self.client.set_leverage(symbol, lev_u32).await {
            Ok(_) => {
                info!("[LEV][LIVE][{}] Successfully set leverage to {}", symbol, lev_u32);
                Ok(())
            },
            Err(e) => {
                let err_msg = e.to_string();
                error!("[LEV][LIVE][{}] Failed to set leverage: {}", symbol, err_msg);
                Err(err_msg)
            }
        }
    }
}
