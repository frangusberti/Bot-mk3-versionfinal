use tokio::sync::mpsc;
use log::{info, error};
use bot_data::binance::client::BinanceClient;
use bot_data::binance::model::BinanceEvent;
use bot_data::orderbook::engine::OrderBook;
use bot_data::storage::writer::{ParquetWriter, MarketEvent};
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use rust_decimal::prelude::ToPrimitive;

use bot_data::health::{HealthMonitor, ComponentStatus};
use std::sync::Arc;

use tokio::sync::broadcast;
use bot_core::proto::MarketSnapshot;

#[allow(clippy::too_many_arguments)]
pub async fn run_recorder(
    run_id: String,
    symbol: String,
    data_dir: PathBuf,
    mut stop_rx: mpsc::Receiver<()>,
    monitor: Arc<HealthMonitor>,
    snapshot_tx: broadcast::Sender<MarketSnapshot>,
    config: crate::config::RecorderConfig,
    stall_threshold_sec: u64,
) {
    info!("Starting recorder loop for run_id: {}", run_id);
    monitor.update_component("Recorder", ComponentStatus::Ok, "Running");

    // 1. Setup Channels
    let (tx, mut rx) = mpsc::channel(1000);

use bot_data::binance::poller::OpenInterestPoller;

// ...

    // 2. Setup Components
    let streams = vec![
        format!("{}@aggTrade", symbol.to_lowercase()),
        format!("{}@depth@100ms", symbol.to_lowercase()),
        format!("{}@bookTicker", symbol.to_lowercase()),
    ];
    
    let client = BinanceClient::new(streams, tx.clone(), stall_threshold_sec); // Clone tx for poller
    let poller = OpenInterestPoller::new(symbol.clone(), tx);
    let mut orderbook = OrderBook::new(symbol.clone());
    
    // Setup Rotation Helpers
    let mut part_count = 0;
    let max_file_size = 512 * 1024 * 1024; // 512 MB

    let get_path = |part: usize| -> PathBuf {
        let now = chrono::Utc::now();
        let date = now.format("%Y-%m-%d").to_string();
        let hour = now.format("%H").to_string();
        data_dir.join("runs").join(&run_id).join("events")
            .join(format!("{}_{}_{}_part-{:04}.parquet", symbol, date, hour, part))
    };

    let mut current_path = get_path(part_count);
    if let Some(parent) = current_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut writer = ParquetWriter::new(current_path.clone());

    // 3. Spawn Client & Poller
    let client_handle = tokio::spawn(async move {
        client.connect_and_run().await;
    });
    
    let poller_handle = tokio::spawn(async move {
        poller.run().await;
    });

    // 4. Event Loop
    let mut last_health_update = SystemTime::now();
    let mut last_snapshot_time = SystemTime::now(); // For max 10Hz snapshots
    let mut event_count = 0;
    let mut last_rate = 0.0;
    let mut total_events: u64 = 0; // For sampling

    loop {
        tokio::select! {
             _ = stop_rx.recv() => {
                info!("Stop signal received. Shutting down recorder.");
                monitor.update_component("Recorder", ComponentStatus::Ok, "Stopping");
                poller_handle.abort(); // Stop poller
                client_handle.abort(); // Stop client
                break;
            }
            Some(event) = rx.recv() => {
                event_count += 1;
                total_events += 1;
                let local_ts = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_millis() as i64;

                // Update health every 1s
                if last_health_update.elapsed().unwrap_or_default().as_secs() >= 1 {
                    monitor.update_metric("Recorder", "events_per_sec", &event_count.to_string());
                    last_rate = event_count as f64;
                    event_count = 0;
                    last_health_update = SystemTime::now();
                }

                // Apply to OrderBook
                if let BinanceEvent::DepthUpdate(u) = &event {
                     let bids: Vec<(rust_decimal::Decimal, rust_decimal::Decimal)> = u.bids.iter().map(|(p, q)| (*p, *q)).collect();
                     let asks: Vec<(rust_decimal::Decimal, rust_decimal::Decimal)> = u.asks.iter().map(|(p, q)| (*p, *q)).collect();
                     orderbook.apply_delta(u.first_update_id, u.final_update_id, u.prev_update_id, bids, asks);
                }

                // Auto-Resync Check
                {
                    use bot_data::orderbook::engine::OrderBookStatus;
                    // If not InSync and not already Resyncing (though resync() checks that too), try to resync.
                    // The resync logic inside OrderBook handles backoff and rate limits internally.
                    if orderbook.status == OrderBookStatus::Desynced || 
                       orderbook.status == OrderBookStatus::WaitingForSnapshot ||
                       orderbook.status == OrderBookStatus::GapDetected 
                    {
                        // Fire and forget (it awaits, but handles its own state)
                        if let Err(e) = orderbook.resync().await {
                             error!("Resync request error: {}", e);
                        }
                    }
                }
                
                // Write to Parquet
                let storage_event = map_to_storage_event(event, local_ts, symbol.clone(), &config, total_events);
                if let Err(e) = writer.write(storage_event) {
                    error!("Failed to write event: {}", e);
                    monitor.update_component("Recorder", ComponentStatus::Error, &format!("Write error: {}", e));
                }

                // Rotation Check
                if writer.current_file_size() >= max_file_size {
                    info!("Rotating file due to size limit: {} bytes", writer.current_file_size());
                    if let Err(e) = writer.close() {
                        error!("Failed to close writer during rotation: {}", e);
                    }
                    part_count += 1;
                    current_path = get_path(part_count);
                    if let Some(parent) = current_path.parent() {
                        let _ = std::fs::create_dir_all(parent);
                    }
                    writer = ParquetWriter::new(current_path.clone());
                }
                
                if last_snapshot_time.elapsed().unwrap_or_default().as_millis() >= 100 {
                     let best_bid = orderbook.bids.keys().next_back().cloned().unwrap_or_default().to_f64().unwrap_or_default();
                     let best_ask = orderbook.asks.keys().next().cloned().unwrap_or_default().to_f64().unwrap_or_default();
                     let mid_price = if best_bid > 0.0 && best_ask > 0.0 { (best_bid + best_ask) / 2.0 } else { 0.0 };
                     let spread = if mid_price > 0.0 { (best_ask - best_bid) / mid_price * 100.0 } else { 0.0 };
                     
                     let file_size = writer.current_file_size();

                     let snap = MarketSnapshot {
                         symbol: symbol.clone(),
                         best_bid,
                         best_ask,
                         spread_percent: spread,
                         mid_price,
                         last_update_id: orderbook.last_update_id,
                         in_sync: true, 
                         events_per_sec: last_rate, 
                         lag_p99_ms: 0.0,
                         sequence_gaps: 0,
                         file_size_bytes: file_size as i64,
                     };
                     
                     let _ = snapshot_tx.send(snap);
                     last_snapshot_time = SystemTime::now();
                }
            }
        }
    }
    
    // Cleanup
    if let Err(e) = writer.close() {
        error!("Failed to close writer: {}", e);
    }
    monitor.update_component("Recorder", ComponentStatus::Ok, "Stopped");
    info!("Recorder loop finished for run_id: {}", run_id);
}

