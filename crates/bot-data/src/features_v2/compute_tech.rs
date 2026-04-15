use super::buffers::{EmaState, RsiState, BollingerState, CandleBuilder};

/// State for Group F: Slow technical indicators (for gating, not HFT).
/// Computed from selected timeframe closes (e.g. 1s or 1m).
/// State for Group F: Slow technical indicators (for gating/alpha).
/// Computed from selected timeframe closes (e.g. 1m, 5m, 15m).
#[derive(Debug, Clone)]
pub struct HorizonState {
    pub rsi14: RsiState,
    pub bb20: BollingerState,
    pub builder: CandleBuilder,
}

impl HorizonState {
    pub fn new(period_ms: i64) -> Self {
        Self {
            rsi14: RsiState::new(14),
            bb20: BollingerState::new(20, 2.0),
            builder: CandleBuilder::new(period_ms),
        }
    }

    pub fn update(&mut self, ts: i64, price: f64) -> bool {
        if let Some(closed_price) = self.builder.update(ts, price) {
            self.rsi14.update(closed_price);
            self.bb20.update(closed_price);
            true
        } else {
            false
        }
    }

    pub fn candle_count(&self) -> usize {
        self.rsi14.count()
    }
}

/// State for Group F: Slow technical indicators (for gating/alpha).
#[derive(Debug, Clone)]
pub struct TechState {
    pub h1m: HorizonState,
    pub h5m: HorizonState,
    pub h15m: HorizonState,
    pub ema200_1m: EmaState, // Legacy/Critical 200-period EMA on 1m
}

impl TechState {
    pub fn new() -> Self {
        Self {
            h1m: HorizonState::new(60_000),
            h5m: HorizonState::new(300_000),
            h15m: HorizonState::new(900_000),
            ema200_1m: EmaState::new(200),
        }
    }

    pub fn update(&mut self, ts: i64, mid_price: f64) {
        if self.h1m.update(ts, mid_price) {
            if let Some(c) = self.h1m.builder.current_candle_start {
                // Update EMA on 1m close
                self.ema200_1m.update(mid_price);
            }
        }
        self.h5m.update(ts, mid_price);
        self.h15m.update(ts, mid_price);
    }

    pub fn compute(&self, mid_price: f64) -> TechFeatures {
        let ema200_distance_pct = if self.ema200_1m.is_ready() {
            self.ema200_1m.get().map(|ema| {
                if ema > 0.0 { (mid_price - ema) / ema * 100.0 } else { 0.0 }
            })
        } else {
            None
        };

        TechFeatures {
            ema200_distance_pct,
            rsi_1m: self.h1m.rsi14.get(),
            bb_width_1m: self.h1m.bb20.width(),
            bb_pos_1m: self.h1m.bb20.position(mid_price),
            
            rsi_5m: self.h5m.rsi14.get(),
            bb_width_5m: self.h5m.bb20.width(),
            bb_pos_5m: self.h5m.bb20.position(mid_price),
            
            rsi_15m: self.h15m.rsi14.get(),
            bb_width_15m: self.h15m.bb20.width(),
            bb_pos_15m: self.h15m.bb20.position(mid_price),
        }
    }
}

#[derive(Debug, Clone)]
pub struct TechFeatures {
    pub ema200_distance_pct: Option<f64>,
    pub rsi_1m: Option<f64>,
    pub bb_width_1m: Option<f64>,
    pub bb_pos_1m: Option<f64>,
    pub rsi_5m: Option<f64>,
    pub bb_width_5m: Option<f64>,
    pub bb_pos_5m: Option<f64>,
    pub rsi_15m: Option<f64>,
    pub bb_width_15m: Option<f64>,
    pub bb_pos_15m: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_tech_state_multi_horizon() {
        let mut tech = TechState::new();
        
        // Feed 1 minute (60s) of data
        for i in 1..=60 {
            tech.update(i * 1000, 100.0 + (i as f64));
        }
        
        let f1 = tech.compute(160.0);
        // After 1 minute, 1 candle has closed for h1m. 
        // Bollinger needs 20 candles.
        assert!(f1.bb_width_1m.is_none(), "1m BB should not be ready after 1 minute");

        // Feed 21 minutes of data
        for i in 61..=(21 * 60) {
            tech.update(i * 1000, 100.0 + (i as f64));
        }

        let f2 = tech.compute(100.0 + (21.0 * 60.0));
        assert!(f2.bb_width_1m.is_some(), "1m BB should be ready after 20+ minutes");
        assert!(f2.rsi_1m.is_some(), "1m RSI should be ready after 14+ minutes");
        
        // h5m needs 20 * 5 = 100 minutes
        assert!(f2.bb_width_5m.is_none(), "5m BB should not be ready after 21 minutes");
    }
}
