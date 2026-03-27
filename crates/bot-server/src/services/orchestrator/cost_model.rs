use serde::{Deserialize, Serialize};
use crate::services::orchestrator::commission::{OrderIntent, CommissionPolicyConfig};
use bot_data::features_v2::schema::FeatureRow;

// ════════════════════════════════════════════════════════════════════════
//  Trade Cost Model Block (Sprint 3 / Phase 2)
// ════════════════════════════════════════════════════════════════════════

#[derive(Debug, Clone, PartialEq)]
pub enum EntryMode { Maker, Taker, NoTrade }

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum ValueMappingMode {
    LegacyRaw,
    ScaledX10000,
    BaselineOnly,
}

impl Default for ValueMappingMode {
    fn default() -> Self {
        ValueMappingMode::BaselineOnly
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CostModelConfig {
    pub edge_threshold_bps: f64,
    pub slippage_factor: f64,
    pub value_mapping_mode: ValueMappingMode,
    pub value_to_bps_multiplier: f64,
}

impl Default for CostModelConfig {
    fn default() -> Self {
        Self {
            edge_threshold_bps: 2.0,
            slippage_factor: 10.0,
            value_mapping_mode: ValueMappingMode::BaselineOnly,
            value_to_bps_multiplier: 1.0, 
        }
    }
}

pub struct ExpectedTradeCost {
    pub fee_bps_est: f64,
    pub spread_cost_bps_est: f64,
    pub slippage_bps_est: f64,
    pub adverse_selection_bps_est: f64,
    pub total_cost_bps_est: f64,
    pub notes: String,
}

pub struct ExpectedNetEdge {
    pub expected_move_bps: f64, // Keep for legacy
    pub total_cost_bps: f64,
    pub net_edge_bps: f64,
    pub passes_threshold: bool,
    
    // Explicit Tracking Fields
    pub raw_model_value: f64,
    pub baseline_move_bps: f64,
    pub expected_move_bps_used: f64,
    pub cost_gate_mode: String,
}

pub struct CostModelBlock;

impl CostModelBlock {
    pub fn estimate_cost(
        _intent: &OrderIntent,
        mode: &EntryMode,
        expected_size: f64,
        features: &FeatureRow,
        commission_cfg: &CommissionPolicyConfig,
    ) -> ExpectedTradeCost {
        let spread_bps = features.spread_bps.unwrap_or(2.0);
        let notes = String::new();

        let fee_bps_est = match mode {
            EntryMode::Taker => commission_cfg.taker_fee_bps,
            EntryMode::Maker | EntryMode::NoTrade => commission_cfg.maker_fee_bps,
        };

        let spread_cost_bps_est = match mode {
            EntryMode::Taker => spread_bps / 2.0,
            EntryMode::Maker | EntryMode::NoTrade => 0.0, // Conservative: don't assume we capture negative spread
        };

        let slippage_bps_est = match mode {
            EntryMode::Taker => {
                // Approximate depth proxy; fallback model
                let slip = (expected_size / 10.0) * spread_bps * 0.1;
                slip.clamp(0.0, 10.0)
            },
            EntryMode::Maker | EntryMode::NoTrade => 0.0,
        };

        let adverse_selection_bps_est = match mode {
            EntryMode::Taker => {
                let shock = features.regime_shock.unwrap_or(0.0);
                if shock > 0.5 { 5.0 } else { 1.0 }
            },
            EntryMode::Maker | EntryMode::NoTrade => (spread_bps / 2.0) + 1.0, // Penalty for adverse queue selection
        };

        let total_cost_bps_est = fee_bps_est + spread_cost_bps_est + slippage_bps_est + adverse_selection_bps_est;

        ExpectedTradeCost {
            fee_bps_est,
            spread_cost_bps_est,
            slippage_bps_est,
            adverse_selection_bps_est,
            total_cost_bps_est,
            notes,
        }
    }

    pub fn check_edge(
        raw_model_value: f64,
        cost: &ExpectedTradeCost,
        cfg: &CostModelConfig,
        baseline_move_bps: f64,
    ) -> ExpectedNetEdge {
        
        let mut expected_move_bps_used = match cfg.value_mapping_mode {
            ValueMappingMode::LegacyRaw => raw_model_value * cfg.value_to_bps_multiplier,
            ValueMappingMode::ScaledX10000 => raw_model_value * 10000.0,
            ValueMappingMode::BaselineOnly => baseline_move_bps,
        };

        // Fallback to explicitly defined regime baseline if uncalibrated/negative
        if cfg.value_mapping_mode != ValueMappingMode::BaselineOnly && expected_move_bps_used <= 0.0 {
            expected_move_bps_used = baseline_move_bps; 
        }
        
        // Edge calculation must use the mapped scale
        let net_edge_bps = expected_move_bps_used - cost.total_cost_bps_est;
        let passes_threshold = net_edge_bps > cfg.edge_threshold_bps;

        ExpectedNetEdge {
            expected_move_bps: expected_move_bps_used,
            total_cost_bps: cost.total_cost_bps_est,
            net_edge_bps,
            passes_threshold,
            raw_model_value,
            baseline_move_bps,
            expected_move_bps_used,
            cost_gate_mode: format!("{:?}", cfg.value_mapping_mode),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cost_model_spread_symmetric() {
        let mut features = FeatureRow::default();
        features.spread_bps = Some(10.0); // Wide spread

        let comm_cfg = CommissionPolicyConfig {
            taker_fee_bps: 5.0,
            maker_fee_bps: 2.0,
            maker_entry_offset_bps: 1.0,
            ..CommissionPolicyConfig::default()
        };

        // Test Taker
        let intent = OrderIntent::Entry;
        let taker_cost = CostModelBlock::estimate_cost(
            &intent,
            &EntryMode::Taker,
            100.0,
            &features,
            &comm_cfg,
        );

        assert_eq!(taker_cost.fee_bps_est, 5.0);
        assert_eq!(taker_cost.spread_cost_bps_est, 5.0); // 10.0 / 2
        assert_eq!(taker_cost.adverse_selection_bps_est, 1.0); // Baseline
        assert!(taker_cost.total_cost_bps_est > 11.0);

        // Test Maker
        let maker_cost = CostModelBlock::estimate_cost(
            &intent,
            &EntryMode::Maker,
            100.0,
            &features,
            &comm_cfg,
        );

        assert_eq!(maker_cost.fee_bps_est, 2.0);
        assert_eq!(maker_cost.spread_cost_bps_est, 0.0);
        assert_eq!(maker_cost.adverse_selection_bps_est, 6.0); // (10.0/2) + 1.0
        assert_eq!(maker_cost.total_cost_bps_est, 8.0);
    }

    #[test]
    fn test_net_edge_veto() {
        let cost = ExpectedTradeCost {
            fee_bps_est: 5.0,
            spread_cost_bps_est: 5.0,
            slippage_bps_est: 2.0,
            adverse_selection_bps_est: 1.0,
            total_cost_bps_est: 13.0,
            notes: "".to_string(),
        };

        let cfg = CostModelConfig {
            edge_threshold_bps: 2.0,
            value_mapping_mode: ValueMappingMode::LegacyRaw,
            value_to_bps_multiplier: 1.0,
            ..CostModelConfig::default()
        };

        // Case 1: Predicted move is positive but less than costs + threshold
        // Move = 14.0 -> Net edge = 1.0 -> fails threshold (2.0)
        let edge = CostModelBlock::check_edge(14.0, &cost, &cfg, 0.0);
        assert_eq!(edge.net_edge_bps, 1.0);
        assert!(!edge.passes_threshold);

        // Case 2: Predicted move > costs + threshold
        // Move = 20.0 -> Net edge = 7.0 -> passes
        let edge2 = CostModelBlock::check_edge(20.0, &cost, &cfg, 0.0);
        assert_eq!(edge2.net_edge_bps, 7.0);
        assert!(edge2.passes_threshold);
    }
}
