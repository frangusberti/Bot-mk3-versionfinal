use super::buffers::EmaState;
use std::collections::VecDeque;

#[derive(Debug, Clone)]
pub struct OIState {
    latest_oi: Option<f64>,
    oi_history: VecDeque<(i64, f64)>, // (timestamp_ms, oi_value)
    zscore_ewma: EmaState,
    zscore_var: EmaState,
}

impl OIState {
    pub fn new() -> Self {
        Self {
            latest_oi: None,
            oi_history: VecDeque::new(),
            // 30 minute equivalent if emitting at 1s intervals = 1800 ticks roughly.
            // But we update on event. We'll use a fixed period for z-score ewma (e.g. 1800)
            zscore_ewma: EmaState::new(1800),
            zscore_var: EmaState::new(1800),
        }
    }

    pub fn update(&mut self, ts: i64, open_interest: f64) {
        self.latest_oi = Some(open_interest);
        self.oi_history.push_back((ts, open_interest));
        
        // Clean up history older than 5 minutes (300,000 ms) + buffer
        while let Some(&(old_ts, _)) = self.oi_history.front() {
            if ts - old_ts > 310_000 {
                self.oi_history.pop_front();
            } else {
                break;
            }
        }

        // Update EWMA
        self.zscore_ewma.update(open_interest);
        
        if let Some(mean) = self.zscore_ewma.get() {
            let diff = open_interest - mean;
            let sq_diff = diff * diff;
            self.zscore_var.update(sq_diff);
        }
    }

    pub fn compute(&self, current_ts: i64) -> OIFeatures {
        let oi_value = self.latest_oi;

        // Helper: find OI at approximately `offset_ms` ago
        let oi_at_offset = |offset_ms: i64, min_age_ms: i64| -> Option<f64> {
            let target_ts = current_ts - offset_ms;
            let mut best: Option<(i64, f64)> = None;
            for &(ts, val) in self.oi_history.iter() {
                if current_ts - ts >= min_age_ms {
                    match best {
                        None => best = Some((ts, val)),
                        Some((bt, _)) => {
                            if (ts - target_ts).abs() < (bt - target_ts).abs() {
                                best = Some((ts, val));
                            }
                        }
                    }
                }
            }
            best.map(|(_, v)| v)
        };

        let relative_delta = |old: Option<f64>| -> Option<f64> {
            if let (Some(curr), Some(old_val)) = (oi_value, old) {
                if old_val > 0.0 {
                    Some((curr - old_val) / old_val * 100.0)
                } else {
                    Some(0.0)
                }
            } else {
                None
            }
        };

        // Short deltas: 30s and 1m
        let oi_delta_30s = relative_delta(oi_at_offset(30_000, 25_000));
        let oi_delta_1m = relative_delta(oi_at_offset(60_000, 50_000));

        // Existing 5m delta
        let oi_5m_ago = oi_at_offset(300_000, 270_000);
        let oi_delta_5m = relative_delta(oi_5m_ago);

        let oi_zscore_30m = if self.zscore_ewma.is_ready() && self.zscore_var.is_ready() {
            if let (Some(val), Some(mean), Some(var)) = (oi_value, self.zscore_ewma.get(), self.zscore_var.get()) {
                if var > 1e-9 {
                    let std_dev = var.sqrt();
                    let mut z = (val - mean) / std_dev;
                    z = z.clamp(-5.0, 5.0);
                    Some(z)
                } else {
                    Some(0.0)
                }
            } else {
                None
            }
        } else {
            None
        };

        OIFeatures {
            oi_value,
            oi_delta_30s,
            oi_delta_1m,
            oi_delta_5m,
            oi_zscore_30m,
        }
    }
}

#[derive(Debug, Clone)]
pub struct OIFeatures {
    pub oi_value: Option<f64>,
    pub oi_delta_30s: Option<f64>,
    pub oi_delta_1m: Option<f64>,
    pub oi_delta_5m: Option<f64>,
    pub oi_zscore_30m: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_oi_state_basic() {
        let mut oi = OIState::new();
        
        oi.update(1000, 100.0);
        let f1 = oi.compute(1000);
        assert_eq!(f1.oi_value, Some(100.0));
        assert_eq!(f1.oi_delta_5m, None, "Not enough history for 5m delta");
        
        // Fast forward 5 mins
        oi.update(301_500, 105.0);
        let f2 = oi.compute(301_500);
        assert_eq!(f2.oi_value, Some(105.0));
        assert_eq!(f2.oi_delta_5m, Some(5.0)); // (105-100)/100 * 100 = 5%
    }

    #[test]
    fn test_oi_never_valid_when_missing() {
        let oi = OIState::new();
        // Compute directly without updates
        let f = oi.compute(50_000);
        
        assert_eq!(f.oi_value, None);
        assert_eq!(f.oi_delta_5m, None);
        assert_eq!(f.oi_zscore_30m, None);
        
        // The `to_obs_vec` logic maps `None` to `0.0` value and `0.0` mask.
        // We guarantee that nothing leaks.
    }

    #[test]
    fn test_oi_warmup_respects_poll_interval() {
        // Case A: Poll 60s
        let mut oi_60s = OIState::new();
        for i in 0..=5 {
            oi_60s.update(i * 60_000, 100.0);
        }
        // At i=5 (300_000ms, which is 5 mins), delta should be valid!
        let f_60s = oi_60s.compute(300_000);
        assert!(f_60s.oi_delta_5m.is_some(), "Delta must be valid after 5 mins (5 samples)");

        // Case B: Poll 30s
        let mut oi_30s = OIState::new();
        for i in 0..=5 {
            oi_30s.update(i * 30_000, 100.0);
        }
        // At i=5 (150_000ms), we only have 2.5 mins of data despite 6 samples!
        let f_30s_early = oi_30s.compute(150_000);
        assert!(f_30s_early.oi_delta_5m.is_none(), "Delta must NOT be valid at 2.5 mins (despite 6 samples)");

        // Fast forward the 30s poller to reach 5 mins (10 samples)
        for i in 6..=10 {
            oi_30s.update(i * 30_000, 100.0);
        }
        let f_30s_ready = oi_30s.compute(300_000);
        assert!(f_30s_ready.oi_delta_5m.is_some(), "Delta must be valid at 5 mins (10 samples)");
    }
}
