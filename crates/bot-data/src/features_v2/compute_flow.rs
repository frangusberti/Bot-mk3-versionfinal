use super::buffers::TradeAccumulator;
use super::buffers::EwmaVarianceTracker;

/// State for Group C: Taker Flow / Tape features.
#[derive(Debug, Clone)]
pub struct FlowState {
    trades: TradeAccumulator,
    /// Robust EWMA tracker for mean and variance of 1s trade counts
    trade_count_baseline: EwmaVarianceTracker,
    /// Tracker for 5s trade count z-score
    trade_count_5s_baseline: EwmaVarianceTracker,
}

impl FlowState {
    pub fn new() -> Self {
        Self::default()
    }
}

impl Default for FlowState {
    fn default() -> Self {
        Self {
            trades: TradeAccumulator::new(20_000), // Keep 20s of trade data (for 15s windows)
            trade_count_baseline: EwmaVarianceTracker::new(60), // 60s EWMA half-life
            trade_count_5s_baseline: EwmaVarianceTracker::new(30),
        }
    }
}

impl FlowState {
    /// Record an incoming trade event (called from update()).
    pub fn record_trade(&mut self, ts: i64, qty: f64, is_taker_buy: bool) {
        self.trades.record_trade(ts, qty, is_taker_buy);
    }

    /// 5-second trade count — used by absorption gating (Sprint 2).
    pub fn trade_count_5s(&self, now: i64) -> usize {
        self.trades.trade_count(now, 5_000)
    }

    /// Compute flow features at emission time.
    pub fn compute(&mut self, t_emit: i64, z_clamp: (f64, f64)) -> FlowFeatures {
        let taker_buy_vol_1s = self.trades.buy_vol(t_emit, 1_000);
        let taker_sell_vol_1s = self.trades.sell_vol(t_emit, 1_000);
        let taker_buy_vol_5s = self.trades.buy_vol(t_emit, 5_000);
        let taker_sell_vol_5s = self.trades.sell_vol(t_emit, 5_000);
        let tape_trades_1s = self.trades.trade_count(t_emit, 1_000) as f64;
        let tape_trades_5s = self.trades.trade_count(t_emit, 5_000) as f64;

        // ── Trade Imbalance: (buy - sell) / (buy + sell), at 1s/5s/15s ──
        let trade_imbalance_1s = {
            let total = taker_buy_vol_1s + taker_sell_vol_1s;
            if total > 0.0 {
                Some((taker_buy_vol_1s - taker_sell_vol_1s) / total)
            } else {
                None
            }
        };

        let trade_imbalance_5s = {
            let total = taker_buy_vol_5s + taker_sell_vol_5s;
            if total > 0.0 {
                Some((taker_buy_vol_5s - taker_sell_vol_5s) / total)
            } else {
                None
            }
        };

        let trade_imbalance_15s = {
            let buy_15 = self.trades.buy_vol(t_emit, 15_000);
            let sell_15 = self.trades.sell_vol(t_emit, 15_000);
            let total = buy_15 + sell_15;
            if total > 0.0 {
                Some((buy_15 - sell_15) / total)
            } else {
                None
            }
        };

        // ── Taker buy ratio removed — taker_buy_ratio_5s = 0.5 + trade_imbalance_5s/2
        //    Perfectly redundant with trade_imbalance_5s (ρ=1.000). Removed in Sprint 1 fix.

        // ── Z-score of 1s trade count vs rolling robust baseline ──
        self.trade_count_baseline.update(tape_trades_1s);
        let tape_intensity_z = if self.trade_count_baseline.is_ready() {
            let mean = self.trade_count_baseline.mean().unwrap_or(0.0);
            let std = self.trade_count_baseline.std().unwrap_or(1.0);
            let z = if std > 0.0 {
                (tape_trades_1s - mean) / std
            } else {
                0.0
            };
            Some(z.clamp(z_clamp.0, z_clamp.1))
        } else {
            None
        };

        // ── Z-score of 5s trade count ──
        self.trade_count_5s_baseline.update(tape_trades_5s);
        let tape_intensity_5s_z = if self.trade_count_5s_baseline.is_ready() {
            let mean = self.trade_count_5s_baseline.mean().unwrap_or(0.0);
            let std = self.trade_count_5s_baseline.std().unwrap_or(1.0);
            let z = if std > 0.0 {
                (tape_trades_5s - mean) / std
            } else {
                0.0
            };
            Some(z.clamp(z_clamp.0, z_clamp.1))
        } else {
            None
        };

        FlowFeatures {
            taker_buy_vol_1s,
            taker_sell_vol_1s,
            taker_buy_vol_5s,
            taker_sell_vol_5s,
            tape_trades_1s,
            tape_intensity_z,
            trade_imbalance_1s,
            trade_imbalance_5s,
            trade_imbalance_15s,
            tape_intensity_5s_z,
        }
    }
}

#[derive(Debug, Clone)]
pub struct FlowFeatures {
    pub taker_buy_vol_1s: f64,
    pub taker_sell_vol_1s: f64,
    pub taker_buy_vol_5s: f64,
    pub taker_sell_vol_5s: f64,
    pub tape_trades_1s: f64,
    pub tape_intensity_z: Option<f64>,
    // New dynamic features
    pub trade_imbalance_1s: Option<f64>,
    pub trade_imbalance_5s: Option<f64>,
    pub trade_imbalance_15s: Option<f64>,
    pub tape_intensity_5s_z: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_flow_state() {
        let mut fs = FlowState::new();
        fs.record_trade(500, 1.0, true);
        fs.record_trade(800, 2.0, true);
        fs.record_trade(900, 0.5, false);

        let f = fs.compute(1000, (-5.0, 5.0));
        assert!((f.taker_buy_vol_1s - 3.0).abs() < 1e-10);
        assert!((f.taker_sell_vol_1s - 0.5).abs() < 1e-10);
        assert_eq!(f.tape_trades_1s, 3.0);
    }

    #[test]
    fn test_trade_imbalance() {
        let mut fs = FlowState::new();
        // All buys → imbalance should be +1.0
        fs.record_trade(500, 5.0, true);
        fs.record_trade(800, 3.0, true);

        let f = fs.compute(1000, (-5.0, 5.0));
        let imb = f.trade_imbalance_1s.unwrap();
        assert!((imb - 1.0).abs() < 1e-10, "All buys → imbalance = +1.0");

        // Now add equal sells
        let mut fs2 = FlowState::new();
        fs2.record_trade(500, 5.0, true);
        fs2.record_trade(800, 5.0, false);
        let f2 = fs2.compute(1000, (-5.0, 5.0));
        let imb2 = f2.trade_imbalance_1s.unwrap();
        assert!(imb2.abs() < 1e-10, "Equal buy/sell → imbalance = 0.0");
    }

    #[test]
    fn test_no_trades_gives_none() {
        let mut fs = FlowState::new();
        let f = fs.compute(1000, (-5.0, 5.0));
        assert!(f.trade_imbalance_1s.is_none());
    }

    #[test]
    fn test_tape_intensity_outlier_clamp() {
        let mut fs = FlowState::new();
        for i in 1..=60 {
            fs.record_trade(i * 1000, 1.0, true);
            fs.compute(i * 1000, (-5.0, 5.0));
        }

        for _ in 0..100 {
            fs.record_trade(61000, 1.0, true);
        }
        let f = fs.compute(61000, (-5.0, 5.0));

        let z = f.tape_intensity_z.expect("Should be ready");
        assert_eq!(z, 5.0, "Z-score should be clamped to max +5.0");
    }
}
