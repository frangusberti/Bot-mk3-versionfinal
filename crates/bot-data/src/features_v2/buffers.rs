use std::collections::VecDeque;

// ============================================================================
//  RingBuffer — Fixed-size circular buffer for scalar time series
// ============================================================================

/// A fixed-capacity circular buffer of f64 values.
/// Supports push, mean, std, and indexed access (0 = most recent).
#[derive(Debug, Clone)]
pub struct RingBuffer {
    data: VecDeque<f64>,
    capacity: usize,
}

impl RingBuffer {
    pub fn new(capacity: usize) -> Self {
        Self {
            data: VecDeque::with_capacity(capacity),
            capacity,
        }
    }

    pub fn push(&mut self, val: f64) {
        if self.data.len() == self.capacity {
            self.data.pop_back();
        }
        self.data.push_front(val);
    }

    pub fn len(&self) -> usize { self.data.len() }
    pub fn is_empty(&self) -> bool { self.data.is_empty() }
    pub fn is_full(&self) -> bool { self.data.len() == self.capacity }
    pub fn capacity(&self) -> usize { self.capacity }

    /// Get value at offset from most recent. 0 = last pushed.
    pub fn get(&self, offset: usize) -> Option<f64> {
        self.data.get(offset).copied()
    }

    /// Most recent value.
    pub fn front(&self) -> Option<f64> { self.data.front().copied() }

    pub fn mean(&self) -> Option<f64> {
        if self.data.is_empty() { return None; }
        Some(self.data.iter().sum::<f64>() / self.data.len() as f64)
    }

    /// Sample standard deviation (N-1 denominator).
    pub fn std(&self) -> Option<f64> {
        let n = self.data.len();
        if n < 2 { return None; }
        let mean = self.data.iter().sum::<f64>() / n as f64;
        let var = self.data.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (n - 1) as f64;
        Some(var.sqrt())
    }

    /// Standard deviation using all N elements (matching spec: over last N samples).
    pub fn std_n(&self, n: usize) -> Option<f64> {
        if self.data.len() < n { return None; }
        let slice: Vec<f64> = self.data.iter().take(n).copied().collect();
        let mean = slice.iter().sum::<f64>() / n as f64;
        let var = slice.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / (n - 1) as f64;
        Some(var.sqrt())
    }

    pub fn iter(&self) -> impl Iterator<Item = &f64> {
        self.data.iter()
    }
}

// ============================================================================
//  TradeAccumulator — Time-windowed buy/sell volume aggregation
// ============================================================================

#[derive(Debug, Clone)]
struct TimedVolume {
    ts: i64,
    qty: f64,
}

/// Accumulates trade volumes in time windows for taker flow features.
#[derive(Debug, Clone)]
pub struct TradeAccumulator {
    buys: VecDeque<TimedVolume>,
    sells: VecDeque<TimedVolume>,
    trade_count: VecDeque<i64>,  // timestamps of all trades for counting
    max_window_ms: i64,
}

impl TradeAccumulator {
    pub fn new(max_window_ms: i64) -> Self {
        Self {
            buys: VecDeque::new(),
            sells: VecDeque::new(),
            trade_count: VecDeque::new(),
            max_window_ms,
        }
    }

    /// Record a trade. is_taker_buy=true means buy-side aggressor.
    pub fn record_trade(&mut self, ts: i64, qty: f64, is_taker_buy: bool) {
        let tv = TimedVolume { ts, qty };
        if is_taker_buy {
            self.buys.push_back(tv);
        } else {
            self.sells.push_back(tv);
        }
        self.trade_count.push_back(ts);
        self.prune(ts);
    }

    fn prune(&mut self, now: i64) {
        let cutoff = now - self.max_window_ms;
        while self.buys.front().is_some_and(|v| v.ts < cutoff) {
            self.buys.pop_front();
        }
        while self.sells.front().is_some_and(|v| v.ts < cutoff) {
            self.sells.pop_front();
        }
        while self.trade_count.front().is_some_and(|&t| t < cutoff) {
            self.trade_count.pop_front();
        }
    }

