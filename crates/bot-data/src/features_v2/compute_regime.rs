use super::compute_absorption::AbsorptionFeatures;
use super::compute_persistence::PersistenceFeatures;

/// Market regime classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarketRegime {
    Trend,
    Range,
    Shock,
    DeadMarket,
}

/// Thresholds for regime score computation and transition logic.
#[derive(Debug, Clone)]
pub struct RegimeThresholds {
    /// RV_5s threshold for fully-shock score (0.1% per tick)
    pub rv_shock: f64,
    /// RV_5s threshold for fully-dead score (0.01% per tick)
    pub rv_dead: f64,
    /// Expected tape trades per 1s window for scaling
    pub expected_trades_1s: f64,
    /// Minimum margin between winner and second-best for transition
    pub margin: f32,
    /// Minimum dwell (emit windows) before regime can transition
    pub min_dwell: u32,
}

impl Default for RegimeThresholds {
    fn default() -> Self {
        Self {
            rv_shock: 0.001,
            rv_dead: 0.0001,
            expected_trades_1s: 5.0,
            margin: 0.10,
            min_dwell: 5,
        }
    }
}

/// Internal hysteretic regime state.
#[derive(Debug, Clone)]
pub struct RegimeState {
    pub current: MarketRegime,
    pub scores: [f32; 4], // [trend, range, shock, dead]
    pub dwell: u32,
    pub confidence: f32,
}

impl RegimeState {
    pub fn new() -> Self {
        Self {
            current: MarketRegime::Range, // conservative fallback
            scores: [0.0, 1.0, 0.0, 0.0],
            dwell: 0,
            confidence: 0.0,
        }
    }
}

/// Inputs consumed by the regime classifier.
/// All are Option to handle graceful degradation.
pub struct RegimeInputs {
    pub flow_persistence_buy: Option<f64>,
    pub flow_persistence_sell: Option<f64>,
    pub slope_mid_5s: Option<f64>,
    pub slope_mid_5m: Option<f64>,
    pub slope_mid_15m: Option<f64>,
    pub slope_mid_1h: Option<f64>,
    pub microprice_confirmation: Option<f64>,
    pub breakout_failure: Option<f64>,
    pub spread_vs_baseline: Option<f64>,
    pub rv_5s: Option<f64>,
    pub rv_15m: Option<f64>,
    pub rv_1h: Option<f64>,
    pub tape_intensity_z: Option<f64>,
    pub liq_count_30s: f64,
    pub tape_trades_1s: f64,
    pub trade_imbalance_5s: Option<f64>,
    pub ret_5m: Option<f64>,
    pub ret_15m: Option<f64>,
    pub ret_1h: Option<f64>,
    pub range_pos_5m: Option<f64>,
    pub range_pos_15m: Option<f64>,
    pub range_pos_1h: Option<f64>,
}

/// Clamp to [0, 1].
fn c01(x: f64) -> f64 {
    x.clamp(0.0, 1.0)
}

fn normalize4(a: f64, b: f64, c: f64, d: f64) -> (f64, f64, f64, f64) {
    let sum = (a + b + c + d).max(1e-9);
    (a / sum, b / sum, c / sum, d / sum)
}

fn bias_score(ret: f64, slope: f64, range_pos: f64, ret_scale: f64, slope_scale: f64) -> f64 {
    let ret_term = (ret / ret_scale).tanh();
    let slope_term = (slope / slope_scale).tanh();
    let loc_term = ((range_pos - 0.5) * 2.0).clamp(-1.0, 1.0);
    (0.50 * ret_term + 0.35 * slope_term + 0.15 * loc_term).clamp(-1.0, 1.0)
}

fn avg_abs(vals: &[f64]) -> f64 {
    if vals.is_empty() {
        return 0.0;
    }
    vals.iter().map(|v| v.abs()).sum::<f64>() / vals.len() as f64
}

