use serde::{Deserialize, Serialize};
use bot_data::features_v2::schema::FeatureRow;

// ════════════════════════════════════════════════════════════════════════
//  Setup-Aware Stop Policy Block (Sprint 3 / Phase 5)
// ════════════════════════════════════════════════════════════════════════

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct StopPolicyConfig {
    pub regime_trend_vol_mult: f64,
    pub regime_range_vol_mult: f64,
    pub regime_shock_vol_mult: f64,
    pub max_allowed_sl_bps: f64,
}

impl Default for StopPolicyConfig {
    fn default() -> Self {
        Self {
            regime_trend_vol_mult: 1.2,
            regime_range_vol_mult: 0.8,
            regime_shock_vol_mult: 2.0,
            max_allowed_sl_bps: 150.0,
        }
    }
}

pub struct StopPolicyResult {
    pub sl_dist_bps: f64,
    pub is_valid: bool,
}

pub struct StopPolicyBlock;

impl StopPolicyBlock {
    pub fn compute_stop(
        features: &FeatureRow,
        regime_scores: (f64, f64, f64, f64),
        cfg: &StopPolicyConfig,
    ) -> StopPolicyResult {
        // approximate RV 5m to bps (standard dev return * 10k)
        let rv_5m_bps = features.rv_5m.unwrap_or(0.001) * 10000.0;
        let volatility_proxy = rv_5m_bps.max(10.0); // Minimum assumed vol 10 bps
        
        let spread_bps = features.spread_bps.unwrap_or(2.0);

        let (tre, ran, sho, dea) = regime_scores;

        let regime_vol_mult = if sho > tre && sho > ran && sho > dea {
            cfg.regime_shock_vol_mult
        } else if ran > tre && ran > dea {
            cfg.regime_range_vol_mult
        } else {
            cfg.regime_trend_vol_mult
        };

        let sl_dist_bps = (volatility_proxy * regime_vol_mult) + (spread_bps * 1.5);
        let is_valid = sl_dist_bps <= cfg.max_allowed_sl_bps;

        StopPolicyResult {
            sl_dist_bps,
            is_valid,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_stop_policy_veto() {
        let mut f = FeatureRow::default();
        f.rv_5m = Some(0.015); // 1.5% -> 150 bps
        f.spread_bps = Some(10.0);

        let cfg = StopPolicyConfig::default(); // max 150 bps stop allowed
        
        // Under shock: 150 bps * 2.0 + 15 = 315 bps stop -> Invalid
        let res = StopPolicyBlock::compute_stop(&f, (0.1, 0.1, 0.9, 0.1), &cfg);
        
        assert_eq!(res.sl_dist_bps, 315.0);
        assert!(!res.is_valid);
    }
}
