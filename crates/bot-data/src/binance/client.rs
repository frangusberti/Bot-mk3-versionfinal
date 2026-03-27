use crate::binance::model::BinanceEvent;
use futures_util::{SinkExt, StreamExt};
use log::{error, info, warn};
use std::time::Duration;
use tokio::sync::mpsc;
use tokio::time::sleep;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};
use url::Url;

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

pub struct BinanceClient {
    streams: Vec<String>,
    tx: mpsc::Sender<BinanceEvent>,
    stall_threshold_sec: u64,
    last_message_ts: Arc<AtomicU64>,
}

impl BinanceClient {
    pub fn new(
        streams: Vec<String>,
        tx: mpsc::Sender<BinanceEvent>,
        stall_threshold_sec: u64,
    ) -> Self {
        Self {
            streams,
            tx,
            stall_threshold_sec,
            last_message_ts: Arc::new(AtomicU64::new(0)),
        }
    }

    pub async fn connect_and_run(&self) {
        // Network Firewall for Offline Mode
        if std::env::var("BOT_OFFLINE_MODE")
            .ok()
            .map(|v| v == "1" || v.to_lowercase() == "true")
            .unwrap_or(false)
        {
            error!(
                "OFFLINE MODE ENABLED: Blocking outbound connection to {}",
                self.streams.join(",")
            );
            return;
        }

        let all_streams = self.streams.clone();
        let stream_query = all_streams.join("/");
        let url = format!("wss://fstream.binance.com/ws/{}", stream_query);
        let url = Url::parse(&url).expect("Invalid WebSocket URL");

        loop {
            info!("Connecting to {}", url);
            match connect_async(url.clone()).await {
                Ok((ws_stream, _)) => {
                    info!("Connected to Binance Futures WS");
                    let (mut write, mut read) = ws_stream.split();

                    // Reset Watchdog
                    let now = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap()
                        .as_secs();
                    self.last_message_ts.store(now, Ordering::Relaxed);

                    // Watchdog Loop
                    let last_ts_clone = self.last_message_ts.clone();
                    let threshold = self.stall_threshold_sec;

                    let mut ping_interval = tokio::time::interval(Duration::from_secs(15));

                    // Use select! to handle reading, heartbeat and watchdog simultaneously
                    loop {
                        tokio::select! {
                            msg_opt = read.next() => {
                                match msg_opt {
                                    Some(Ok(Message::Text(text))) => {
                                        // Update Watchdog
                                        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
                                        self.last_message_ts.store(now, Ordering::Relaxed);

                                        match serde_json::from_str::<BinanceEvent>(&text) {
                                            Ok(event) => {
                                                if let Err(e) = self.tx.send(event).await {
                                                    error!("Failed to send event to channel: {}", e);
                                                    break;
                                                }
                                            }
                                            Err(e) => warn!("Failed to parse message: {}. Text: {}", e, text),
                                        }
                                    }
                                    Some(Ok(Message::Ping(payload))) => {
                                         // Update Watchdog on Ping too
                                        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
                                        self.last_message_ts.store(now, Ordering::Relaxed);

                                        if let Err(e) = write.send(Message::Pong(payload)).await {
                                            error!("Failed to respond Pong: {}", e);
                                            break;
                                        }
                                    }
                                    Some(Ok(Message::Pong(_))) => {
                                        // Update Watchdog on Pong as explicit heartbeat signal
                                        let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
                                        self.last_message_ts.store(now, Ordering::Relaxed);
                                    }
                                    Some(Ok(Message::Close(_))) => {
                                        warn!("Server closed connection");
                                        break;
                                    }
                                    Some(Err(e)) => {
                                        error!("WebSocket error: {}", e);
                                        break;
                                    }
                                    None => {
                                        warn!("WebSocket stream ended");
                                        break;
                                    }
                                    _ => {}
                                }
                            }
                            _ = ping_interval.tick() => {
                                // Active heartbeat to keep connection alive and detect stale links earlier
                                if let Err(e) = write.send(Message::Ping(Vec::new())).await {
                                    error!("Failed to send Ping heartbeat: {}", e);
                                    break;
                                }
                            }
                            _ = sleep(Duration::from_secs(1)) => {
                                // Check Watchdog
                                let now = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs();
                                let last = last_ts_clone.load(Ordering::Relaxed);
                                if now.saturating_sub(last) > threshold {
                                    error!("ws_stall_detected: No message in {}s (Threshold: {}s). Reconnecting...", now.saturating_sub(last), threshold);
                                    break;
                                }
                            }
                        }
                    }
                }
                Err(e) => {
                    error!("Failed to connect: {}", e);
                }
            }

            warn!("ws_reconnect_triggered: Reconnecting in 5 seconds...");
            sleep(Duration::from_secs(5)).await;
        }
    }
}