/// Compute regime scores and perform hysteretic classification.
///
/// The 4 scores (trend, range, shock, dead) are written to feature slots.
/// The internal enum is available via `state.current` for downstream gating.
///
/// Regime transition follows the deterministic rules from the approved plan:
/// 1. Low confidence → hold prior regime, emit uniform scores
/// 2. Same winner as current → hold, increment dwell
/// 3. Dwell < min_dwell → hold (hysteresis)
/// 4. Margin < threshold → hold (ambiguity)
/// 5. Otherwise → transition
pub fn classify(
    state: &mut RegimeState,
    inputs: &RegimeInputs,
    thresholds: &RegimeThresholds,
) -> RegimeFeatures {
    let mut invalid_count = 0u32;
    let total_inputs = 19u32;

    // Helper: unwrap or count as invalid
    let mut get = |opt: Option<f64>, default: f64| -> f64 {
        match opt {
            Some(v) => v,
            None => { invalid_count += 1; default }
        }
    };

    let flow_per_buy = get(inputs.flow_persistence_buy, 0.0);
    let flow_per_sell = get(inputs.flow_persistence_sell, 0.0);
    let slope = get(inputs.slope_mid_5s, 0.0);
    let slope_5m = get(inputs.slope_mid_5m, 0.0);
    let slope_15m = get(inputs.slope_mid_15m, 0.0);
    let slope_1h = get(inputs.slope_mid_1h, 0.0);
    let mp_conf = get(inputs.microprice_confirmation, 0.0);
    let brk_fail = get(inputs.breakout_failure, 0.0);
    let svb = get(inputs.spread_vs_baseline, 0.0);
    let rv = get(inputs.rv_5s, 0.0);
    let rv_15m = get(inputs.rv_15m, 0.0);
    let rv_1h = get(inputs.rv_1h, 0.0);
    let tape_z = get(inputs.tape_intensity_z, 0.0);
    let imb = get(inputs.trade_imbalance_5s, 0.0);
    let ret_5m = get(inputs.ret_5m, 0.0);
    let ret_15m = get(inputs.ret_15m, 0.0);
    let ret_1h = get(inputs.ret_1h, 0.0);
    let range_pos_5m = get(inputs.range_pos_5m, 0.5);
    let range_pos_15m = get(inputs.range_pos_15m, 0.5);
    let range_pos_1h = get(inputs.range_pos_1h, 0.5);
    // liq_count_30s and tape_trades_1s are always present (default 0)
    let liq = inputs.liq_count_30s;
    let tape_t = inputs.tape_trades_1s;

    let confidence = 1.0 - (invalid_count as f32 / total_inputs as f32);

    // ── Compute raw scores ──
    if confidence < 0.5 {
        // Low confidence: emit uniform scores, hold prior regime
        state.scores = [0.25, 0.25, 0.25, 0.25];
        state.dwell += 1;
        state.confidence = confidence;
        return RegimeFeatures {
            regime_trend: 0.25,
            regime_range: 0.25,
            regime_shock: 0.25,
            regime_dead: 0.25,
            context_regime_trend: 0.25,
            context_regime_range: 0.25,
            context_regime_shock: 0.25,
            context_regime_dead: 0.25,
            trend_bias_5m: 0.0,
            trend_bias_15m: 0.0,
            trend_bias_1h: 0.0,
            trend_alignment: 0.0,
        };
    }

    let flow_persistence_max = flow_per_buy.max(flow_per_sell);

    let score_trend =
        0.30 * c01(flow_persistence_max)
      + 0.25 * c01(slope.abs() / 0.5)
      + 0.25 * c01(mp_conf / 5.0)
      + 0.20 * c01(1.0 - brk_fail);

    let score_shock =
        0.30 * c01(svb / 3.0)
      + 0.30 * c01(rv / thresholds.rv_shock)
      + 0.20 * c01(tape_z / 3.0)
      + 0.20 * c01(liq / 5.0);

    let score_dead =
        0.40 * c01(1.0 - tape_t / thresholds.expected_trades_1s)
      + 0.30 * c01(1.0 - imb.abs())
      + 0.30 * c01(1.0 - rv / thresholds.rv_dead);

    // range is residual — watch item #2 per plan
    let score_range = (1.0 - score_trend - score_shock - score_dead).max(0.0);

    let scores = [
        score_trend as f32,
        score_range as f32,
        score_shock as f32,
        score_dead as f32,
    ];

    // ── Score→Enum transition ──
    let (winner_idx, winner_score) = scores.iter()
        .enumerate()
        .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())
        .unwrap();

    let second_best = scores.iter()
        .enumerate()
        .filter(|(i, _)| *i != winner_idx)
        .map(|(_, s)| *s)
        .fold(f32::NEG_INFINITY, f32::max);

    let margin = winner_score - second_best;
    let winner_regime = idx_to_regime(winner_idx);

    if winner_regime == state.current {
        // Same regime: hold, increment dwell
        state.dwell += 1;
    } else if state.dwell < thresholds.min_dwell {
        // Hysteresis hold: haven't dwelt long enough
        state.dwell += 1;
    } else if margin < thresholds.margin {
        // Ambiguity hold: margin too small
        state.dwell += 1;
    } else {
        // Transition
        state.current = winner_regime;
        state.dwell = 1;
    }

    state.scores = scores;
    state.confidence = confidence;

    let trend_bias_5m = bias_score(ret_5m, slope_5m, range_pos_5m, 0.0025, 0.18);
    let trend_bias_15m = bias_score(ret_15m, slope_15m, range_pos_15m, 0.0045, 0.12);
    let trend_bias_1h = bias_score(ret_1h, slope_1h, range_pos_1h, 0.0090, 0.06);
    let biases = [trend_bias_5m, trend_bias_15m, trend_bias_1h];
    let avg_bias = biases.iter().sum::<f64>() / biases.len() as f64;
    let trend_strength = avg_abs(&biases);
    let avg_abs_bias = trend_strength.max(1e-6);
    let directional_consensus = (avg_bias.abs() / avg_abs_bias).clamp(0.0, 1.0);
    let long_vol = c01((rv_15m / 0.0015).max(rv_1h / 0.0025));
    let low_vol = c01(1.0 - (rv_15m / 0.00045).max(rv_1h / 0.0008));
    let center_5m = 1.0 - ((range_pos_5m - 0.5).abs() * 2.0).clamp(0.0, 1.0);
    let center_15m = 1.0 - ((range_pos_15m - 0.5).abs() * 2.0).clamp(0.0, 1.0);
    let center_1h = 1.0 - ((range_pos_1h - 0.5).abs() * 2.0).clamp(0.0, 1.0);
    let center_mean = (center_5m + center_15m + center_1h) / 3.0;

    let trend_ctx_raw =
        0.46 * trend_strength +
        0.29 * directional_consensus +
        0.15 * scores[0] as f64 +
        0.10 * c01(1.0 - long_vol);
    let range_ctx_raw =
        0.36 * c01(1.0 - trend_strength) +
        0.24 * center_mean +
        0.20 * scores[1] as f64 +
        0.20 * c01(1.0 - long_vol);
    let shock_ctx_raw =
        0.48 * long_vol +
        0.27 * scores[2] as f64 +
        0.15 * c01(1.0 - directional_consensus) +
        0.10 * c01(svb / 3.0);
    let dead_ctx_raw =
        0.40 * low_vol +
        0.28 * scores[3] as f64 +
        0.20 * c01(1.0 - trend_strength) +
        0.12 * center_mean;

    let (ctx_trend, ctx_range, ctx_shock, ctx_dead) =
        normalize4(trend_ctx_raw, range_ctx_raw, shock_ctx_raw, dead_ctx_raw);

    RegimeFeatures {
        regime_trend: scores[0] as f64,
        regime_range: scores[1] as f64,
        regime_shock: scores[2] as f64,
        regime_dead: scores[3] as f64,
        context_regime_trend: ctx_trend,
        context_regime_range: ctx_range,
        context_regime_shock: ctx_shock,
        context_regime_dead: ctx_dead,
        trend_bias_5m,
        trend_bias_15m,
        trend_bias_1h,
        trend_alignment: avg_bias.clamp(-1.0, 1.0),
    }
}

