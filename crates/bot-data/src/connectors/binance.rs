use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use futures_util::{StreamExt, SinkExt};
use url::Url;
use log::{info, error, warn};
use anyhow::Result;
use tokio::sync::mpsc;
use bot_core::schema::MarketEvent;
use serde_json::Value;
use chrono::{TimeZone, Utc};
use rust_decimal::Decimal;
use std::str::FromStr;
use bot_core::schema::{Trade, Ticker, FundingRate, Exchange, Side, BookDelta, Level};

fn parse_binance_message(text: &str) -> Result<Option<MarketEvent>> {
    let mut v: Value = serde_json::from_str(text)?;
    
    // If using combined streams, the actual event is inside "data".
    if let Some(data) = v.get("data") {
        v = data.clone();
    }
    
    // Check if it's an event
    if let Some(e_type) = v.get("e").and_then(|val| val.as_str()) {
        match e_type {
            "depthUpdate" => {
                let symbol = v["s"].as_str().unwrap_or_default().to_string();
                let first_update_id = v["U"].as_u64().unwrap_or(0);
                let final_update_id = v["u"].as_u64().unwrap_or(0);
                let prev_update_id = v["pu"].as_u64().unwrap_or(0); // Optional field, usually present
                let ts = v["E"].as_i64().unwrap_or(0);
                
                let parse_levels = |levels: &Value| -> Vec<Level> {
                    if let Some(arr) = levels.as_array() {
                        arr.iter().filter_map(|x| {
                            let p_str = x[0].as_str()?;
                            let q_str = x[1].as_str()?;
                            Some(Level {
                                price: Decimal::from_str(p_str).ok()?,
                                quantity: Decimal::from_str(q_str).ok()?,
                            })
                        }).collect()
                    } else {
                        Vec::new()
                    }
                };

                let bids = parse_levels(&v["b"]);
                let asks = parse_levels(&v["a"]);

                Ok(Some(MarketEvent::BookDelta(BookDelta {
                    exchange: Exchange::Binance,
                    symbol,
                    first_update_id,
                    final_update_id,
                    prev_update_id,
                    bids,
                    asks,
                    timestamp: Utc.timestamp_millis_opt(ts).unwrap(),
                })))
            },
            "aggTrade" => {
                let symbol = v["s"].as_str().unwrap_or_default().to_string();
                let price = Decimal::from_str(v["p"].as_str().unwrap_or("0"))?;
                let qty = Decimal::from_str(v["q"].as_str().unwrap_or("0"))?;
                let ts = v["T"].as_i64().unwrap_or(0);
                let is_buyer_maker = v["m"].as_bool().unwrap_or(false);
                
                // AggTrade doesn't have side explicit, inferred from is_buyer_maker
                // buyer_maker = true -> Sell side initiated? No.
                // If buyer is maker, then seller is taker -> Sell.
                // If buyer is taker, then buyer_maker = false -> Buy.
                let side = if is_buyer_maker { Side::Sell } else { Side::Buy };
                
                Ok(Some(MarketEvent::Trade(Trade {
                    exchange: Exchange::Binance,
                    symbol,
                    trade_id: v["a"].as_u64().unwrap_or(0).to_string(),
                    price,
                    quantity: qty,
                    side,
                    is_liquidation: false,
                    timestamp: Utc.timestamp_millis_opt(ts).unwrap(),
                })))
            },
            "bookTicker" => {
                let symbol = v["s"].as_str().unwrap_or_default().to_string();
                let params = (
                    v["b"].as_str().unwrap_or("0"),
                    v["a"].as_str().unwrap_or("0"),
                    v["B"].as_str().unwrap_or("0"), // bid qty (not used in Ticker struct?)
                    v["A"].as_str().unwrap_or("0")  // ask qty
                );
                
                let best_bid = Decimal::from_str(params.0)?;
                let best_ask = Decimal::from_str(params.1)?;
                let ts = v["T"].as_i64().unwrap_or(0); // T is transaction time, E is event time? bookTicker usually u/u/T/E
                // "T" is transaction time.
                let ts_val = if ts > 0 { ts } else { v["E"].as_i64().unwrap_or(Utc::now().timestamp_millis()) };

                Ok(Some(MarketEvent::Ticker(Ticker {
                    exchange: Exchange::Binance,
                    symbol,
                    best_bid,
                    best_ask,
                    last_price: (best_bid + best_ask) / Decimal::from(2), // Mid price approx if no last price in bookTicker
                    timestamp: Utc.timestamp_millis_opt(ts_val).unwrap(),
                })))
            },
            "markPriceUpdate" => {
                let symbol = v["s"].as_str().unwrap_or_default().to_string();
                let mark_price = Decimal::from_str(v["p"].as_str().unwrap_or("0"))?;
                let rate = Decimal::from_str(v["r"].as_str().unwrap_or("0"))?;
                let next_funding = v["T"].as_i64().unwrap_or(0);
                let ts = v["E"].as_i64().unwrap_or(0);
                
                Ok(Some(MarketEvent::FundingRate(FundingRate {
                    exchange: Exchange::Binance,
                    symbol,
                    rate,
                    mark_price,
                    timestamp: Utc.timestamp_millis_opt(ts).unwrap(),
                    next_funding_time: Utc.timestamp_millis_opt(next_funding).unwrap(),
                })))
            },
            _ => Ok(None)
        }
    } else {
        // specific check for bookTicker simple payload? 
        // bookTicker sometimes comes as simple object without "e" if requested via REST, but WS always has "e".
        Ok(None)
    }
}

