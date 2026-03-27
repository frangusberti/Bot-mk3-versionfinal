use std::collections::{HashMap, VecDeque};
use serde::Serialize;

// ════════════════════════════════════════════════════════════════════════
//  M7: Health Monitor — Per-Symbol WS, Book, Lag, Error Tracking
// ════════════════════════════════════════════════════════════════════════

const LAG_WINDOW_SIZE: usize = 1000;

// ────────────────────────────────────────────────────────────
//  Per-Symbol Health
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, Serialize)]
pub struct SymbolHealth {
    pub symbol: String,
    pub ws_connected: bool,
    pub book_synced: bool,
    pub book_resets: u64,
    pub spread_bps: f64,
}

#[allow(dead_code)]
impl SymbolHealth {
    pub fn new(symbol: &str) -> Self {
        Self {
            symbol: symbol.to_string(),
            ws_connected: false,
            book_synced: false,
            book_resets: 0,
            spread_bps: 0.0,
        }
    }
}

// ────────────────────────────────────────────────────────────
//  Health Monitor
// ────────────────────────────────────────────────────────────

pub struct HealthMonitor {
    symbols: HashMap<String, SymbolHealth>,
    lag_samples: VecDeque<f64>,
    pub errors_total: u64,
    pub last_error: String,
}

#[allow(dead_code)]
impl HealthMonitor {
    pub fn new() -> Self {
        Self {
            symbols: HashMap::new(),
            lag_samples: VecDeque::with_capacity(LAG_WINDOW_SIZE),
            errors_total: 0,
            last_error: String::new(),
        }
    }

    /// Register a symbol for tracking
    pub fn register_symbol(&mut self, symbol: &str) {
        self.symbols.entry(symbol.to_string())
            .or_insert_with(|| SymbolHealth::new(symbol));
    }

    /// Update WS connection status for a symbol
    pub fn set_ws_connected(&mut self, symbol: &str, connected: bool) {
        if let Some(h) = self.symbols.get_mut(symbol) {
            h.ws_connected = connected;
        }
    }

    /// Update orderbook sync status
    pub fn set_book_synced(&mut self, symbol: &str, synced: bool) {
        if let Some(h) = self.symbols.get_mut(symbol) {
            h.book_synced = synced;
        }
    }

    /// Increment book reset counter
    pub fn record_book_reset(&mut self, symbol: &str) {
        if let Some(h) = self.symbols.get_mut(symbol) {
            h.book_resets += 1;
        }
    }

    /// Update current spread
    pub fn update_spread(&mut self, symbol: &str, spread_bps: f64) {
        if let Some(h) = self.symbols.get_mut(symbol) {
            h.spread_bps = spread_bps;
        }
    }

    /// Record a latency sample (ms)
    pub fn record_lag(&mut self, lag_ms: f64) {
        if self.lag_samples.len() >= LAG_WINDOW_SIZE {
            self.lag_samples.pop_front();
        }
        self.lag_samples.push_back(lag_ms);
    }

    /// Record an error
    pub fn record_error(&mut self, error: &str) {
        self.errors_total += 1;
        self.last_error = error.to_string();
    }

    /// Compute p50 (median) of lag samples
    pub fn lag_p50(&self) -> f64 {
        self.percentile(50.0)
    }

    /// Compute p99 of lag samples
    pub fn lag_p99(&self) -> f64 {
        self.percentile(99.0)
    }

    /// Get all symbol health snapshots
    pub fn symbol_health(&self) -> Vec<SymbolHealth> {
        self.symbols.values().cloned().collect()
    }

    fn percentile(&self, pct: f64) -> f64 {
        if self.lag_samples.is_empty() {
            return 0.0;
        }
        let mut sorted: Vec<f64> = self.lag_samples.iter().copied().collect();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
        let idx = ((pct / 100.0) * (sorted.len() as f64 - 1.0)).round() as usize;
        let idx = idx.min(sorted.len() - 1);
        sorted[idx]
    }
}

impl Default for HealthMonitor {
    fn default() -> Self {
        Self::new()
    }
}

// ════════════════════════════════════════════════════════════════════════
//  Tests
// ════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_health_monitor_basics() {
        let mut hm = HealthMonitor::new();
        hm.register_symbol("BTCUSDT");
        hm.set_ws_connected("BTCUSDT", true);
        hm.set_book_synced("BTCUSDT", true);
        hm.update_spread("BTCUSDT", 1.5);
        hm.record_book_reset("BTCUSDT");

        let symbols = hm.symbol_health();
        assert_eq!(symbols.len(), 1);
        assert!(symbols[0].ws_connected);
        assert!(symbols[0].book_synced);
        assert_eq!(symbols[0].book_resets, 1);
        assert!((symbols[0].spread_bps - 1.5).abs() < 0.01);
    }

    #[test]
    fn test_lag_percentiles() {
        let mut hm = HealthMonitor::new();
        for i in 1..=100 {
            hm.record_lag(i as f64);
        }
        assert!((hm.lag_p50() - 50.0).abs() < 1.5);
        assert!((hm.lag_p99() - 99.0).abs() < 1.5);
    }

    #[test]
    fn test_error_tracking() {
        let mut hm = HealthMonitor::new();
        hm.record_error("Connection timeout");
        hm.record_error("Parse error");
        assert_eq!(hm.errors_total, 2);
        assert_eq!(hm.last_error, "Parse error");
    }
}
