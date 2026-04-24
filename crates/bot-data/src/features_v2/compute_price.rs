use super::buffers::{RingBuffer, EwmaVarianceTracker};

/// State for Group A (Price/Spread) and Group B (Returns/Volatility).
#[derive(Debug, Clone)]
pub struct PriceState {
    /// History of mid prices (most recent first)
    pub mid_history: RingBuffer,      // capacity covers 1h horizons
    /// History of 1s log returns
    ret_1s_history: RingBuffer,   // capacity covers 1h horizons
    /// EWMA baseline for spread_bps z-score
    spread_baseline: EwmaVarianceTracker,
}

impl PriceState {
    pub fn new() -> Self {
        Self::default()
    }
}

impl Default for PriceState {
    fn default() -> Self {
        Self {
            mid_history: RingBuffer::new(3605),  // Need >3600 due to offset=1 logic
            ret_1s_history: RingBuffer::new(3605),
            spread_baseline: EwmaVarianceTracker::new(120), // 2-min EWMA half-life
        }
    }
}

impl PriceState {
    fn window_values(&self, mid: f64, offset: usize, len: usize) -> Option<Vec<f64>> {
        if len == 0 {
            return None;
        }
        let mut values = Vec::with_capacity(len);
        values.push(mid);
        values.extend(self.mid_history.iter().skip(offset).take(len.saturating_sub(1)).copied());
        if values.len() == len {
            Some(values)
        } else {
            None
        }
    }

    fn range_position(&self, mid: f64, offset: usize, len: usize) -> Option<f64> {
        self.window_values(mid, offset, len).map(|vals| {
            let low = vals.iter().copied().fold(f64::INFINITY, f64::min);
            let high = vals.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let width = high - low;
            if !width.is_finite() || width <= 1e-12 {
                0.5
            } else {
                ((mid - low) / width).clamp(0.0, 1.0)
            }
        })
    }

    /// Update internal buffers (history, baseline) without returning features.
    /// Call this once per second.
    pub fn update_state(&mut self, mid: f64, spread_bps: f64) {
        // Record return (mid vs mid_history.get(0))
        if let Some(prev) = self.mid_history.get(0) {
            if prev > 0.0 {
                let r = (mid / prev).ln();
                self.ret_1s_history.push(r);
            }
        }
        
        // Spread baseline
        self.spread_baseline.update(spread_bps);
        
        // Push mid to history AFTER computing returns
        self.mid_history.push(mid);
    }

    /// Computes features based on current mid and BID/ASK.
    /// Does NOT update state. Detects if update_state was already called to avoid off-by-one errors.
    pub fn compute(&self, best_bid: f64, best_ask: f64) -> PriceFeatures {
        let mid = (best_bid + best_ask) / 2.0;
        let spread_abs = best_ask - best_bid;
        let spread_bps = if mid > 0.0 { spread_abs / mid * 10_000.0 } else { 0.0 };

        // Determine if history already contains the 'current' mid from a recent update_state call.
        // If it does, we shift our lookback indices by 1.
        let offset = if self.mid_history.front() == Some(mid) { 1 } else { 0 };

        // Returns at multiple horizons
        let ret_1s = self.mid_history.get(offset + 0).map(|prev| (mid / prev).ln());
        let ret_3s = self.mid_history.get(offset + 2).map(|prev| (mid / prev).ln());
        let ret_5s = self.mid_history.get(offset + 4).map(|prev| (mid / prev).ln());
        let ret_10s = self.mid_history.get(offset + 9).map(|prev| (mid / prev).ln());
        let ret_30s = self.mid_history.get(offset + 29).map(|prev| (mid / prev).ln());
        let ret_5m = self.mid_history.get(offset + 299).map(|prev| (mid / prev).ln());
        let ret_15m = self.mid_history.get(offset + 899).map(|prev| (mid / prev).ln());
        let ret_1h = self.mid_history.get(offset + 3599).map(|prev| (mid / prev).ln());

        // Realized volatility at multiple horizons
        let rv_5s = self.ret_1s_history.std_n(5);
        let rv_30s = self.ret_1s_history.std_n(30);
        let rv_5m = self.ret_1s_history.std_n(300);
        let rv_15m = self.ret_1s_history.std_n(900);
        let rv_1h = self.ret_1s_history.std_n(3600);

        // Spread vs rolling baseline (z-score)
        let spread_vs_baseline = if self.spread_baseline.is_ready() {
            let mean = self.spread_baseline.mean().unwrap_or(0.0);
            let std = self.spread_baseline.std().unwrap_or(1.0);
            if std > 1e-9 {
                Some(((spread_bps - mean) / std).clamp(-5.0, 5.0))
            } else {
                Some(0.0)
            }
        } else {
            None
        };

        // Slope of mid price
        let slope_mid_5s = self.mid_history.get(offset + 4).map(|prev| {
            if mid > 0.0 { (mid - prev) / mid * 10_000.0 / 5.0 } else { 0.0 }
        });
        let slope_mid_15s = self.mid_history.get(offset + 14).map(|prev| {
            if mid > 0.0 { (mid - prev) / mid * 10_000.0 / 15.0 } else { 0.0 }
        });
        let slope_mid_60s = self.mid_history.get(offset + 59).map(|prev| {
            if mid > 0.0 { (mid - prev) / mid * 10_000.0 / 60.0 } else { 0.0 }
        });
        let slope_mid_5m = self.mid_history.get(offset + 299).map(|prev| {
            if mid > 0.0 { (mid - prev) / mid * 10_000.0 / 300.0 } else { 0.0 }
        });
        let slope_mid_15m = self.mid_history.get(offset + 899).map(|prev| {
            if mid > 0.0 { (mid - prev) / mid * 10_000.0 / 900.0 } else { 0.0 }
        });
        let slope_mid_1h = self.mid_history.get(offset + 3599).map(|prev| {
            if mid > 0.0 { (mid - prev) / mid * 10_000.0 / 3600.0 } else { 0.0 }
        });

        let range_pos_5m = self.range_position(mid, offset, 300);
        let range_pos_15m = self.range_position(mid, offset, 900);
        let range_pos_1h = self.range_position(mid, offset, 3600);

        PriceFeatures {
            mid_price: mid,
            spread_abs,
            spread_bps,
            ret_1s,
            ret_3s,
            ret_5s,
            ret_10s,
            ret_30s,
            ret_5m,
            ret_15m,
            ret_1h,
            rv_5s,
            rv_30s,
            rv_5m,
            rv_15m,
            rv_1h,
            spread_vs_baseline,
            slope_mid_5s,
            slope_mid_15s,
            slope_mid_60s,
            slope_mid_5m,
            slope_mid_15m,
            slope_mid_1h,
            range_pos_5m,
            range_pos_15m,
            range_pos_1h,
        }
    }

