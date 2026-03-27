use super::buffers::{RingBuffer, EwmaVarianceTracker};

/// State for Group A (Price/Spread) and Group B (Returns/Volatility).
#[derive(Debug, Clone)]
pub struct PriceState {
    /// History of mid prices (most recent first)
    mid_history: RingBuffer,      // capacity = 300 (for rv_5m)
    /// History of 1s log returns
    ret_1s_history: RingBuffer,   // capacity = 300 (for rv_5m)
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
            mid_history: RingBuffer::new(300),  // 5 minutes of 1s samples
            ret_1s_history: RingBuffer::new(300),
            spread_baseline: EwmaVarianceTracker::new(120), // 2-min EWMA half-life
        }
    }
}

impl PriceState {
    /// Call once per emit cycle with the current mid_price.
    pub fn compute(
        &mut self,
        best_bid: f64,
        best_ask: f64,
    ) -> PriceFeatures {
        let mid = (best_bid + best_ask) / 2.0;
        let spread_abs = best_ask - best_bid;
        let spread_bps = if mid > 0.0 { spread_abs / mid * 10_000.0 } else { 0.0 };

        // Returns at multiple horizons
        let ret_1s = self.mid_history.get(0).map(|prev| (mid / prev).ln());
        let ret_3s = self.mid_history.get(2).map(|prev| (mid / prev).ln());
        let ret_5s = self.mid_history.get(4).map(|prev| (mid / prev).ln());
        let ret_10s = self.mid_history.get(9).map(|prev| (mid / prev).ln());
        let ret_30s = self.mid_history.get(29).map(|prev| (mid / prev).ln());

        // Record return
        if let Some(r) = ret_1s {
            self.ret_1s_history.push(r);
        }

        // Realized volatility at multiple horizons
        let rv_5s = self.ret_1s_history.std_n(5);
        let rv_30s = self.ret_1s_history.std_n(30);
        let rv_5m = self.ret_1s_history.std_n(300);

        // Spread vs rolling baseline (z-score)
        self.spread_baseline.update(spread_bps);
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

        // Slope of mid price (linear regression proxy via endpoint difference)
        // slope_5s: price change per second over 5s window, normalized by mid
        let slope_mid_5s = self.mid_history.get(4).map(|prev| {
            if mid > 0.0 { (mid - prev) / mid * 10_000.0 / 5.0 } else { 0.0 }
        });
        let slope_mid_15s = self.mid_history.get(14).map(|prev| {
            if mid > 0.0 { (mid - prev) / mid * 10_000.0 / 15.0 } else { 0.0 }
        });

        // Push mid to history AFTER computing returns
        self.mid_history.push(mid);

        PriceFeatures {
            mid_price: mid,
            spread_abs,
            spread_bps,
            ret_1s,
            ret_3s,
            ret_5s,
            ret_10s,
            ret_30s,
            rv_5s,
            rv_30s,
            rv_5m,
            spread_vs_baseline,
            slope_mid_5s,
            slope_mid_15s,
        }
    }

    /// Returns current mid price without computing features.
    pub fn current_mid(&self) -> Option<f64> {
        self.mid_history.front()
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
    pub rv_5s: Option<f64>,
    pub rv_30s: Option<f64>,
    pub rv_5m: Option<f64>,
    pub spread_vs_baseline: Option<f64>,
    pub slope_mid_5s: Option<f64>,
    pub slope_mid_15s: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_price_state_basic() {
        let mut ps = PriceState::new();
        let f1 = ps.compute(50000.0, 50010.0);
        assert!((f1.mid_price - 50005.0).abs() < 1e-6);
        assert!((f1.spread_abs - 10.0).abs() < 1e-6);
        assert!(f1.ret_1s.is_none());

        let f2 = ps.compute(50010.0, 50020.0);
        assert!(f2.ret_1s.is_some());
        assert!(f2.ret_3s.is_none()); // Only 1 prior sample
    }

    #[test]
    fn test_new_return_horizons() {
        let mut ps = PriceState::new();
        // Feed 10 samples
        for i in 0..10 {
            ps.compute(50000.0 + i as f64, 50010.0 + i as f64);
        }
        let f = ps.compute(50010.0, 50020.0);
        assert!(f.ret_1s.is_some());
        assert!(f.ret_3s.is_some());
        assert!(f.ret_5s.is_some());
        assert!(f.ret_10s.is_some());
        assert!(f.ret_30s.is_none()); // Only 11 samples
    }

    #[test]
    fn test_rv_5s_warmup() {
        let mut ps = PriceState::new();
        for i in 0..5 {
            ps.compute(50000.0 + i as f64, 50010.0 + i as f64);
        }
        let f = ps.compute(50005.0, 50015.0);
        assert!(f.rv_5s.is_some(), "rv_5s should be Some after 5+ returns");
    }

    #[test]
    fn test_spread_baseline() {
        let mut ps = PriceState::new();
        // Feed 120 identical spread samples to warm up baseline
        for _ in 0..120 {
            ps.compute(50000.0, 50010.0); // spread = 10 bps
        }
        let f = ps.compute(50000.0, 50010.0);
        assert!(f.spread_vs_baseline.is_some());
        // Same spread as baseline → z-score should be ~0
        assert!(f.spread_vs_baseline.unwrap().abs() < 0.5);
    }

    #[test]
    fn test_slope() {
        let mut ps = PriceState::new();
        // Feed 15 samples with rising prices
        for i in 0..15 {
            ps.compute(50000.0 + i as f64 * 10.0, 50010.0 + i as f64 * 10.0);
        }
        let f = ps.compute(50150.0, 50160.0);
        assert!(f.slope_mid_5s.is_some());
        assert!(f.slope_mid_15s.is_some());
        // Prices are rising → slope should be positive
        assert!(f.slope_mid_5s.unwrap() > 0.0);
        assert!(f.slope_mid_15s.unwrap() > 0.0);
    }

    #[test]
    fn test_no_lookahead() {
        let mut ps = PriceState::new();
        ps.compute(100.0, 101.0);
        ps.compute(101.0, 102.0);
        let f3 = ps.compute(102.0, 103.0);

        ps.compute(103.0, 104.0);
        ps.compute(104.0, 105.0);

        let mut ps2 = PriceState::new();
        ps2.compute(100.0, 101.0);
        ps2.compute(101.0, 102.0);
        let f3b = ps2.compute(102.0, 103.0);

        assert_eq!(f3.mid_price, f3b.mid_price);
        assert_eq!(f3.ret_1s, f3b.ret_1s);
        assert_eq!(f3.ret_3s, f3b.ret_3s);
        assert_eq!(f3.spread_abs, f3b.spread_abs);
    }

    #[test]
    fn test_rv_warmup() {
        let mut ps = PriceState::new();
        for i in 0..29 {
            ps.compute(50000.0 + i as f64, 50010.0 + i as f64);
        }
        let f = ps.compute(50030.0, 50040.0);
        assert!(f.rv_30s.is_none(), "rv_30s should be None before 30 samples");

        let f = ps.compute(50031.0, 50041.0);
        assert!(f.rv_30s.is_some(), "rv_30s should be Some after 30+ returns");
    }
}
