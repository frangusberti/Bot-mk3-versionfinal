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
pub struct MeanReversionV2Config {
    // Regime gate
    pub max_bb_width: f64,         // default 0.02 (tight bands = ranging)
    pub max_rv_5m: f64,            // default 0.003

    // Entry thresholds
    pub bb_pos_long: f64,          // default 0.10 (oversold)
    pub bb_pos_short: f64,         // default 0.90 (overbought)
    pub rsi_long: f64,             // default 30.0
    pub rsi_short: f64,            // default 70.0
    pub max_spread_bps: f64,       // default 2.0

    // Exit thresholds
    pub bb_pos_target: f64,        // default 0.50 (mean / mid-band)
    pub k_stop: f64,               // ATR multiplier for stop (default 1.5)

    // Sizing
    pub qty_frac: f64,             // default 0.08
}

impl Default for MeanReversionV2Config {
    fn default() -> Self {
        Self {
            max_bb_width: 0.02,
            max_rv_5m: 0.003,
            bb_pos_long: 0.10,
            bb_pos_short: 0.90,
            rsi_long: 30.0,
            rsi_short: 70.0,
            max_spread_bps: 2.0,
            bb_pos_target: 0.50,
            k_stop: 1.5,
            qty_frac: 0.08,
        }
    }
}

// ============================================================================
//  Strategy
// ============================================================================

pub struct MeanReversionV2Strategy {
    pub config: MeanReversionV2Config,
}

impl MeanReversionV2Strategy {
    pub fn new(config: MeanReversionV2Config) -> Self {
        Self { config }
    }

    fn atr_proxy(&self, obs: &Observation) -> f64 {
        let rv = obs.features.rv_30s.unwrap_or(0.001);
        let mid = obs.mid_price();
        rv * mid * 30.0_f64.sqrt()
    }

    fn is_ranging_regime(&self, obs: &Observation) -> bool {
        let bb_w = obs.features.bb_width_1m.unwrap_or(1.0);
        let rv5m = obs.features.rv_5m.unwrap_or(1.0);
        bb_w < self.config.max_bb_width && rv5m < self.config.max_rv_5m
    }
}

impl Strategy for MeanReversionV2Strategy {
    fn name(&self) -> &str { "mean_reversion_v2" }

    fn on_observation(&mut self, obs: &Observation, _ctx: &mut StrategyContext) -> StrategyAction {
        let f = &obs.features;

        // ── Position management (exit checks) ──
        if !obs.is_flat() {
            let atr = self.atr_proxy(obs);
            let pnl_per_unit = if obs.is_long() {
                obs.mid_price() - obs.position.entry_price
            } else {
                obs.position.entry_price - obs.mid_price()
            };

            // Stop loss
            let stop_dist = self.config.k_stop * atr;
            if pnl_per_unit < -stop_dist {
                return StrategyAction::Exit {
                    qty_frac: 1.0,
                    intent: OrderIntent::StopLoss,
                    urgency: Urgency::High,
                    reason: format!("mr_stop:pnl={:.4},atr={:.4}", pnl_per_unit, atr),
                };
            }

            // Target: mean reversion to BB mid
            let bb_pos = f.bb_pos_1m.unwrap_or(0.5);
            let target_hit = if obs.is_long() {
                bb_pos >= self.config.bb_pos_target
            } else {
                bb_pos <= (1.0 - self.config.bb_pos_target)
            };

            if target_hit {
                return StrategyAction::Exit {
                    qty_frac: 1.0,
                    intent: OrderIntent::TakeProfit,
                    urgency: Urgency::Normal,
                    reason: format!("mr_target:bb_pos={:.2}", bb_pos),
                };
            }

            return StrategyAction::Flat { reason: "mr_holding".to_string() };
        }

        // ── Regime gate: only trade in ranging markets ──
        if !self.is_ranging_regime(obs) {
            return StrategyAction::Flat { reason: "mr_trending_regime".to_string() };
        }

        let bb_pos = f.bb_pos_1m.unwrap_or(0.5);
        let rsi = f.rsi_1m.unwrap_or(50.0);
        let spread = f.spread_bps.unwrap_or(100.0);

        // Spread gate
        if spread >= self.config.max_spread_bps {
            return StrategyAction::Flat { reason: "mr_spread_wide".to_string() };
        }

        // ── Entry: Long at oversold ──
        if bb_pos < self.config.bb_pos_long && rsi < self.config.rsi_long {
            let reason = format!("mr_entry_long:bb_pos={:.2},rsi={:.1}", bb_pos, rsi);
            info!("[mean_reversion_v2] {}", reason);
            return StrategyAction::EnterLong {
                qty_frac: self.config.qty_frac,
                intent: OrderIntent::Entry,
                urgency: Urgency::Normal,
                reason,
            };
        }

        // ── Entry: Short at overbought ──
        if bb_pos > self.config.bb_pos_short && rsi > self.config.rsi_short {
            let reason = format!("mr_entry_short:bb_pos={:.2},rsi={:.1}", bb_pos, rsi);
            info!("[mean_reversion_v2] {}", reason);
            return StrategyAction::EnterShort {
                qty_frac: self.config.qty_frac,
                intent: OrderIntent::Entry,
                urgency: Urgency::Normal,
                reason,
            };
        }

        StrategyAction::Flat { reason: "mr_no_signal".to_string() }
    }

