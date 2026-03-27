use rust_decimal::Decimal;
use std::collections::BTreeMap;
use log::{warn, info, error};
use serde::Serialize;
use serde::Deserialize;
use std::str::FromStr;
use std::time::{Instant, Duration};
use std::collections::VecDeque;

#[derive(Debug, Clone, PartialEq, Serialize)]
pub enum OrderBookStatus {
    WaitingForSnapshot,
    InSync,
    GapDetected,
    Resyncing,
    Desynced,
    Stabilizing,
}

#[derive(Debug, Clone, Serialize)]
pub struct OrderBookSnapshot {
    pub symbol: String,
    pub best_bid: Decimal,
    pub best_ask: Decimal,
    pub last_update_id: i64,
    pub in_sync: bool,
}

// Internal structs for Binance API response
#[derive(Debug, Deserialize)]
struct BinanceSnapshot {
    #[serde(rename = "lastUpdateId")]
    last_update_id: i64,
    bids: Vec<[String; 2]>,
    asks: Vec<[String; 2]>,
}

pub struct OrderBook {
    pub symbol: String,
    pub bids: BTreeMap<Decimal, Decimal>,
    pub asks: BTreeMap<Decimal, Decimal>,
    pub last_update_id: i64,
    pub status: OrderBookStatus,
    client: reqwest::Client,
    
    // Resync & Backoff State
    pub last_resync_attempt: Option<Instant>,
    pub last_resync_failure: Option<Instant>,
    pub last_resync_success_ts: Option<Instant>,
    pub last_insync_ts: Option<Instant>,
    pub consecutive_failures: u32,
    pub resync_backoff: Duration,
    
    // Stabilizing State
    pub stabilizing_start_ts: Option<Instant>,
    pub stabilizing_updates_count: u64,

    // Rate Limiting (Secondary Guard)
    pub resync_attempts_window: VecDeque<Instant>,
    pub max_attempts_per_minute: usize,
    pub last_rate_limit_log_ts: Option<Instant>,
}

// Hardcoded Governance Constants
const STABILIZING_MS: u64 = 1000;
const STABILIZING_MS_HARDCAP: u64 = 5000;
const STABILIZING_MIN_UPDATES: u64 = 10;
const POST_RESYNC_FLAP_WINDOW_MS: u64 = 2000;
const IN_SYNC_CONFIRM_MS: u64 = 1000;
const BACKOFF_BASE_MS: u64 = 250;
const BACKOFF_CAP_MS: u64 = 30000;
const JITTER_PCT: f64 = 0.15;

impl OrderBook {
    pub fn new(symbol: String) -> Self {
        Self {
            symbol,
            bids: BTreeMap::new(),
            asks: BTreeMap::new(),
            last_update_id: 0,
            status: OrderBookStatus::WaitingForSnapshot,
            client: reqwest::Client::new(),
            
            last_resync_attempt: None,
            last_resync_failure: None,
            last_resync_success_ts: None,
            last_insync_ts: None,
            consecutive_failures: 0,
            resync_backoff: Duration::from_millis(BACKOFF_BASE_MS),
            
            stabilizing_start_ts: None,
            stabilizing_updates_count: 0,

            resync_attempts_window: VecDeque::new(),
            max_attempts_per_minute: 5,
            last_rate_limit_log_ts: None,
        }
    }

    pub fn apply_snapshot(&mut self, last_update_id: i64, bids: Vec<(Decimal, Decimal)>, asks: Vec<(Decimal, Decimal)>) {
        self.bids.clear();
        self.asks.clear();
        
        for (price, qty) in bids {
            self.bids.insert(price, qty);
        }
        
        for (price, qty) in asks {
            self.asks.insert(price, qty);
        }

        self.last_update_id = last_update_id;
        self.status = OrderBookStatus::Stabilizing;
        let now = Instant::now();
        self.stabilizing_start_ts = Some(now);
        self.stabilizing_updates_count = 0;
        self.last_resync_success_ts = Some(now);

        info!(r#"{{"event": "orderbook_stabilizing_start", "symbol": "{}", "last_update_id": {}, "consecutive_failures": {}}}"#, 
            self.symbol, last_update_id, self.consecutive_failures);
    }

    pub fn trigger_resync(&mut self) {
        if self.status == OrderBookStatus::InSync || self.status == OrderBookStatus::Stabilizing {
             warn!("Gap detected for {} in state {:?}. Marking Desynced.", self.symbol, self.status);
             self.status = OrderBookStatus::Desynced;
             // STRICT: Clear book immediately to prevent stale liquidity leakage
             self.bids.clear();
             self.asks.clear();
        }
        // If already Desynced, Resyncing, or Waiting, we let the process continue.
    }