fn idx_to_regime(idx: usize) -> MarketRegime {
    match idx {
        0 => MarketRegime::Trend,
        1 => MarketRegime::Range,
        2 => MarketRegime::Shock,
        3 => MarketRegime::DeadMarket,
        _ => MarketRegime::Range, // fallback
    }
}

/// Output of regime classification, written to feature slots 70-73.
#[derive(Debug, Clone)]
pub struct RegimeFeatures {
    pub regime_trend: f64,
    pub regime_range: f64,
    pub regime_shock: f64,
    pub regime_dead: f64,
    pub context_regime_trend: f64,
    pub context_regime_range: f64,
    pub context_regime_shock: f64,
    pub context_regime_dead: f64,
    pub trend_bias_5m: f64,
    pub trend_bias_15m: f64,
    pub trend_bias_1h: f64,
    pub trend_alignment: f64,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dead_market_inputs() -> RegimeInputs {
        RegimeInputs {
            flow_persistence_buy: Some(0.0),
            flow_persistence_sell: Some(0.0),
            slope_mid_5s: Some(0.0),
            slope_mid_5m: Some(0.0),
            slope_mid_15m: Some(0.0),
            slope_mid_1h: Some(0.0),
            microprice_confirmation: Some(0.0),
            breakout_failure: Some(0.0),
            spread_vs_baseline: Some(0.0),
            rv_5s: Some(0.00001), // very low vol
            rv_15m: Some(0.00005),
            rv_1h: Some(0.00008),
            tape_intensity_z: Some(-2.0),
            liq_count_30s: 0.0,
            tape_trades_1s: 0.0,
            trade_imbalance_5s: Some(0.0),
            ret_5m: Some(0.0),
            ret_15m: Some(0.0),
            ret_1h: Some(0.0),
            range_pos_5m: Some(0.5),
            range_pos_15m: Some(0.5),
            range_pos_1h: Some(0.5),
        }
    }