    fn reset(&mut self) {
        // No internal state to reset
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

    fn make_obs(bb_w: f64, rv5m: f64, bb_pos: f64, rsi: f64, spread: f64) -> Observation {
        let mut f = FeatureRow::default();
        f.mid_price = Some(100.0);
        f.bb_width_1m = Some(bb_w);
        f.rv_5m = Some(rv5m);
        f.bb_pos_1m = Some(bb_pos);
        f.rsi_1m = Some(rsi);
        f.spread_bps = Some(spread);
        f.rv_30s = Some(0.001);
        Observation {
            ts: 1000,
            symbol: "BTCUSDT".to_string(),
            features: f,
            account: AccountSnapshot { equity: 10000.0, ..Default::default() },
            position: PositionSnapshot::default(),
        }
    }

    #[test]
    fn test_entry_long_oversold() {
        let mut s = MeanReversionV2Strategy::new(MeanReversionV2Config::default());
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let obs = make_obs(0.01, 0.001, 0.05, 25.0, 1.0);
        let action = s.on_observation(&obs, &mut ctx);
        assert!(matches!(action, StrategyAction::EnterLong { .. }));
    }

    #[test]
    fn test_entry_short_overbought() {
        let mut s = MeanReversionV2Strategy::new(MeanReversionV2Config::default());
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let obs = make_obs(0.01, 0.001, 0.95, 75.0, 1.0);
        let action = s.on_observation(&obs, &mut ctx);
        assert!(matches!(action, StrategyAction::EnterShort { .. }));
    }

    #[test]
    fn test_no_entry_trending_regime() {
        let mut s = MeanReversionV2Strategy::new(MeanReversionV2Config::default());
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let obs = make_obs(0.05, 0.001, 0.05, 25.0, 1.0); // bb_width=0.05 > 0.02
        let action = s.on_observation(&obs, &mut ctx);
        assert!(action.is_flat());
    }

    #[test]
    fn test_exit_at_target() {
        let mut s = MeanReversionV2Strategy::new(MeanReversionV2Config::default());
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let mut f = FeatureRow::default();
        f.mid_price = Some(101.0);
        f.bb_pos_1m = Some(0.55); // past target of 0.50
        f.rv_30s = Some(0.001);
        let obs = Observation {
            ts: 2000,
            symbol: "BTCUSDT".to_string(),
            features: f,
            account: AccountSnapshot { equity: 10000.0, ..Default::default() },
            position: PositionSnapshot {
                qty: 1.0, entry_price: 99.0,
                unrealized_pnl: 2.0, latent_pnl_pct: 2.0,
                max_pnl_pct: 2.0, holding_ms: 5000,
            },
        };
        let action = s.on_observation(&obs, &mut ctx);
        assert!(matches!(action, StrategyAction::Exit { intent: OrderIntent::TakeProfit, .. }));
    }
}
