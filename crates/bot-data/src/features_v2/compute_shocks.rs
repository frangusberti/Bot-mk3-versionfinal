use super::buffers::{LiqAccumulator, EwmaVarianceTracker};

/// State for Group E: Shocks / Derivatives features.
#[derive(Debug, Clone)]
pub struct ShockState {
    liqs: LiqAccumulator,
    mark_price: Option<f64>,
    funding_rate_val: Option<f64>,
    /// EWMA baseline for funding rate z-score
    funding_baseline: EwmaVarianceTracker,
}

impl ShockState {
    pub fn new() -> Self {
        Self::default()
    }
}

impl Default for ShockState {
    fn default() -> Self {
        Self {
            liqs: LiqAccumulator::new(60_000),
            mark_price: None,
            funding_rate_val: None,
            funding_baseline: EwmaVarianceTracker::new(480), // ~8h of 1-min samples ≈ 1-day at 8h funding
        }
    }
}

impl ShockState {
    /// Record a liquidation event.
    pub fn record_liquidation(&mut self, ts: i64, qty: f64, is_buy: bool) {
        self.liqs.record(ts, qty, is_buy);
    }

    /// Update mark price from markPrice stream.
    pub fn update_mark_price(&mut self, mark: f64) {
        self.mark_price = Some(mark);
    }

    /// Update funding rate.
    pub fn update_funding_rate(&mut self, rate: f64) {
        self.funding_rate_val = Some(rate);
        self.funding_baseline.update(rate);
    }

    /// Compute shock features at emission time.
    pub fn compute(&self, t_emit: i64, mid_price: f64) -> ShockFeatures {
        let window_30s = 30_000;
        let liq_buy_vol_30s = self.liqs.buy_vol(t_emit, window_30s);
        let liq_sell_vol_30s = self.liqs.sell_vol(t_emit, window_30s);
        let liq_net_30s = liq_buy_vol_30s - liq_sell_vol_30s;
        let liq_count_30s = self.liqs.count(t_emit, window_30s) as f64;

        let mark_minus_mid_bps = self.mark_price.and_then(|mark| {
            if mid_price > 0.0 {
                Some((mark - mid_price) / mid_price * 10_000.0)
            } else {
                None
            }
        });

        // Funding z-score
        let funding_zscore = if self.funding_baseline.is_ready() {
            if let Some(rate) = self.funding_rate_val {
                let mean = self.funding_baseline.mean().unwrap_or(0.0);
                let std = self.funding_baseline.std().unwrap_or(1.0);
                if std > 1e-12 {
                    Some(((rate - mean) / std).clamp(-5.0, 5.0))
                } else {
                    Some(0.0)
                }
            } else {
                None
            }
        } else {
            None
        };

        ShockFeatures {
            liq_buy_vol_30s,
            liq_sell_vol_30s,
            liq_net_30s,
            liq_count_30s,
            mark_minus_mid_bps,
            funding_rate: self.funding_rate_val,
            funding_zscore,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ShockFeatures {
    pub liq_buy_vol_30s: f64,
    pub liq_sell_vol_30s: f64,
    pub liq_net_30s: f64,
    pub liq_count_30s: f64,
    pub mark_minus_mid_bps: Option<f64>,
    pub funding_rate: Option<f64>,
    pub funding_zscore: Option<f64>,
}
