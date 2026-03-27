use tokio::sync::mpsc;
use log::error;
use crate::connectors::binance::BinanceFuturesWS;
use crate::normalization::schema::NormalizedMarketEvent;
use bot_core::schema::MarketEvent;
use rust_decimal::prelude::ToPrimitive;

pub struct LiveMarketData {
    symbol: String,
    testnet: bool,
}

impl LiveMarketData {
    pub fn new(symbol: String, testnet: bool) -> Self {
        Self { symbol, testnet }
    }

    pub async fn run(self, tx: mpsc::Sender<Result<NormalizedMarketEvent, String>>) {
        let (ws_tx, mut ws_rx) = mpsc::channel(100);
        let ws = BinanceFuturesWS::new(&self.symbol, self.testnet);
        let symbol = self.symbol.clone();
        
        tokio::spawn(async move {
            loop {
                log::info!("Connecting to Binance WebSocket for {}...", symbol);
                if let Err(e) = ws.connect(ws_tx.clone()).await {
                    log::error!("WS Connection failed for {}: {}. Retrying in 5s...", symbol, e);
                    tokio::time::sleep(tokio::time::Duration::from_secs(5)).await;
                } else {
                    log::warn!("WS Connection closed for {}. Reconnecting...", symbol);
                    tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                }
            }
        });

        while let Some(event) = ws_rx.recv().await {
            if let Some(normalized) = self.normalize(event) {
                if tx.send(Ok(normalized)).await.is_err() {
                    break;
                }
            }
        }
    }

    fn normalize(&self, event: MarketEvent) -> Option<NormalizedMarketEvent> {
        let ts = chrono::Utc::now().timestamp_millis();
        
        // Helper to new NormalizedMarketEvent with defaults
        let mut norm = NormalizedMarketEvent {
            schema_version: 1,
            run_id: "live".to_string(),
            exchange: "binance".to_string(),
            market_type: "future".to_string(),
            symbol: self.symbol.clone(),
            stream_name: String::new(),
            event_type: String::new(),
            time_exchange: 0,
            time_local: ts,
            time_canonical: 0,
            recv_time: Some(ts),
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
        };

        match event {
            MarketEvent::Trade(t) => {
                norm.stream_name = "aggTrade".to_string(); // approximate mapping
                norm.event_type = "trade".to_string();
                norm.time_exchange = t.timestamp.timestamp_millis();
                norm.time_canonical = t.timestamp.timestamp_millis();
                norm.price = t.price.to_f64();
                norm.qty = t.quantity.to_f64();
                norm.side = Some(format!("{:?}", t.side).to_lowercase());
            },
            MarketEvent::Ticker(t) => {
                norm.stream_name = "bookTicker".to_string();
                norm.event_type = "bookTicker".to_string();
                norm.time_exchange = t.timestamp.timestamp_millis();
                norm.time_canonical = t.timestamp.timestamp_millis();
                norm.price = t.last_price.to_f64(); // Mid? or Last? Ticker has last_price.
                norm.best_bid = t.best_bid.to_f64();
                norm.best_ask = t.best_ask.to_f64();
            },
            MarketEvent::FundingRate(f) => {
                norm.stream_name = "markPrice".to_string();
                norm.event_type = "fundingRate".to_string();
                norm.time_exchange = f.timestamp.timestamp_millis();
                norm.time_canonical = f.timestamp.timestamp_millis();
                norm.mark_price = f.mark_price.to_f64();
                norm.funding_rate = f.rate.to_f64();
            },
            MarketEvent::BookDelta(d) => {
                norm.stream_name = "depthUpdate".to_string();
                norm.event_type = "depthUpdate".to_string();
                norm.time_exchange = d.timestamp.timestamp_millis();
                norm.time_canonical = d.timestamp.timestamp_millis();
                norm.update_id_first = Some(d.first_update_id as i64);
                norm.update_id_final = Some(d.final_update_id as i64);
                norm.update_id_prev = Some(d.prev_update_id as i64);
                // prev_update_id isn't in BookDelta schema yet? Checked binance.rs parsing, it puts it where?
                // binance.rs: parse_levels... BookDelta struct has first/final.
                // Wait, I need to check BookDelta struct definition in bot-core/schema.rs.
                // It has first_update_id, final_update_id. NOT prev_update_id.
                // I should probably add prev_update_id to BookDelta layout in schema.rs first?
                // Or just rely on first/final for now. OrderBook logic uses prev_update_id to detecting gap.
                // Without prev_update_id, we can only check strict continuity (first == last + 1).
                // That is sufficient for most cases.
                
                // Construct payload manually or serde?
                // We need to pass the raw-ish levels to apply_delta in Agent.
                // NormalizedMarketEvent payload_json is usually specific.
                // But Agent needs vector of (price, qty).
                // NormalizedMarketEvent doesn't have vector fields for levels.
                // Using payload_json to carry the levels?
                // Or just use the raw normalized fields? No, normalized is flat.
                
                // Decision: Serializable the BookDelta to payload_json.
                norm.payload_json = serde_json::to_string(&d).unwrap_or_default();
            },
            _ => return None, // Ignore others for now
        }

        Some(norm)
    }
}
