use super::{
    Observation, StrategyAction, StrategyContext, Strategy,
    OrderIntent, Urgency,
};
use serde::{Serialize, Deserialize};

/// Configuration for the Professional Rule-Based Baseline Strategy.
/// Calibrated for Microstructure Scalping.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RulePolicyConfig {
    /// OBI (Order Book Imbalance) barrier to trigger entry.
    pub obi_threshold: f64,              // default 0.15
    /// Microprice vs Mid price bias (bps)
    pub micro_bias_threshold_bps: f64,   // default 0.0 (strict sign)
    /// Flow imbalance (taker_buy - taker_sell)
    pub flow_vol_imbalance_threshold: f64, // default 0.0 (strict sign)
    /// Max relative spread to allow entry.
    pub spread_threshold_bps: f64,       // default 2.0
    
    // Exit Parameters
    pub tp_bps: f64,                     // default 4.0
    pub sl_bps: f64,                     // default 3.0
    pub max_holding_s: i64,              // default 20
    
    // Sizing
    pub qty_frac: f64,                   // default 0.10
}

impl Default for RulePolicyConfig {
    fn default() -> Self {
        Self {
            obi_threshold: 0.15,
            micro_bias_threshold_bps: 0.0,
            flow_vol_imbalance_threshold: 0.0,
            spread_threshold_bps: 2.5,
            tp_bps: 4.0,
            sl_bps: 3.0,
            max_holding_s: 20,
            qty_frac: 0.10,
        }
    }
}

/// A Rule-Based Microstructure Strategy Baseline.
/// Implements Logic: 
///   Long: imbalance > 0.15 AND micro > mid AND buy_vol > sell_vol AND spread < threshold
///   Short: imbalance < -0.15 AND micro < mid AND sell_vol > buy_vol AND spread < threshold
pub struct RulePolicyBaseline {
    pub config: RulePolicyConfig,
}

impl RulePolicyBaseline {
    pub fn new(config: RulePolicyConfig) -> Self {
        Self { config }
    }

    /// Primary Signal Computation: returns (long_score, short_score) in [0, 1] range.
    pub fn compute_signal(&self, obs: &Observation) -> (f64, f64) {
        let f = &obs.features;
        
        let imbalance = f.obi_top1.unwrap_or(0.0);
        let micro = f.microprice.unwrap_or(0.0);
        let mid = f.mid_price.unwrap_or(0.0);
        let buy_v = f.taker_buy_vol_5s.unwrap_or(0.0);
        let sell_v = f.taker_sell_vol_5s.unwrap_or(0.0);
        let spread = f.spread_bps.unwrap_or(999.0);

        // Long Signal
        let mut l_score = 0.0;
        if imbalance > self.config.obi_threshold 
            && micro > mid 
            && buy_v > sell_v 
            && spread < self.config.spread_threshold_bps 
        {
            l_score = 1.0;
        }

        // Short Signal
        let mut s_score = 0.0;
        if imbalance < -self.config.obi_threshold 
            && micro < mid 
            && sell_v > buy_v 
            && spread < self.config.spread_threshold_bps 
        {
            s_score = 1.0;
        }

        (l_score, s_score)
    }

    fn check_exit(&self, obs: &Observation) -> Option<StrategyAction> {
        let pos = &obs.position;
        // latent_pnl_pct is decimal (e.g. 0.0001 = 1bp)
        let pnl_bps = pos.latent_pnl_pct * 10000.0;
        
        // 1. Take Profit
        if pnl_bps >= self.config.tp_bps {
            return Some(StrategyAction::Exit {
                qty_frac: 1.0,
                intent: OrderIntent::TakeProfit,
                urgency: Urgency::Normal,
                reason: format!("tp:{:.1}bps", pnl_bps),
            });
        }
        
        // 2. Stop Loss
        if pnl_bps <= -self.config.sl_bps {
            return Some(StrategyAction::Exit {
                qty_frac: 1.0,
                intent: OrderIntent::StopLoss,
                urgency: Urgency::High,
                reason: format!("sl:{:.1}bps", pnl_bps),
            });
        }
        
        // 3. Max Holding Time
        if pos.holding_ms > self.config.max_holding_s * 1000 {
            return Some(StrategyAction::Exit {
                qty_frac: 1.0,
                intent: OrderIntent::RiskFlatten,
                urgency: Urgency::Normal,
                reason: format!("time_expiry:{}s", self.config.max_holding_s),
            });
        }
        
        None
    }