fn should_store_payload(mode: &str, counter: u64) -> bool {
    match mode {
        "full" => true,
        "sample" => counter.is_multiple_of(1000), // Hardcoded 1Hz approx if 1000Hz, or simply 1/1000
        _ => false, // "none" or unknown
    }
}

fn get_payload_json<T: serde::Serialize>(mode: &str, obj: &T, counter: u64) -> String {
    if should_store_payload(mode, counter) {
        serde_json::to_string(obj).unwrap_or_default()
    } else {
        "".to_string()
    }
}

fn map_to_storage_event(event: BinanceEvent, local_ts: i64, symbol: String, config: &crate::config::RecorderConfig, counter: u64) -> MarketEvent {
    // Helper closure removed in favor of generic function get_payload_json
    
    match event {
        BinanceEvent::AggTrade(t) => MarketEvent {
            local_timestamp: local_ts,
            exchange_timestamp: t.event_time,
            symbol,
            event_type: "aggTrade".to_string(),
            price: Some(t.price.try_into().unwrap_or(0.0)),
            quantity: Some(t.quantity.try_into().unwrap_or(0.0)),
            bid_price: None,
            ask_price: None,
            is_buyer_maker: Some(t.is_buyer_maker),
            payload: get_payload_json(&config.payload.agg_trade, &t, counter),
        },
        BinanceEvent::BookTicker(t) => MarketEvent {
            local_timestamp: local_ts,
            exchange_timestamp: t.event_time, 
            symbol,
            event_type: "bookTicker".to_string(),
            price: None, 
            quantity: None,
            bid_price: Some(t.best_bid_price.try_into().unwrap_or(0.0)),
            ask_price: Some(t.best_ask_price.try_into().unwrap_or(0.0)),
            is_buyer_maker: None,
            payload: get_payload_json(&config.payload.book_ticker, &t, counter),
        },
        BinanceEvent::DepthUpdate(d) => MarketEvent {
            local_timestamp: local_ts,
            exchange_timestamp: d.event_time,
            symbol,
            event_type: "depthUpdate".to_string(),
            price: None,
            quantity: None,
            bid_price: None,
            ask_price: None,
            is_buyer_maker: None,
            payload: get_payload_json(&config.payload.depth, &d, counter),
        },
        BinanceEvent::MarkPriceUpdate(m) => MarketEvent {
            local_timestamp: local_ts,
            exchange_timestamp: m.event_time,
            symbol,
            event_type: "markPrice".to_string(),
            price: Some(m.mark_price.try_into().unwrap_or(0.0)),
            quantity: None,
            bid_price: None,
            ask_price: None,
            is_buyer_maker: None,
            payload: get_payload_json(&config.payload.mark_price, &m, counter),
        },
        BinanceEvent::ForceOrder(f) => MarketEvent {
             local_timestamp: local_ts,
            exchange_timestamp: f.event_time,
            symbol,
            event_type: "forceOrder".to_string(),
            price: Some(f.order.price.try_into().unwrap_or(0.0)),
            quantity: Some(f.order.original_quantity.try_into().unwrap_or(0.0)),
            bid_price: None,
            ask_price: None,
            is_buyer_maker: None,
            payload: serde_json::to_string(&f).unwrap_or_default(),
        },
        BinanceEvent::OpenInterest(oi) => MarketEvent {
            local_timestamp: local_ts,
            exchange_timestamp: oi.time,
            symbol,
            event_type: "openInterest".to_string(),
            price: None,
            quantity: Some(oi.open_interest.parse().unwrap_or(0.0)),
            bid_price: None,
            ask_price: None,
            is_buyer_maker: None,
            payload: serde_json::to_string(&oi).unwrap_or_default(),
        },
        // Handle other variants if any, or default?
        // Enum usually exhaustive.
        // Assuming Trade, Ticker, etc covered.
        // If strict matching is needed, we cover all used.
    }
}
