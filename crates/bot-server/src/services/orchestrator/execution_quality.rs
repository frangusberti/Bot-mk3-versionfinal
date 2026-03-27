use serde::{Deserialize, Serialize};
use bot_data::features_v2::schema::FeatureRow;
use bot_data::orderbook::engine::OrderBookStatus;

// ════════════════════════════════════════════════════════════════════════
//  Execution Quality Block (Sprint 3 / Phase 2)
// ════════════════════════════════════════════════════════════════════════

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ExecutionQualityConfig {
    pub max_spread_bps_for_entry: f64,
}

impl Default for ExecutionQualityConfig {
    fn default() -> Self {
        Self {
            max_spread_bps_for_entry: 5.0,
        }
    }
}

pub struct ExecutionQualityResult {
    pub score: f64,
    pub is_tradeable: bool,
    pub enforce_maker_only: bool,
    pub primary_reason: String,
}

pub struct ExecutionQualityBlock;

impl ExecutionQualityBlock {
    pub fn evaluate(
        features: &FeatureRow,
        obs_quality: f64,
        ob_status: &OrderBookStatus,
        config: &ExecutionQualityConfig,
    ) -> ExecutionQualityResult {
        let mut score = 1.0;
        let mut primary_reason = "OK".to_string();

        // 1. Hard Check: Spread
        if let Some(spread) = features.spread_bps {
            if spread > config.max_spread_bps_for_entry {
                return ExecutionQualityResult {
                    score: 0.0,
                    is_tradeable: false,
                    enforce_maker_only: true, // Safe fallback
                    primary_reason: "SpreadBps_TooWide".to_string(),
                };
            }
        }

        // 2. Compute Score
        if let Some(spread_baseline) = features.spread_vs_baseline {
            if spread_baseline > 1.5 {
                score -= 0.2;
                if score < 1.0 { primary_reason = "SpreadVBase_High".to_string(); }
            }
        }

        if obs_quality < 0.8 {
            score -= 0.8 - obs_quality;
            primary_reason = "Low_Obs_Quality".to_string();
        }

        // Depth deterioration persistence indicators (0.0 to 1.0)
        let dd_bid = features.depth_deterioration_bid.unwrap_or(0.0);
        let dd_ask = features.depth_deterioration_ask.unwrap_or(0.0);
        let max_dd = dd_bid.max(dd_ask);
        if max_dd > 0.5 {
            score -= 0.3;
            primary_reason = if dd_bid > dd_ask { "DepthDrain_Bid".to_string() } else { "DepthDrain_Ask".to_string() };
        }

        // Regime Penalty
        if let Some(shock) = features.regime_shock {
            if shock > 0.5 {
                score -= 0.4;
                primary_reason = "Regime_Shock".to_string();
            }
        }

        score = score.clamp(0.0, 1.0);

        // 3. Veto Gates
        let is_tradeable = score >= 0.3 && *ob_status == OrderBookStatus::InSync;
        let enforce_maker_only = score < 0.6;

        if !is_tradeable && primary_reason == "OK" {
            primary_reason = "ExecQual_TooLow".to_string();
        }

        if *ob_status != OrderBookStatus::InSync {
            primary_reason = format!("OB_Status_{:?}", ob_status);
        }

        ExecutionQualityResult {
            score,
            is_tradeable,
            enforce_maker_only,
            primary_reason,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_execution_quality_spread_veto() {
        let mut features = FeatureRow::default();
        features.spread_bps = Some(10.0); // > 5.0 max spread

        let cfg = ExecutionQualityConfig {
            max_spread_bps_for_entry: 5.0,
        };

        let ob_status = OrderBookStatus::InSync;
        let result = ExecutionQualityBlock::evaluate(&features, 1.0, &ob_status, &cfg);

        assert_eq!(result.score, 0.0);
        assert!(!result.is_tradeable);
        assert!(result.enforce_maker_only);
        assert_eq!(result.primary_reason, "SpreadBps_TooWide");
    }

    #[test]
    fn test_execution_quality_shock_penalty() {
        let mut features = FeatureRow::default();
        features.spread_bps = Some(2.0);
        features.regime_shock = Some(1.0); // Shock > 0.5 -> score -= 0.4

        let cfg = ExecutionQualityConfig::default();
        let ob_status = OrderBookStatus::InSync;
        let result = ExecutionQualityBlock::evaluate(&features, 1.0, &ob_status, &cfg);

        // Max is 1.0 -> -0.4 = 0.6
        // is_tradeable = true (score >= 0.3)
        // enforce_maker_only = false (score >= 0.6)
        assert_eq!(result.score, 0.6);
        assert!(result.is_tradeable);
        assert!(!result.enforce_maker_only);
        assert_eq!(result.primary_reason, "Regime_Shock");
    }
}
