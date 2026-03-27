use serde::{Deserialize, Serialize};
use crate::services::orchestrator::risk::RiskConfig;
use crate::services::orchestrator::execution_quality::ExecutionQualityResult;

// ════════════════════════════════════════════════════════════════════════
//  Dynamic Position Sizing Block (Sprint 3 / Phase 4)
// ════════════════════════════════════════════════════════════════════════

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DynamicSizingConfig {
    pub regime_trend_mult: f64,
    pub regime_range_mult: f64,
    pub regime_shock_mult: f64,
    pub regime_dead_mult: f64,
    pub exec_qual_floor_mult: f64,
    pub min_confidence: f64,
}

impl Default for DynamicSizingConfig {
    fn default() -> Self {
        Self {
            regime_trend_mult: 1.0,
            regime_range_mult: 0.75,
            regime_shock_mult: 0.30,
            regime_dead_mult: 0.00,
            exec_qual_floor_mult: 0.50,
            min_confidence: 0.50,
        }
    }
}

#[derive(Debug, Clone)]
pub struct DynamicSizingResult {
    pub target_qty: f64,
    pub target_notional: f64,
    pub regime_mult: f64,
    pub base_size: f64,
}

pub struct DynamicSizingBlock;

impl DynamicSizingBlock {
    #[allow(clippy::too_many_arguments)]
    pub fn compute_size(
        equity: f64,
        current_mid: f64,
        confidence: f64,
        regime_scores: (f64, f64, f64, f64), // Trend, Range, Shock, Dead
        exec_qual: &ExecutionQualityResult,
        cfg: &DynamicSizingConfig,
        risk_cfg: &RiskConfig,
        effective_max_frac: f64,
        effective_lev: f64,
    ) -> Option<DynamicSizingResult> {
        let margin_budget = equity * effective_max_frac;
        let base_notional = margin_budget * effective_lev;

        // Find max regime
        let (tre, ran, sho, dea) = regime_scores;
        let regime_mult = if sho > tre && sho > ran && sho > dea {
            cfg.regime_shock_mult
        } else if dea > tre && dea > ran {
            cfg.regime_dead_mult
        } else if ran > tre {
            cfg.regime_range_mult
        } else {
            cfg.regime_trend_mult
        };

        let exec_qual_mult = exec_qual.score.max(cfg.exec_qual_floor_mult);
        let conf_mult = if confidence > cfg.min_confidence { 1.0 } else { 0.0 };
        
        // Soft veto penalty
        let setup_mult = if exec_qual.enforce_maker_only { 0.5 } else { 1.0 };

        let mut target_notional = base_notional * regime_mult * exec_qual_mult * conf_mult * setup_mult;
        
        if target_notional == 0.0 {
            return None; // NoTrade
        }

        target_notional = target_notional.clamp(risk_cfg.min_notional_per_order, risk_cfg.max_notional_per_order);

        if target_notional < (risk_cfg.min_notional_per_order / 2.0) {
            return None; // NoTrade
        }

        Some(DynamicSizingResult {
            target_qty: target_notional / current_mid,
            target_notional,
            regime_mult,
            base_size: base_notional,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_dynamic_sizing_clamping() {
        let risk_cfg = RiskConfig {
            min_notional_per_order: 10.0,
            max_notional_per_order: 1000.0,
            ..RiskConfig::default()
        };
        let cfg = DynamicSizingConfig::default();
        let exec_qual = ExecutionQualityResult {
            score: 0.1, // very low
            is_tradeable: false,
            enforce_maker_only: true, // setup_mult = 0.5
            primary_reason: "Shock".into(),
        };

        // Base notional = 100 (equity) * 1.0 (frac) * 1.0 (lev) = 100.0
        // Expected target:
        // regime_mult (Shock) = 0.30
        // exec_mult = max(0.1, 0.5) = 0.5
        // conf_mult = (0.4 > 0.5) -> 0.0
        // setup_mult = 0.5
        // Target = 100 * 0.3 * 0.5 * 0.0 * 0.5 = 0.0 -> returns None
        // Min order is 10.0.
        
        let res = DynamicSizingBlock::compute_size(
            100.0,
            1.0,
            0.4,
            (0.1, 0.1, 0.9, 0.1), // Shock dominant
            &exec_qual,
            &cfg,
            &risk_cfg,
            1.0,
            1.0,
        );

        assert!(res.is_none(), "Should be NoTrade because confidence is low, and target is 0.0");
    }
}
