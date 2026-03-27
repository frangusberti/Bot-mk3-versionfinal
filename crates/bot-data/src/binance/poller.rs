use crate::binance::model::{BinanceEvent, OpenInterestEvent};
use log::{error, warn, info};
use std::time::Duration;
use tokio::sync::mpsc;
use tokio::time::sleep;

pub struct OpenInterestPoller {
    symbol: String,
    tx: mpsc::Sender<BinanceEvent>,
}

impl OpenInterestPoller {
    pub fn new(symbol: String, tx: mpsc::Sender<BinanceEvent>) -> Self {
        Self { symbol, tx }
    }

    pub async fn run(&self) {
        let client = reqwest::Client::new();
        let url = format!("https://fapi.binance.com/fapi/v1/openInterest?symbol={}", self.symbol.to_uppercase());

        loop {
            match client.get(&url).send().await {
                Ok(resp) => {
                    if resp.status().is_success() {
                        #[derive(serde::Deserialize)]
                        #[allow(non_snake_case)]
                        struct RESTResponse {
                            symbol: String,
                            openInterest: String,
                            time: i64,
                        }

                        match resp.json::<RESTResponse>().await {
                            Ok(oi_data) => {
                                info!("oi_poll_success: Fetched Open Interest for {}", self.symbol);
                                let event = BinanceEvent::OpenInterest(OpenInterestEvent {
                                    symbol: oi_data.symbol,
                                    open_interest: oi_data.openInterest,
                                    open_interest_value: "0.0".to_string(), // Not in REST v1
                                    time: oi_data.time,
                                });
                                if let Err(e) = self.tx.send(event).await {
                                    error!("Failed to send OI event: {}", e);
                                    break;
                                }
                            },
                            Err(e) => error!("Failed to parse OI json: {}", e),
                        }
                    } else {
                        warn!("OI Poll failed: {}", resp.status());
                    }
                },
                Err(e) => error!("OI Poll request error: {}", e),
            }
            
            sleep(Duration::from_secs(30)).await; // Poll every 30s
        }
    }
}