    /// Total buy volume in last `window_ms` milliseconds ending at `now`.
    pub fn buy_vol(&self, now: i64, window_ms: i64) -> f64 {
        let cutoff = now - window_ms;
        self.buys.iter().filter(|v| v.ts >= cutoff).map(|v| v.qty).sum()
    }

    /// Total sell volume in last `window_ms` milliseconds ending at `now`.
    pub fn sell_vol(&self, now: i64, window_ms: i64) -> f64 {
        let cutoff = now - window_ms;
        self.sells.iter().filter(|v| v.ts >= cutoff).map(|v| v.qty).sum()
    }

    /// Number of trades in last `window_ms` milliseconds.
    pub fn trade_count(&self, now: i64, window_ms: i64) -> usize {
        let cutoff = now - window_ms;
        self.trade_count.iter().filter(|&&t| t >= cutoff).count()
    }
}

// ============================================================================
//  LiqAccumulator — Liquidation event time windows
// ============================================================================

#[derive(Debug, Clone)]
struct LiqEvent {
    ts: i64,
    qty: f64,
    is_buy: bool,
}

/// Accumulates liquidation events for shock detection features.
#[derive(Debug, Clone)]
pub struct LiqAccumulator {
    events: VecDeque<LiqEvent>,
    max_window_ms: i64,
}

impl LiqAccumulator {
    pub fn new(max_window_ms: i64) -> Self {
        Self {
            events: VecDeque::new(),
            max_window_ms,
        }
    }

    pub fn record(&mut self, ts: i64, qty: f64, is_buy: bool) {
        self.events.push_back(LiqEvent { ts, qty, is_buy });
        let cutoff = ts - self.max_window_ms;
        while self.events.front().is_some_and(|e| e.ts < cutoff) {
            self.events.pop_front();
        }
    }

    pub fn buy_vol(&self, now: i64, window_ms: i64) -> f64 {
        let cutoff = now - window_ms;
        self.events.iter().filter(|e| e.ts >= cutoff && e.is_buy).map(|e| e.qty).sum()
    }

    pub fn sell_vol(&self, now: i64, window_ms: i64) -> f64 {
        let cutoff = now - window_ms;
        self.events.iter().filter(|e| e.ts >= cutoff && !e.is_buy).map(|e| e.qty).sum()
    }

    pub fn count(&self, now: i64, window_ms: i64) -> usize {
        let cutoff = now - window_ms;
        self.events.iter().filter(|e| e.ts >= cutoff).count()
    }
}

// ============================================================================
//  EmaState — Incremental Exponential Moving Average
// ============================================================================

/// Incremental EMA with configurable period (alpha = 2/(period+1)).
#[derive(Debug, Clone)]
pub struct EmaState {
    alpha: f64,
    pub value: Option<f64>,
    count: usize,
    period: usize,
}

impl EmaState {
    pub fn new(period: usize) -> Self {
        Self {
            alpha: 2.0 / (period as f64 + 1.0),
            value: None,
            count: 0,
            period,
        }
    }

    pub fn update(&mut self, val: f64) {
        self.count += 1;
        match self.value {
            None => self.value = Some(val),
            Some(prev) => self.value = Some(prev + self.alpha * (val - prev)),
        }
    }

    pub fn is_ready(&self) -> bool { self.count >= self.period }
    pub fn get(&self) -> Option<f64> { self.value }
}

// ============================================================================
//  CandleBuilder — Aggregates high-frequency ticks into slower candles
// ============================================================================

#[derive(Debug, Clone)]
pub struct CandleBuilder {
    pub period_ms: i64,
    pub current_candle_start: Option<i64>,
    pub current_close: f64,
}

impl CandleBuilder {
    pub fn new(period_ms: i64) -> Self {
        Self {
            period_ms,
            current_candle_start: None,
            current_close: 0.0,
        }
    }

    pub fn update(&mut self, ts: i64, price: f64) -> Option<f64> {
        let candle_start = (ts / self.period_ms) * self.period_ms;

        match self.current_candle_start {
            None => {
                self.current_candle_start = Some(candle_start);
                self.current_close = price;
                None
            }
            Some(start) => {
                if candle_start > start {
                    let closed_price = self.current_close;
                    self.current_candle_start = Some(candle_start);
                    self.current_close = price;
                    Some(closed_price)
                } else {
                    self.current_close = price;
                    None
                }
            }
        }
    }
}