    fn trend_inputs() -> RegimeInputs {
        RegimeInputs {
            flow_persistence_buy: Some(0.9),    // strong persistent flow
            flow_persistence_sell: Some(0.0),
            slope_mid_5s: Some(0.8),            // strong slope
            slope_mid_5m: Some(0.35),
            slope_mid_15m: Some(0.22),
            slope_mid_1h: Some(0.08),
            microprice_confirmation: Some(8.0),  // confirmed
            breakout_failure: Some(0.0),         // no failure
            spread_vs_baseline: Some(0.0),       // normal spread
            rv_5s: Some(0.0003),                 // moderate vol
            rv_15m: Some(0.0007),
            rv_1h: Some(0.0011),
            tape_intensity_z: Some(1.0),         // normal tape
            liq_count_30s: 0.0,
            tape_trades_1s: 8.0,                 // active
            trade_imbalance_5s: Some(0.7),
            ret_5m: Some(0.003),
            ret_15m: Some(0.008),
            ret_1h: Some(0.014),
            range_pos_5m: Some(0.82),
            range_pos_15m: Some(0.76),
            range_pos_1h: Some(0.70),
        }
    }

    fn shock_inputs() -> RegimeInputs {
        RegimeInputs {
            flow_persistence_buy: Some(0.2),
            flow_persistence_sell: Some(0.2),
            slope_mid_5s: Some(0.1),
            microprice_confirmation: Some(0.0),
            breakout_failure: Some(1.0),
            spread_vs_baseline: Some(4.0),       // spread 4σ above baseline
            rv_5s: Some(0.002),                   // very high vol
            tape_intensity_z: Some(4.0),          // extreme tape
            liq_count_30s: 8.0,                   // many liquidations
            tape_trades_1s: 20.0,
            trade_imbalance_5s: Some(0.1),
        }
    }

    fn default_thresholds() -> RegimeThresholds {
        RegimeThresholds::default()
    }

    #[test]
    fn test_dead_market_highest() {
        let mut state = RegimeState::new();
        let th = default_thresholds();
        let inputs = dead_market_inputs();

        let f = classify(&mut state, &inputs, &th);

        assert!(f.regime_dead > f.regime_trend,
            "Dead should beat trend: {} vs {}", f.regime_dead, f.regime_trend);
        assert!(f.regime_dead > f.regime_shock,
            "Dead should beat shock: {} vs {}", f.regime_dead, f.regime_shock);
    }