    pub async fn resync(&mut self) -> Result<(), String> {
        // 1. Single Flight Check
        if self.status == OrderBookStatus::Resyncing {
             return Ok(());
        }

        // 2. Backoff Check
        if let Some(last) = self.last_resync_attempt {
             let delay = self.calculate_backoff_delay();
             if last.elapsed() < delay {
                 // Silent return or debug log to avoid spam
                 return Ok(());
             }
        }

        // 3. Rate Limit Check
        let now = Instant::now();
        while let Some(t) = self.resync_attempts_window.front() {
             if t.elapsed() > Duration::from_secs(60) {
                 self.resync_attempts_window.pop_front();
             } else {
                 break;
             }
        }
        
        if self.resync_attempts_window.len() >= self.max_attempts_per_minute {
             let log_cooldown = Duration::from_secs(5);
             let should_log = match self.last_rate_limit_log_ts {
                 Some(t) => t.elapsed() >= log_cooldown,
                 None => true,
             };

             if should_log {
                 warn!("Resync rate limit reached for {}. Skipping attempt.", self.symbol);
                 info!(r#"{{"event": "orderbook_resync_skipped_rate_limit", "symbol": "{}"}}"#, self.symbol);
                 self.last_rate_limit_log_ts = Some(now);
             }
             return Ok(());
        }

        info!("Starting OrderBook resync for {}", self.symbol);
        info!(
            r#"{{"event": "orderbook_resync_started", "symbol": "{}", "consecutive_failures": {}, "orderbook_state": "{:?}"}}"#,
            self.symbol, self.consecutive_failures, self.status
        );

        self.status = OrderBookStatus::Resyncing;
        self.last_resync_attempt = Some(now);
        self.resync_attempts_window.push_back(now);

        let url = format!("https://fapi.binance.com/fapi/v1/depth?symbol={}&limit=1000", self.symbol.to_uppercase());
        
        match self.client.get(&url).send().await {
            Ok(resp) => {
                if resp.status().is_success() {
                     match resp.json::<BinanceSnapshot>().await {
                         Ok(snap) => {
                             let mut bids = Vec::new();
                             for b in snap.bids {
                                 if let (Ok(p), Ok(q)) = (Decimal::from_str(&b[0]), Decimal::from_str(&b[1])) {
                                     bids.push((p, q));
                                 }
                             }
                             let mut asks = Vec::new();
                             for a in snap.asks {
                                 if let (Ok(p), Ok(q)) = (Decimal::from_str(&a[0]), Decimal::from_str(&a[1])) {
                                     asks.push((p, q));
                                 }
                             }
                             
                             self.apply_snapshot(snap.last_update_id, bids, asks);
                             
                             // Reset failure bit on success (but failures reset only after confirm_ms in InSync)
                             self.last_resync_failure = None;

                             info!(
                                r#"{{"event": "orderbook_resync_completed", "symbol": "{}", "last_update_id": {}, "consecutive_failures": {}}}"#,
                                self.symbol, snap.last_update_id, self.consecutive_failures
                            );
                             Ok(())
                         },
                         Err(e) => {
                             let msg = format!("Failed to parse snapshot: {}", e);
                             error!("{}", msg);
                             self.handle_resync_failure();
                             Err(msg)
                         }
                     }
                } else {
                    let status_code = resp.status();
                    let msg = format!("Snapshot request failed: {}", status_code);
                    error!("{}", msg);
                    
                    if status_code == reqwest::StatusCode::TOO_MANY_REQUESTS {
                        self.handle_429();
                    } else {
                        self.handle_resync_failure();
                    }
                    Err(msg)
                }
            },
            Err(e) => {
                let msg = format!("Snapshot network error: {}", e);
                error!("{}", msg);
                self.handle_resync_failure();
                Err(msg)
            }
        }
    }

    fn handle_resync_failure(&mut self) {
        self.status = OrderBookStatus::Desynced;
        self.bids.clear();
        self.asks.clear();
        self.last_resync_failure = Some(Instant::now());
        self.consecutive_failures += 1;
        let next_delay = self.calculate_backoff_delay();
        
        warn!("OrderBook resync failed for {}. consecutive_failures={}. Next delay: {}ms", 
            self.symbol, self.consecutive_failures, next_delay.as_millis());

        info!(r#"{{"event": "orderbook_resync_failed", "symbol": "{}", "consecutive_failures": {}, "next_resync_delay_ms": {}, "orderbook_state": "{:?}"}}"#, 
            self.symbol, self.consecutive_failures, next_delay.as_millis(), self.status);
    }

    fn handle_429(&mut self) {
        self.status = OrderBookStatus::Desynced;
        self.bids.clear();
        self.asks.clear();
        self.last_resync_failure = Some(Instant::now());
        
        // 429 is a special failure, jump ahead in backoff if low
        if self.consecutive_failures < 3 {
             self.consecutive_failures = 3;
        } else {
             self.consecutive_failures += 1;
        }

        let next_delay = self.calculate_backoff_delay();
        
        warn!("OrderBook 429 Rate Limited for {}. Consecutive failures: {}. Backing off for {}ms", 
            self.symbol, self.consecutive_failures, next_delay.as_millis());

        info!(r#"{{"event": "orderbook_resync_rate_limited", "symbol": "{}", "consecutive_failures": {}, "next_resync_delay_ms": {}}}"#, 
            self.symbol, self.consecutive_failures, next_delay.as_millis());
    }

    pub fn calculate_backoff_delay(&self) -> Duration {
        if self.consecutive_failures == 0 {
            return Duration::from_millis(BACKOFF_BASE_MS);
        }

        // Exponential part: base * 2^failures
        let power = 2u32.saturating_pow(self.consecutive_failures.min(10)); // cap exponent to avoid overflow
        let base_delay = BACKOFF_BASE_MS * power as u64;
        
        // Jitter part
        let jitter_range = (base_delay as f64 * JITTER_PCT) as u64;
        let jitter = if jitter_range > 0 {
             let mut rng = rand::thread_rng();
             rand::Rng::gen_range(&mut rng, 0..jitter_range)
        } else {
             0
        };

        let total_delay = std::cmp::min(base_delay + jitter, BACKOFF_CAP_MS);
        Duration::from_millis(total_delay)
    }

    pub fn apply_delta(&mut self, first_update_id: i64, final_update_id: i64, prev_update_id: i64, bids: Vec<(Decimal, Decimal)>, asks: Vec<(Decimal, Decimal)>) {
        // 1. Ignore Stale/Old Events (Already applied via Snapshot)
        if final_update_id <= self.last_update_id {
            return; 
        }

        if self.status == OrderBookStatus::WaitingForSnapshot || self.status == OrderBookStatus::Resyncing {
            // Buffer? Or just ignore until we have snapshot?
            // Usually we need to buffer if we want perfect sync, but simple resync just drops until snapshot is fresh enough.
            // (Stale check handled above)
            
            // If we are InSync, check continuity. 
            // If we just Resynced, we might be `InSync` now (set in apply_snapshot).
            if self.status != OrderBookStatus::InSync {
                 return;
            }
        }

        if self.status == OrderBookStatus::GapDetected || self.status == OrderBookStatus::Desynced {
            return; // Wait for resync trigger
        }

        // Check for continuity
        // "The first processed event should have U <= lastUpdateId+1 AND u >= lastUpdateId+1"
        // OR if following a stream, prev_update_id == last_update_id.
        if self.status == OrderBookStatus::GapDetected || self.status == OrderBookStatus::Desynced {
            return; // Wait for resync trigger
        }

        // Check for continuity
        if prev_update_id != self.last_update_id {
            // RELAXED: If we were at ID 0 (just seeded from BBO/Snapshot-0), just accept the gap 
            // and lock onto the current sequence. This is essential for replay stability.
            if self.last_update_id == 0 && prev_update_id > 0 {
                info!("OrderBook re-baselining sequence for {} from 0 to {}", self.symbol, prev_update_id);
                // No need to clear, just continue and it will set last_update_id to final_update_id at end.
            } else {
                // Check strict overlap for recovery
                if first_update_id <= self.last_update_id + 1 && final_update_id > self.last_update_id {
                     // Valid overlap, proceed.
                } else {
                     warn!("Gap detected in {}: Current ID {}, Event Prev ID {}. Triggering resync.", self.symbol, self.last_update_id, prev_update_id);
                     
                     // Flap Detection: if we gap shortly after a successful resync, it counts as a failure for backoff purposes.
                     if let Some(resync_ts) = self.last_resync_success_ts {
                         if resync_ts.elapsed() < Duration::from_millis(POST_RESYNC_FLAP_WINDOW_MS) {
                             self.consecutive_failures += 1;
                             warn!("OrderBook FLAP detected for {}. incrementing consecutive_failures to {}", self.symbol, self.consecutive_failures);
                         }
                     }
    
                     info!(
                        r#"{{"event": "orderbook_gap_detected", "symbol": "{}", "current_id": {}, "prev_id": {}, "consecutive_failures": {}, "orderbook_state": "{:?}"}}"#,
                        self.symbol, self.last_update_id, prev_update_id, self.consecutive_failures, self.status
                    );
                     
                     self.trigger_resync();
                     return;
                }
            }
        }

        // ─── Data Application ───
        for (price, qty) in bids {
            if qty.is_zero() {
                self.bids.remove(&price);
            } else {
                self.bids.insert(price, qty);
            }
        }

        for (price, qty) in asks {
            if qty.is_zero() {
                self.asks.remove(&price);
            } else {
                self.asks.insert(price, qty);
            }
        }

        self.last_update_id = final_update_id;

        // ─── Post-Update State Transition ───
        match self.status {
            OrderBookStatus::Stabilizing => {
                self.stabilizing_updates_count += 1;
                let stable_ms = self.stabilizing_start_ts.map(|t| t.elapsed().as_millis() as u64).unwrap_or(0);
                
                let ready = (stable_ms >= STABILIZING_MS && self.stabilizing_updates_count >= STABILIZING_MIN_UPDATES)
                            || (stable_ms >= STABILIZING_MS_HARDCAP);

                if ready {
                    self.status = OrderBookStatus::InSync;
                    let now = Instant::now();
                    self.last_insync_ts = Some(now);
                    info!(r#"{{"event": "orderbook_stabilizing_complete", "symbol": "{}", "stabilizing_ms": {}, "updates": {}}}"#, 
                        self.symbol, stable_ms, self.stabilizing_updates_count);
                }
            }
            OrderBookStatus::InSync => {
                // Check if we can reset consecutive failures
                if self.consecutive_failures > 0 {
                    if let Some(insync_ts) = self.last_insync_ts {
                        if insync_ts.elapsed() >= Duration::from_millis(IN_SYNC_CONFIRM_MS) {
                            info!("OrderBook {} stable. Resetting consecutive_failures from {}", self.symbol, self.consecutive_failures);
                            self.consecutive_failures = 0;
                            self.resync_backoff = Duration::from_millis(BACKOFF_BASE_MS);
                        }
                    }
                }
            }
            _ => {}
        }
    }

    pub fn get_snapshot(&self) -> Option<OrderBookSnapshot> {
        let best_bid = self.bids.keys().next_back().cloned().unwrap_or(Decimal::ZERO); 
        let best_ask = self.asks.keys().next().cloned().unwrap_or(Decimal::ZERO);

        Some(OrderBookSnapshot {
            symbol: self.symbol.clone(),
            best_bid,
            best_ask,
            last_update_id: self.last_update_id,
            in_sync: self.status == OrderBookStatus::InSync,
        })
    }

    /// Returns (best_bid, best_ask) as f64 for commission policy decisions.
    /// Falls back to (0.0, 0.0) if the book is empty.
    pub fn best_bid_ask(&self) -> (f64, f64) {
        use rust_decimal::prelude::ToPrimitive;
        let bid = self.bids.keys().next_back().and_then(|d| d.to_f64()).unwrap_or(0.0);
        let ask = self.asks.keys().next().and_then(|d| d.to_f64()).unwrap_or(0.0);
        (bid, ask)
    }

    /// Returns top N bid levels as [(price, qty), ...] sorted best→worst (highest price first).
    pub fn top_bids(&self, n: usize) -> Vec<(f64, f64)> {
        use rust_decimal::prelude::ToPrimitive;
        self.bids.iter().rev().take(n)
            .map(|(p, q)| (p.to_f64().unwrap_or(0.0), q.to_f64().unwrap_or(0.0)))
            .collect()
    }

    /// Returns top N ask levels as [(price, qty), ...] sorted best→worst (lowest price first).
    pub fn top_asks(&self, n: usize) -> Vec<(f64, f64)> {
        use rust_decimal::prelude::ToPrimitive;
        self.asks.iter().take(n)
            .map(|(p, q)| (p.to_f64().unwrap_or(0.0), q.to_f64().unwrap_or(0.0)))
            .collect()
    }
    
    pub fn is_sync(&self) -> bool {
        self.status == OrderBookStatus::InSync
    }
    
    pub fn mark_desynced(&mut self) {
        self.status = OrderBookStatus::Desynced;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rust_decimal_macros::dec;
    use std::thread::sleep;

    #[test]
    fn test_gap_sets_state_desync_and_clears_book() {
        let mut ob = OrderBook::new("BTCUSDT".to_string());
        ob.apply_snapshot(100, vec![(dec!(50000), dec!(1))], vec![(dec!(50001), dec!(1))]);
        assert_eq!(ob.status, OrderBookStatus::Stabilizing);

        // Advance to InSync manually for this test setup
        ob.status = OrderBookStatus::InSync;
        ob.last_insync_ts = Some(Instant::now());

        // Gap: current 100, prev 102
        ob.apply_delta(103, 103, 102, vec![], vec![]);
        assert_eq!(ob.status, OrderBookStatus::Desynced);
        assert!(ob.bids.is_empty());
        assert!(ob.asks.is_empty());
    }

    #[test]
    fn test_backoff_increases_and_caps() {
        let mut ob = OrderBook::new("BTCUSDT".to_string());
        
        // 0 failures
        let d0 = ob.calculate_backoff_delay();
        assert!(d0.as_millis() >= 250);

        // 1 failure
        ob.consecutive_failures = 1;
        let d1 = ob.calculate_backoff_delay();
        assert!(d1.as_millis() >= 500);

        // 5 failures
        ob.consecutive_failures = 5;
        let d5 = ob.calculate_backoff_delay();
        // 250 * 2^5 = 250 * 32 = 8000
        assert!(d5.as_millis() >= 8000);

        // Many failures (cap)
        ob.consecutive_failures = 20;
        let d_cap = ob.calculate_backoff_delay();
        assert_eq!(d_cap.as_millis(), 30000);
    }

    #[test]
    fn test_stabilizing_blocks_ready_until_threshold() {
        let mut ob = OrderBook::new("BTCUSDT".to_string());
        ob.apply_snapshot(100, vec![(dec!(50000), dec!(1))], vec![(dec!(50001), dec!(1))]);
        assert_eq!(ob.status, OrderBookStatus::Stabilizing);

        // 5 updates, but less than 1000ms
        for i in 1..=5 {
            ob.apply_delta(100 + i, 100 + i, 100 + i - 1, vec![], vec![]);
        }
        assert_eq!(ob.status, OrderBookStatus::Stabilizing);

        // Wait to pass STABILIZING_MS
        sleep(Duration::from_millis(1100));

        // Still not ready (need updates)
        ob.apply_delta(106, 106, 105, vec![], vec![]);
        assert_eq!(ob.status, OrderBookStatus::Stabilizing);

        // Finish updates
        for i in 7..=10 {
            ob.apply_delta(100 + i, 100 + i, 100 + i - 1, vec![], vec![]);
        }
        assert_eq!(ob.status, OrderBookStatus::InSync);
    }

    #[test]
    fn test_stabilizing_hardcap_prevents_eternal_hold() {
        let mut ob = OrderBook::new("BTCUSDT".to_string());
        ob.apply_snapshot(100, vec![(dec!(50000), dec!(1))], vec![(dec!(50001), dec!(1))]);
        
        // Wait 5.1s (hardcap)
        sleep(Duration::from_millis(5100));

        // 1 update should trigger transition regardless of min_updates
        ob.apply_delta(101, 101, 100, vec![], vec![]);
        assert_eq!(ob.status, OrderBookStatus::InSync);
    }

    #[test]
    fn test_flap_detection_increments_failures() {
        let mut ob = OrderBook::new("BTCUSDT".to_string());
        ob.consecutive_failures = 0;

        // Resync success
        ob.apply_snapshot(100, vec![(dec!(50000), dec!(1))], vec![(dec!(50001), dec!(1))]);
        
        // Immediate gap (within 2s flap window)
        ob.apply_delta(102, 102, 101, vec![], vec![]);
        assert_eq!(ob.consecutive_failures, 1);
        assert_eq!(ob.status, OrderBookStatus::Desynced);
    }

    #[test]
    fn test_gap_during_stabilizing_resets() {
        let mut ob = OrderBook::new("BTCUSDT".to_string());
        ob.apply_snapshot(100, vec![(dec!(50000), dec!(1))], vec![(dec!(50001), dec!(1))]);
        assert_eq!(ob.status, OrderBookStatus::Stabilizing);

        // Gap during stabilizing
        ob.apply_delta(103, 103, 101, vec![], vec![]);
        assert_eq!(ob.status, OrderBookStatus::Desynced);
        assert!(ob.bids.is_empty());
    }
}