    /// Returns current mid price without computing features.
    pub fn current_mid(&self) -> Option<f64> {
        self.mid_history.front()
    }

    pub fn mid_count(&self) -> usize {
        self.mid_history.len()
    }
}

/// Output of PriceState::compute.
#[derive(Debug, Clone)]
pub struct PriceFeatures {
    pub mid_price: f64,
    pub spread_abs: f64,
    pub spread_bps: f64,
    pub ret_1s: Option<f64>,
    pub ret_3s: Option<f64>,
    pub ret_5s: Option<f64>,
    pub ret_10s: Option<f64>,
    pub ret_30s: Option<f64>,
    pub ret_5m: Option<f64>,
    pub ret_15m: Option<f64>,
    pub ret_1h: Option<f64>,
    pub rv_5s: Option<f64>,
    pub rv_30s: Option<f64>,
    pub rv_5m: Option<f64>,
    pub rv_15m: Option<f64>,
    pub rv_1h: Option<f64>,
    pub spread_vs_baseline: Option<f64>,
    pub slope_mid_5s: Option<f64>,
    pub slope_mid_15s: Option<f64>,
    pub slope_mid_60s: Option<f64>,
    pub slope_mid_5m: Option<f64>,
    pub slope_mid_15m: Option<f64>,
    pub slope_mid_1h: Option<f64>,
    pub range_pos_5m: Option<f64>,
    pub range_pos_15m: Option<f64>,
    pub range_pos_1h: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_price_state_basic() {
        let mut ps = PriceState::new();
        // Step 1: Compute features (history empty)
        let f1 = ps.compute(50000.0, 50010.0);
        assert!((f1.mid_price - 50005.0).abs() < 1e-6);
        assert!(f1.ret_1s.is_none());
        
        // Step 2: Update state
        ps.update_state(50005.0, 10.0 / 50005.0 * 10000.0);

        // Step 3: Compute f2
        let f2 = ps.compute(50010.0, 50020.0);
        assert!(f2.ret_1s.is_some());
        assert!(f2.ret_3s.is_none()); 
    }

    #[test]
    fn test_price_state_multi_timeframe_context() {
        let mut ps = PriceState::new();
        let mut mid = 100.0;

        for _ in 0..3605 {
            ps.update_state(mid, 10.0);
            mid += 0.01;
        }

        let f = ps.compute(mid - 0.005, mid + 0.005);
        assert!(f.ret_5m.is_some());
        assert!(f.ret_15m.is_some());
        assert!(f.ret_1h.is_some());
        assert!(f.rv_15m.is_some());
        assert!(f.rv_1h.is_some());
        assert!(f.slope_mid_1h.is_some());
        assert!(f.range_pos_5m.is_some());
        assert!(f.range_pos_15m.is_some());
        assert!(f.range_pos_1h.is_some());
    }
}
