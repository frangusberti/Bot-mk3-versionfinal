use serde::Deserialize;
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use futures_util::StreamExt;
use url::Url;
use bot_data::normalization::schema::NormalizedMarketEvent;

#[derive(Debug, Deserialize)]
pub struct BinanceAggTrade {
    #[serde(rename = "E")]
    pub event_time: i64,
    #[serde(rename = "p")]
    pub price: String,
    #[serde(rename = "q")]
    pub qty: String,
    #[serde(rename = "m")]
    pub is_buyer_maker: bool,
}

#[derive(Debug, Deserialize)]
pub struct BinanceBookTicker {
    #[serde(rename = "E")]
    pub event_time: i64,
    #[serde(rename = "b")]
    pub bid_price: String,
    #[serde(rename = "B")]
    pub bid_qty: String,
    #[serde(rename = "a")]
    pub ask_price: String,
    #[serde(rename = "A")]
    pub ask_qty: String,
}

pub struct LiveMarketStream {
    pub symbol: String,
    tx: mpsc::Sender<NormalizedMarketEvent>,
}

impl LiveMarketStream {
    pub fn new(symbol: String, tx: mpsc::Sender<NormalizedMarketEvent>) -> Self {
        Self { symbol, tx }
    }

    pub async fn run(&self) {
        let symbol_lower = self.symbol.to_lowercase();
        let url = format!("wss://fstream.binance.com/ws/{}@aggTrade/{}@bookTicker", symbol_lower, symbol_lower);
        
        loop {
            log::info!("LIVE_STREAM: Connecting to {}", url);
            match connect_async(url.clone()).await {
                Ok((mut ws_stream, _)) => {
                    log::info!("LIVE_STREAM: Connected (WS:OK)");
                    while let Some(msg) = ws_stream.next().await {
                        match msg {
                            Ok(Message::Text(text)) => {
                                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                                    if let Some(event_type) = val.get("e").and_then(|e| e.as_str()) {
                                        let mut norm = NormalizedMarketEvent::default();
                                        norm.symbol = self.symbol.clone();
                                        norm.exchange = "binance".to_string();
                                        norm.market_type = "future".to_string();
                                        
                                        match event_type {
                                            "aggTrade" => {
                                                if let Ok(trade) = serde_json::from_str::<BinanceAggTrade>(&text) {
                                                    norm.stream_name = "aggTrade".to_string();
                                                    norm.event_type = "trade".to_string();
                                                    norm.time_canonical = trade.event_time;
                                                    norm.time_exchange = trade.event_time;
                                                    norm.price = trade.price.parse().ok();
                                                    norm.qty = trade.qty.parse().ok();
                                                    norm.side = Some(if trade.is_buyer_maker { "Sell".to_string() } else { "Buy".to_string() });
                                                }
                                            }
                                            "bookTicker" => {
                                                if let Ok(ticker) = serde_json::from_str::<BinanceBookTicker>(&text) {
                                                    norm.stream_name = "bookTicker".to_string();
                                                    norm.event_type = "bookTicker".to_string();
                                                    norm.time_canonical = ticker.event_time;
                                                    norm.time_exchange = ticker.event_time;
                                                    norm.best_bid = ticker.bid_price.parse().ok();
                                                    norm.best_ask = ticker.ask_price.parse().ok();
                                                }
                                            }
                                            _ => continue,
                                        }
                                        
                                        if self.tx.send(norm).await.is_err() {
                                            return;
                                        }
                                    }
                                }
                            }
                            Ok(Message::Close(_)) => break,
                            Err(e) => {
                                log::error!("LIVE_STREAM ERROR: {}", e);
                                break;
                            }
                            _ => {}
                        }
                    }
                }
                Err(e) => {
                    log::error!("LIVE_STREAM CONNECTION ERROR: {}", e);
                }
            }
            log::warn!("LIVE_STREAM: Reconnecting in 5s (WS:RECONN)...");
            tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
        }
    }
}