// ============================================================================
//  EwmaVarianceTracker — Exponentially Weighted Mean and Variance
// ============================================================================

/// Tracks mean and variance using exponential smoothing for robust z-scores.
#[derive(Debug, Clone)]
pub struct EwmaVarianceTracker {
    alpha: f64,
    pub mean: Option<f64>,
    pub var: Option<f64>,
    count: usize,
    period: usize,
}

impl EwmaVarianceTracker {
    pub fn new(period: usize) -> Self {
        Self {
            alpha: 2.0 / (period as f64 + 1.0),
            mean: None,
            var: None,
            count: 0,
            period,
        }
    }

    pub fn update(&mut self, val: f64) {
        self.count += 1;
        match self.mean {
            None => {
                self.mean = Some(val);
                self.var = Some(0.0);
            }
            Some(prev_mean) => {
                let diff = val - prev_mean;
                let inc = self.alpha * diff;
                self.mean = Some(prev_mean + inc);
                
                let prev_var = self.var.unwrap_or(0.0);
                self.var = Some((1.0 - self.alpha) * (prev_var + diff * inc));
            }
        }
    }

    pub fn is_ready(&self) -> bool { self.count >= self.period }
    pub fn mean(&self) -> Option<f64> { self.mean }
    pub fn std(&self) -> Option<f64> { self.var.map(|v| v.sqrt()) }
}

// ============================================================================
//  RsiState — Wilder's Smoothed RSI
// ============================================================================

/// RSI using Wilder's smoothing method (exponential moving average of gains/losses).
#[derive(Debug, Clone)]
pub struct RsiState {
    period: usize,
    avg_gain: f64,
    avg_loss: f64,
    count: usize,
    prev_close: Option<f64>,
    // For initial SMA computation
    gains: Vec<f64>,
    losses: Vec<f64>,
}

impl RsiState {
    pub fn new(period: usize) -> Self {
        Self {
            period,
            avg_gain: 0.0,
            avg_loss: 0.0,
            count: 0,
            prev_close: None,
            gains: Vec::with_capacity(period),
            losses: Vec::with_capacity(period),
        }
    }

    pub fn update(&mut self, close: f64) {
        if let Some(prev) = self.prev_close {
            let change = close - prev;
            let gain = if change > 0.0 { change } else { 0.0 };
            let loss = if change < 0.0 { -change } else { 0.0 };

            self.count += 1;

            if self.count <= self.period {
                // Accumulate initial SMA
                self.gains.push(gain);
                self.losses.push(loss);

                if self.count == self.period {
                    let n = self.period as f64;
                    self.avg_gain = self.gains.iter().sum::<f64>() / n;
                    self.avg_loss = self.losses.iter().sum::<f64>() / n;
                }
            } else {
                // Wilder smoothing
                let n = self.period as f64;
                self.avg_gain = (self.avg_gain * (n - 1.0) + gain) / n;
                self.avg_loss = (self.avg_loss * (n - 1.0) + loss) / n;
            }
        }
        self.prev_close = Some(close);
    }

    pub fn is_ready(&self) -> bool {
        self.count >= self.period
    }

    pub fn count(&self) -> usize {
        self.count
    }

    pub fn get(&self) -> Option<f64> {
        if !self.is_ready() { return None; }
        if self.avg_loss == 0.0 {
            return Some(100.0); // No losses → RSI = 100
        }
        let rs = self.avg_gain / self.avg_loss;
        Some(100.0 - (100.0 / (1.0 + rs)))
    }
}

// ============================================================================
//  BollingerState — SMA + Standard Deviation Bands
// ============================================================================

/// Bollinger Bands computed from a RingBuffer of closes.
#[derive(Debug, Clone)]
pub struct BollingerState {
    buffer: RingBuffer,
    num_std: f64,
}

impl BollingerState {
    pub fn new(period: usize, num_std: f64) -> Self {
        Self {
            buffer: RingBuffer::new(period),
            num_std,
        }
    }

    pub fn update(&mut self, close: f64) {
        self.buffer.push(close);
    }

    pub fn is_ready(&self) -> bool { self.buffer.is_full() }