pub struct BinanceFuturesWS {
    symbol: String,
    url: String,
}

impl BinanceFuturesWS {
    pub fn new(symbol: &str, testnet: bool) -> Self {
        let base_url = if testnet {
            "wss://stream.binancefuture.com/stream?streams="
        } else {
            "wss://fstream.binance.com/stream?streams="
        };
        
        Self {
            symbol: symbol.to_lowercase(),
            url: base_url.to_string(),
        }
    }

    pub async fn connect(&self, tx: mpsc::Sender<MarketEvent>) -> Result<()> {
        let stream_url = format!("{}{symbol}@aggTrade/{symbol}@depth@100ms/{symbol}@bookTicker", self.url, symbol=self.symbol);
        
        // Network Firewall for Offline Mode
        if std::env::var("BOT_OFFLINE_MODE").ok().map(|v| v == "1" || v.to_lowercase() == "true").unwrap_or(false) {
            error!("OFFLINE MODE ENABLED: Blocking outbound connection to {}", stream_url);
            return Err(anyhow::anyhow!("Network blocked: BOT_OFFLINE_MODE is active"));
        }

        info!("Connecting to Binance Futures WS: {}", stream_url);

        let (ws_stream, _) = connect_async(Url::parse(&stream_url)?).await?;
        info!("Connected to Binance Futures WS");

        let (mut write, mut read) = ws_stream.split();

        while let Some(msg) = read.next().await {
            match msg {
                Ok(Message::Text(text)) => {
                    match parse_binance_message(&text) {
                        Ok(Some(event)) => {
                            if let Err(e) = tx.send(event).await {
                                error!("Failed to send event to channel: {}", e);
                                break;
                            }
                        }
                        Ok(None) => {
                            // Ignored message (e.g. connection response)
                        }
                        Err(e) => {
                            error!("Error parsing message: {}. Payload: {}", e, text);
                        }
                    }
                }
                Ok(Message::Ping(payload)) => {
                    write.send(Message::Pong(payload)).await?;
                }
                Ok(Message::Close(_)) => {
                    warn!("Connection closed by server");
                    break;
                }
                Err(e) => {
                    error!("WebSocket error: {}", e);
                    break;
                }
                _ => {}
            }
        }
        
        Ok(())
    }
}
