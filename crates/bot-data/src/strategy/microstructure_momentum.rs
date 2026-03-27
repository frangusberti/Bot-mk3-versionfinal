use super::{
    Observation, StrategyAction, StrategyContext, Strategy,
    OrderIntent, Urgency,
};
use serde::{Serialize, Deserialize};
use log::info;

// ============================================================================
//  Configuration
// ============================================================================

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MicroMomentumConfig {
    // Entry thresholds
    pub obi_threshold: f64,           // default 0.30
    pub flow_ratio: f64,              // default 1.2
    pub max_spread_bps: f64,          // default 2.0
    pub min_rv: f64,                  // default 0.0001
    pub max_rv: f64,                  // default 0.005
    pub ema_distance_min_pct: f64,    // default -0.5

    // Exit thresholds
    pub k_stop: f64,                  // ATR multiplier for stop (default 2.0)
    pub k_tp: f64,                    // ATR multiplier for take-profit (default 3.0)
    pub trail_start_pct: f64,         // trailing activates after this % PnL (default 0.5)
    pub trail_gap_pct: f64,           // trailing gap (default 0.3)

    // Sizing
    pub qty_frac: f64,                // fraction of equity per trade (default 0.10)
}

impl Default for MicroMomentumConfig {
    fn default() -> Self {
        Self {
            obi_threshold: 0.30,
            flow_ratio: 1.2,
            max_spread_bps: 2.0,
            min_rv: 0.0001,
            max_rv: 0.005,
            ema_distance_min_pct: -0.5,
            k_stop: 2.0,
            k_tp: 3.0,
            trail_start_pct: 0.5,
            trail_gap_pct: 0.3,
            qty_frac: 0.10,
        }
    }
}

// ============================================================================
//  Strategy
// ============================================================================

pub struct MicrostructureMomentumStrategy {
    pub config: MicroMomentumConfig,
    trailing_stop: Option<f64>, // trailing stop level in % PnL
}

impl MicrostructureMomentumStrategy {
    pub fn new(config: MicroMomentumConfig) -> Self {
        Self { config, trailing_stop: None }
    }

    /// Estimate ATR-like measure from rv_30s (annualized vol -> per-second amplitude).
    /// We use rv_30s * mid_price as a simple ATR proxy in price-units.
    fn atr_proxy(&self, obs: &Observation) -> f64 {
        let rv = obs.features.rv_30s.unwrap_or(0.001);
        let mid = obs.mid_price();
        // rv_30s is already a per-tick standard deviation; scale to ~1 bar
        rv * mid * 30.0_f64.sqrt()
    }

    fn check_entry_long(&self, obs: &Observation) -> Option<String> {
        let f = &obs.features;
        let mut reasons = Vec::new();

        // 1. OBI pressure
        let obi = f.obi_top1.unwrap_or(0.0);
        if obi > self.config.obi_threshold {
            reasons.push(format!("obi={:.2}", obi));
        } else {
            return None;
        }

        // 2. Momentum
        let ret5s = f.ret_5s.unwrap_or(0.0);
        if ret5s > 0.0 {
            reasons.push(format!("ret5s={:.6}", ret5s));
        } else {
            return None;
        }

        // 3. Flow confirmation
        let buy = f.taker_buy_vol_5s.unwrap_or(0.0);
        let sell = f.taker_sell_vol_5s.unwrap_or(0.0);
        if sell > 0.0 && buy > self.config.flow_ratio * sell {
            reasons.push(format!("flow={:.1}x", buy / sell));
        } else if sell <= 0.0 && buy > 0.0 {
            reasons.push("flow=inf".to_string());
        } else {
            return None;
        }

        // 4. Spread gate
        let spread = f.spread_bps.unwrap_or(100.0);
        if spread >= self.config.max_spread_bps {
            return None;
        }

        // 5. Volatility band
        let rv = f.rv_30s.unwrap_or(0.0);
        if rv < self.config.min_rv || rv > self.config.max_rv {
            return None;
        }

        // 6. EMA regime
        let ema_dist = f.ema200_distance_pct.unwrap_or(0.0);
        if ema_dist < self.config.ema_distance_min_pct {
            return None;
        }

        Some(format!("entry_long:{}", reasons.join(",")))
    }