    pub fn count(&self) -> usize { self.buffer.len() }

    /// Returns (sma, upper, lower) or None if not ready.
    pub fn get_bands(&self) -> Option<(f64, f64, f64)> {
        let sma = self.buffer.mean()?;
        let std = self.buffer.std()?;
        Some((sma, sma + self.num_std * std, sma - self.num_std * std))
    }

    /// BB Width = (upper - lower) / sma
    pub fn width(&self) -> Option<f64> {
        let (sma, upper, lower) = self.get_bands()?;
        if sma == 0.0 { return None; }
        Some((upper - lower) / sma)
    }

    /// BB Position = (close - lower) / (upper - lower), in [0, 1] typically.
    pub fn position(&self, close: f64) -> Option<f64> {
        let (_sma, upper, lower) = self.get_bands()?;
        let range = upper - lower;
        if range == 0.0 { return None; }
        Some((close - lower) / range)
    }
}

// ============================================================================
//  BoolRingBuffer — Sliding window of Option<bool> for persistence features
// ============================================================================

/// Fixed-capacity sliding window of `Option<bool>` indicators.
/// Used by persistence features to compute fraction of recent windows
/// where a condition held true, with explicit invalidation support.
#[derive(Debug, Clone)]
pub struct BoolRingBuffer {
    buf: VecDeque<Option<bool>>,
    capacity: usize,
}

impl BoolRingBuffer {
    pub fn new(capacity: usize) -> Self {
        Self {
            buf: VecDeque::with_capacity(capacity),
            capacity,
        }
    }

    /// Push an indicator value. `None` = invalid window (excluded from fraction).
    pub fn push(&mut self, val: Option<bool>) {
        if self.buf.len() >= self.capacity {
            self.buf.pop_front();
        }
        self.buf.push_back(val);
    }

    /// Fraction of valid windows where indicator was true.
    /// Returns None if fewer than `min_valid` non-None entries exist.
    pub fn fraction(&self, min_valid: usize) -> Option<f64> {
        let valid = self.valid_count();
        if valid < min_valid {
            return None;
        }
        let true_count = self.buf.iter()
            .filter(|v| matches!(v, Some(true)))
            .count();
        Some(true_count as f64 / valid as f64)
    }

    /// Count of non-None entries in the buffer.
    pub fn valid_count(&self) -> usize {
        self.buf.iter().filter(|v| v.is_some()).count()
    }

    pub fn len(&self) -> usize {
        self.buf.len()
    }
}