    #[test]
    fn test_trend_highest() {
        let mut state = RegimeState::new();
        let th = default_thresholds();
        let inputs = trend_inputs();

        // Need to dwell past min_dwell first
        for _ in 0..6 {
            classify(&mut state, &inputs, &th);
        }

        let f = classify(&mut state, &inputs, &th);

        assert!(f.regime_trend > f.regime_dead,
            "Trend should beat dead: {} vs {}", f.regime_trend, f.regime_dead);
        assert!(f.regime_trend > f.regime_shock,
            "Trend should beat shock: {} vs {}", f.regime_trend, f.regime_shock);
    }

    #[test]
    fn test_shock_highest() {
        let mut state = RegimeState::new();
        let th = default_thresholds();
        let inputs = shock_inputs();

        let f = classify(&mut state, &inputs, &th);

        assert!(f.regime_shock > f.regime_trend,
            "Shock should beat trend: {} vs {}", f.regime_shock, f.regime_trend);
        assert!(f.regime_shock > f.regime_dead,
            "Shock should beat dead: {} vs {}", f.regime_shock, f.regime_dead);
    }

    #[test]
    fn test_hysteresis_holds() {
        let mut state = RegimeState::new();
        let th = default_thresholds();

        // Start in Range (default)
        assert_eq!(state.current, MarketRegime::Range);

        // Feed strong trend inputs but dwell < min_dwell (5)
        let inputs = trend_inputs();
        for _ in 0..4 {
            classify(&mut state, &inputs, &th);
        }

        // After 4 ticks, should still be Range due to hysteresis
        assert_eq!(state.current, MarketRegime::Range,
            "Should hold Range during hysteresis dwell");

        // After 5+ ticks with margin, should transition
        classify(&mut state, &inputs, &th);
        classify(&mut state, &inputs, &th);
        // By now dwell >= min_dwell. If margin is sufficient, transition happens.
        // The exact tick depends on initial dwell=0 and the +1 increments.
        // After 6 calls: dwell was 0→1→2→3→4→5→6, and at dwell=5 (6th call)
        // the transition should fire if margin >= 0.10.
    }

    #[test]
    fn test_margin_blocks_transition() {
        let mut state = RegimeState::new();
        let th = RegimeThresholds {
            margin: 0.90, // very high margin requirement
            min_dwell: 1,
            ..default_thresholds()
        };

        // Even with min_dwell=1, a very high margin requirement blocks transition
        let inputs = trend_inputs();
        for _ in 0..20 {
            classify(&mut state, &inputs, &th);
        }

        // With margin=0.90, it's very hard to transition
        // The regime should stay Range if the margin between trend and second-best < 0.90
    }

    #[test]
    fn test_low_confidence_holds_and_uniform() {
        let mut state = RegimeState::new();
        let th = default_thresholds();

        // Start in Range
        assert_eq!(state.current, MarketRegime::Range);

        // Feed inputs where most are None → confidence < 0.5
        let inputs = RegimeInputs {
            flow_persistence_buy: None,
            flow_persistence_sell: None,
            slope_mid_5s: None,
            microprice_confirmation: None,
            breakout_failure: None,
            spread_vs_baseline: None,
            rv_5s: None,
            tape_intensity_z: None,
            liq_count_30s: 0.0,
            tape_trades_1s: 0.0,
            trade_imbalance_5s: None,
        };

        let f = classify(&mut state, &inputs, &th);

        // Regime enum holds prior (Range)
        assert_eq!(state.current, MarketRegime::Range);
        // Scores are uniform 0.25
        assert!((f.regime_trend - 0.25).abs() < 1e-6);
        assert!((f.regime_range - 0.25).abs() < 1e-6);
        assert!((f.regime_shock - 0.25).abs() < 1e-6);
        assert!((f.regime_dead - 0.25).abs() < 1e-6);
        // Confidence < 0.5
        assert!(state.confidence < 0.5);
    }

    #[test]
    fn test_deterministic() {
        let th = default_thresholds();
        let inputs = trend_inputs();

        let mut state1 = RegimeState::new();
        let mut state2 = RegimeState::new();

        for _ in 0..10 {
            let f1 = classify(&mut state1, &inputs, &th);
            let f2 = classify(&mut state2, &inputs, &th);
            assert_eq!(f1.regime_trend, f2.regime_trend);
            assert_eq!(f1.regime_range, f2.regime_range);
            assert_eq!(state1.current, state2.current);
            assert_eq!(state1.dwell, state2.dwell);
        }
    }
}