    fn check_entry_short(&self, obs: &Observation) -> Option<String> {
        let f = &obs.features;

        // Symmetric conditions
        let obi = f.obi_top1.unwrap_or(0.0);
        if obi >= -self.config.obi_threshold { return None; }

        let ret5s = f.ret_5s.unwrap_or(0.0);
        if ret5s >= 0.0 { return None; }

        let buy = f.taker_buy_vol_5s.unwrap_or(0.0);
        let sell = f.taker_sell_vol_5s.unwrap_or(0.0);
        if buy > 0.0 && sell <= self.config.flow_ratio * buy { return None; }

        let spread = f.spread_bps.unwrap_or(100.0);
        if spread >= self.config.max_spread_bps { return None; }

        let rv = f.rv_30s.unwrap_or(0.0);
        if rv < self.config.min_rv || rv > self.config.max_rv { return None; }

        let ema_dist = f.ema200_distance_pct.unwrap_or(0.0);
        if ema_dist > -self.config.ema_distance_min_pct { return None; }

        Some(format!("entry_short:obi={:.2},ret5s={:.6}", obi, ret5s))
    }

    fn check_exit(&mut self, obs: &Observation) -> Option<StrategyAction> {
        let atr = self.atr_proxy(obs);
        let pos = &obs.position;
        let mid = obs.mid_price();

        // Stop loss (hard)
        let stop_dist = self.config.k_stop * atr;
        let pnl_per_unit = if pos.qty > 0.0 {
            mid - pos.entry_price
        } else {
            pos.entry_price - mid
        };

        if pnl_per_unit < -stop_dist {
            self.trailing_stop = None;
            return Some(StrategyAction::Exit {
                qty_frac: 1.0,
                intent: OrderIntent::StopLoss,
                urgency: Urgency::High,
                reason: format!("stop_loss:pnl_per_unit={:.4},atr={:.4}", pnl_per_unit, atr),
            });
        }

        // Take profit
        let tp_dist = self.config.k_tp * atr;
        if pnl_per_unit > tp_dist {
            self.trailing_stop = None;
            return Some(StrategyAction::Exit {
                qty_frac: 1.0,
                intent: OrderIntent::TakeProfit,
                urgency: Urgency::Normal,
                reason: format!("take_profit:pnl_per_unit={:.4}", pnl_per_unit),
            });
        }

        // Trailing stop
        let pnl_pct = pos.latent_pnl_pct;
        let max_pnl_pct = pos.max_pnl_pct;

        if max_pnl_pct > self.config.trail_start_pct {
            let trail_level = max_pnl_pct - self.config.trail_gap_pct;
            self.trailing_stop = Some(trail_level);

            if pnl_pct < trail_level {
                self.trailing_stop = None;
                return Some(StrategyAction::Exit {
                    qty_frac: 1.0,
                    intent: OrderIntent::Exit,
                    urgency: Urgency::High,
                    reason: format!("trailing_stop:pnl={:.2}%,trail={:.2}%", pnl_pct, trail_level),
                });
            }
        }

        None
    }
}

impl Strategy for MicrostructureMomentumStrategy {
    fn name(&self) -> &str { "microstructure_momentum_v1" }