    /// Decisions for the RL Environment (Teacher Mode).
    /// Returns the integer action index (0-6).
    pub fn generate_teacher_action(&mut self, obs: &Observation) -> i32 {
        let mut ctx = StrategyContext { symbol: obs.symbol.clone() };
        let action = self.on_observation(obs, &mut ctx);
        
        match action {
            StrategyAction::Flat { .. } => 0,      // HOLD
            StrategyAction::EnterLong { .. } => 1,  // POST_BID
            StrategyAction::EnterShort { .. } => 3, // POST_ASK
            StrategyAction::Exit { .. } => 6,      // TAKER_EXIT
        }
    }
}

impl Strategy for RulePolicyBaseline {
    fn name(&self) -> &str { "rule_policy_baseline_v1" }

    fn on_observation(&mut self, obs: &Observation, _ctx: &mut StrategyContext) -> StrategyAction {
        // 1. If in position, manage exits
        if !obs.is_flat() {
            if let Some(exit) = self.check_exit(obs) {
                return exit;
            }
            return StrategyAction::Flat { reason: "holding".to_string() };
        }

        // 2. Flat state: check entries
        let (l, s) = self.compute_signal(obs);
        
        if l > 0.5 {
            return StrategyAction::EnterLong {
                qty_frac: self.config.qty_frac,
                intent: OrderIntent::Entry,
                urgency: Urgency::Normal,
                reason: "rule_long:imbalance+micro+flow".to_string(),
            };
        }

        if s > 0.5 {
            return StrategyAction::EnterShort {
                qty_frac: self.config.qty_frac,
                intent: OrderIntent::Entry,
                urgency: Urgency::Normal,
                reason: "rule_short:imbalance+micro+flow".to_string(),
            };
        }

        StrategyAction::Flat { reason: "no_signal".to_string() }
    }

    fn reset(&mut self) {}
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::features_v2::schema::FeatureRow;
    use crate::strategy::{AccountSnapshot, PositionSnapshot, Observation};

    fn make_obs(obi: f64, micro_diff: f64, buy_v: f64, sell_v: f64, spread: f64) -> Observation {
        let mut f = FeatureRow::default();
        f.mid_price = Some(100.0);
        f.microprice = Some(100.0 + micro_diff);
        f.obi_top1 = Some(obi);
        f.taker_buy_vol_5s = Some(buy_v);
        f.taker_sell_vol_5s = Some(sell_v);
        f.spread_bps = Some(spread);
        
        Observation {
            ts: 1000,
            symbol: "BTCUSDT".to_string(),
            features: f,
            account: AccountSnapshot::default(),
            position: PositionSnapshot::default(),
        }
    }

    #[test]
    fn test_baseline_long_trigger() {
        let mut s = RulePolicyBaseline::new(RulePolicyConfig::default());
        let obs = make_obs(0.20, 0.01, 1000.0, 500.0, 1.0);
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let act = s.on_observation(&obs, &mut ctx);
        assert!(matches!(act, StrategyAction::EnterLong { .. }));
    }

    #[test]
    fn test_baseline_short_trigger() {
        let mut s = RulePolicyBaseline::new(RulePolicyConfig::default());
        let obs = make_obs(-0.20, -0.01, 500.0, 1000.0, 1.0);
        let mut ctx = StrategyContext { symbol: "BTCUSDT".to_string() };
        let act = s.on_observation(&obs, &mut ctx);
        assert!(matches!(act, StrategyAction::EnterShort { .. }));
    }
}
