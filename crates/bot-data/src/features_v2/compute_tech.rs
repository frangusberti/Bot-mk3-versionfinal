use super::buffers::{EmaState, RsiState, BollingerState, CandleBuilder};

/// State for Group F: Slow technical indicators (for gating, not HFT).
/// Computed from selected timeframe closes (e.g. 1s or 1m).
#[derive(Debug, Clone)]
pub struct TechState {
    ema200: EmaState,
    rsi14: RsiState,
    bb20: BollingerState, // 20-period BB with 2σ
    candle_builder: Option<CandleBuilder>,
}

impl TechState {
    pub fn new(slow_tf: &str) -> Self {
        let candle_builder = if slow_tf == "1m" {
            Some(CandleBuilder::new(60_000))
        } else if slow_tf == "1s" {
            Some(CandleBuilder::new(1000))
        } else {
            None
        };

        Self {
            ema200: EmaState::new(200),
            rsi14: RsiState::new(14),
            bb20: BollingerState::new(20, 2.0),
            candle_builder,
        }
    }

    /// Update all technical indicators. If slow_tf == "1m", only updates on 1m close boundaries.
    pub fn update(&mut self, ts: i64, mid_price: f64) {
        if let Some(builder) = &mut self.candle_builder {
            if let Some(closed_price) = builder.update(ts, mid_price) {
                self.ema200.update(closed_price);
                self.rsi14.update(closed_price);
                self.bb20.update(closed_price);
            }
        } else {
            self.ema200.update(mid_price);
            self.rsi14.update(mid_price);
            self.bb20.update(mid_price);
        }
    }

    /// Compute technical features.
    pub fn compute(&self, mid_price: f64) -> TechFeatures {
        let ema200_distance_pct = if self.ema200.is_ready() {
            self.ema200.get().map(|ema| {
                if ema > 0.0 { (mid_price - ema) / ema * 100.0 } else { 0.0 }
            })
        } else {
            None
        };

        let rsi_14 = self.rsi14.get();

        let bb_width = if self.bb20.is_ready() { self.bb20.width() } else { None };
        let bb_pos = if self.bb20.is_ready() { self.bb20.position(mid_price) } else { None };

        TechFeatures {
            ema200_distance_pct,
            rsi_14,
            bb_width,
            bb_pos,
        }
    }
}

#[derive(Debug, Clone)]
pub struct TechFeatures {
    pub ema200_distance_pct: Option<f64>,
    pub rsi_14: Option<f64>,
    pub bb_width: Option<f64>,
    pub bb_pos: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tech_state_1s() {
        let mut tech = TechState::new("1s");
        
        // Feed 30 data points exactly 1 second apart
        for i in 1..=30 {
            tech.update(i * 1000, 100.0 + (i as f64));
        }
        
        // For a 20-period bollinger band, we need 20 inputs. Since we feed 1 per second in "1s" mode,
        // it should definitely be ready after 30 seconds.
        let f = tech.compute(130.0);
        assert!(f.bb_width.is_some(), "BB should be ready after 20 inputs in 1s mode");
    }

    #[test]
    fn test_tech_state_1m() {
        let mut tech = TechState::new("1m");
        
        // Feed 59 data points exactly 1 second apart
        for i in 1..=59 {
            tech.update(i * 1000, 100.0 + (i as f64));
        }
        
        // In "1m" mode, 59 seconds is still inside the first candle [0, 60_000).
        // It hasn't closed yet! No BB state should exist.
        let f1 = tech.compute(159.0);
        assert!(f1.bb_width.is_none(), "BB should not be ready, 0 candles have closed");

        // Now cross the 1-minute boundary (t=60000 starts the [60k, 120k) candle)
        // This closes the first candle with close price = 159.0
        tech.update(60_000, 200.0);
        
        // We now have exactly ONE candle closed.
        // BB needs 20 candles! Let's just push 20 minutes worth of data.
        for min in 1..=21 {
            tech.update(min * 60_000, 100.0 + (min as f64));
        }

        let f2 = tech.compute(121.0);
        assert!(f2.bb_width.is_some(), "BB should be ready after >20 minutes in 1m mode");
    }
}