    fn on_observation(&mut self, obs: &Observation, _ctx: &mut StrategyContext) -> StrategyAction {
        // If in position, check exits first
        if !obs.is_flat() {
            if let Some(exit_action) = self.check_exit(obs) {
                info!("[{}] {}", self.name(), exit_action.reason());
                return exit_action;
            }
            // Holding — no exit triggered
            return StrategyAction::Flat { reason: "holding".to_string() };
        }

        // Flat: check entries
        if let Some(reason) = self.check_entry_long(obs) {
            info!("[{}] {}", self.name(), reason);
            return StrategyAction::EnterLong {
                qty_frac: self.config.qty_frac,
                intent: OrderIntent::Entry,
                urgency: Urgency::Normal,
                reason,
            };
        }

        if let Some(reason) = self.check_entry_short(obs) {
            info!("[{}] {}", self.name(), reason);
            return StrategyAction::EnterShort {
                qty_frac: self.config.qty_frac,
                intent: OrderIntent::Entry,
                urgency: Urgency::Normal,
                reason,
            };
        }

        StrategyAction::Flat { reason: "no_signal".to_string() }
    }

    fn reset(&mut self) {
        self.trailing_stop = None;
    }
}

// ============================================================================
//  Tests
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use crate::features_v2::schema::FeatureRow;
    use crate::strategy::{AccountSnapshot, PositionSnapshot, StrategyContext, Observation};

    fn make_obs(obi: f64, ret5s: f64, buy5s: f64, sell5s: f64, spread: f64, rv: f64, ema_dist: f64) -> Observation {
        let mut f = FeatureRow::default();
        f.mid_price = Some(100.0);
        f.obi_top1 = Some(obi);
        f.ret_5s = Some(ret5s);
        f.taker_buy_vol_5s = Some(buy5s);
        f.taker_sell_vol_5s = Some(sell5s);
        f.spread_bps = Some(spread);
        f.rv_30s = Some(rv);
        f.ema200_distance_pct = Some(ema_dist);
        Observation {
            ts: 1000,
            symbol: "BTCUSDT".to_string(),
            features: f,
            account: AccountSnapshot { equity: 10000.0, ..Default::default() },
            position: PositionSnapshot::default(),
        }
    }

    #[test]
    fn test_entry_long_all_conditions() {
        let mut s = MicrostructureMomentumStrategy::new(MicroMomentumConfig::default());
        let obs = make_obs(0.5, 0.001, 100.0, 50.0, 1.0, 0.001, 0.0);
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let action = s.on_observation(&obs, &mut ctx);
        assert!(matches!(action, StrategyAction::EnterLong { .. }));
    }

    #[test]
    fn test_no_entry_spread_too_wide() {
        let mut s = MicrostructureMomentumStrategy::new(MicroMomentumConfig::default());
        let obs = make_obs(0.5, 0.001, 100.0, 50.0, 5.0, 0.001, 0.0); // spread=5 > 2
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let action = s.on_observation(&obs, &mut ctx);
        assert!(action.is_flat());
    }

    #[test]
    fn test_no_entry_obi_insufficient() {
        let mut s = MicrostructureMomentumStrategy::new(MicroMomentumConfig::default());
        let obs = make_obs(0.1, 0.001, 100.0, 50.0, 1.0, 0.001, 0.0); // obi=0.1 < 0.30
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let action = s.on_observation(&obs, &mut ctx);
        assert!(action.is_flat());
    }

    #[test]
    fn test_stop_loss_exit() {
        let mut s = MicrostructureMomentumStrategy::new(MicroMomentumConfig::default());
        let mut f = FeatureRow::default();
        f.mid_price = Some(95.0); // price dropped from 100
        f.rv_30s = Some(0.001);
        let obs = Observation {
            ts: 2000,
            symbol: "BTCUSDT".to_string(),
            features: f,
            account: AccountSnapshot { equity: 10000.0, ..Default::default() },
            position: PositionSnapshot {
                qty: 1.0, entry_price: 100.0,
                unrealized_pnl: -5.0, latent_pnl_pct: -5.0,
                max_pnl_pct: 0.0, holding_ms: 5000,
            },
        };
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let action = s.on_observation(&obs, &mut ctx);
        assert!(matches!(action, StrategyAction::Exit { intent: OrderIntent::StopLoss, .. }));
    }
}