// ============================================================================
//  Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ring_buffer_basic() {
        let mut rb = RingBuffer::new(3);
        rb.push(1.0);
        rb.push(2.0);
        rb.push(3.0);
        assert!(rb.is_full());
        assert_eq!(rb.front(), Some(3.0));
        assert_eq!(rb.get(0), Some(3.0));
        assert_eq!(rb.get(2), Some(1.0));
        
        rb.push(4.0); // Evicts 1.0
        assert_eq!(rb.get(2), Some(2.0));
        assert_eq!(rb.front(), Some(4.0));
    }

    #[test]
    fn test_ring_buffer_mean_std() {
        let mut rb = RingBuffer::new(4);
        rb.push(2.0);
        rb.push(4.0);
        rb.push(4.0);
        rb.push(4.0);
        assert!((rb.mean().unwrap() - 3.5).abs() < 1e-10);
        assert!((rb.std().unwrap() - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_trade_accumulator() {
        let mut ta = TradeAccumulator::new(10_000);
        ta.record_trade(1000, 1.0, true);  // buy at t=1s
        ta.record_trade(2000, 2.0, false); // sell at t=2s
        ta.record_trade(3000, 3.0, true);  // buy at t=3s
        
        assert!((ta.buy_vol(3000, 5000) - 4.0).abs() < 1e-10);
        assert!((ta.sell_vol(3000, 5000) - 2.0).abs() < 1e-10);
        assert_eq!(ta.trade_count(3000, 5000), 3);
        
        // Only last 1s
        assert!((ta.buy_vol(3000, 1000) - 3.0).abs() < 1e-10);
    }

    #[test]
    fn test_liq_accumulator() {
        let mut la = LiqAccumulator::new(60_000);
        la.record(1000, 10.0, true);
        la.record(2000, 20.0, false);
        
        assert!((la.buy_vol(3000, 30_000) - 10.0).abs() < 1e-10);
        assert!((la.sell_vol(3000, 30_000) - 20.0).abs() < 1e-10);
        assert_eq!(la.count(3000, 30_000), 2);
    }

    #[test]
    fn test_ema_state() {
        let mut ema = EmaState::new(3);
        ema.update(10.0);
        ema.update(12.0);
        ema.update(11.0);
        assert!(ema.is_ready());
        assert!(ema.get().is_some());
    }

    #[test]
    fn test_rsi_state() {
        let mut rsi = RsiState::new(14);
        // Feed 15 values to satisfy warmup (14 changes needed)
        let prices = vec![
            44.0, 44.34, 44.09, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
            46.08, 45.89, 46.03, 45.61, 46.28, 46.28,
        ];
        for p in &prices {
            rsi.update(*p);
        }
        assert!(rsi.is_ready());
        let val = rsi.get().unwrap();
        assert!(val > 0.0 && val < 100.0, "RSI should be between 0 and 100, got {}", val);
    }

    #[test]
    fn test_bollinger_state() {
        let mut bb = BollingerState::new(5, 2.0);
        for v in &[10.0, 11.0, 12.0, 11.0, 10.0] {
            bb.update(*v);
        }
        assert!(bb.is_ready());
        let w = bb.width().unwrap();
        assert!(w > 0.0, "BB width should be positive");
        let pos = bb.position(11.0).unwrap();
        assert!(pos > 0.0 && pos < 1.0, "BB pos should be in (0,1) for mid value");
    }

    #[test]
    fn test_ewma_variance() {
        let mut tracker = EwmaVarianceTracker::new(5);
        for v in &[10.0, 10.0, 10.0, 10.0, 10.0] {
            tracker.update(*v);
        }
        assert!(tracker.is_ready());
        assert!((tracker.mean().unwrap() - 10.0).abs() < 1e-10);
        assert!((tracker.std().unwrap() - 0.0).abs() < 1e-10);
        
        tracker.update(20.0);
        assert!(tracker.mean().unwrap() > 10.0);
        assert!(tracker.std().unwrap() > 0.0);
    }

    #[test]
    fn test_bool_ring_basic() {
        let mut br = BoolRingBuffer::new(5);
        br.push(Some(true));
        br.push(Some(true));
        br.push(Some(false));
        br.push(Some(true));
        br.push(Some(false));
        // 3 true out of 5 valid
        assert!((br.fraction(1).unwrap() - 0.6).abs() < 1e-10);
    }

    #[test]
    fn test_bool_ring_min_valid() {
        let mut br = BoolRingBuffer::new(10);
        br.push(Some(true));
        br.push(None);
        br.push(None);
        // Only 1 valid, min_valid=5 → None
        assert!(br.fraction(5).is_none());
        // But min_valid=1 → Some(1.0)
        assert!((br.fraction(1).unwrap() - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_bool_ring_all_invalid() {
        let mut br = BoolRingBuffer::new(5);
        for _ in 0..5 { br.push(None); }
        assert!(br.fraction(1).is_none());
        assert_eq!(br.valid_count(), 0);
    }

    #[test]
    fn test_bool_ring_eviction() {
        let mut br = BoolRingBuffer::new(3);
        br.push(Some(true));
        br.push(Some(true));
        br.push(Some(true));
        assert!((br.fraction(1).unwrap() - 1.0).abs() < 1e-10);
        // Push false → evicts oldest true
        br.push(Some(false));
        // Now: [true, true, false] → 2/3
        assert!((br.fraction(1).unwrap() - 2.0/3.0).abs() < 1e-10);
    }

    #[test]
    fn test_bool_ring_excludes_none_from_fraction() {
        let mut br = BoolRingBuffer::new(5);
        br.push(Some(true));
        br.push(None);
        br.push(Some(false));
        br.push(None);
        br.push(Some(true));
        // 2 true out of 3 valid
        assert!((br.fraction(1).unwrap() - 2.0/3.0).abs() < 1e-10);
        assert_eq!(br.valid_count(), 3);
    }
}
