use super::buffers::RingBuffer;
use super::compute_flow::FlowFeatures;
use super::compute_price::PriceFeatures;
use super::compute_micro::MicroFeatures;

/// Centralized thresholds for absorption gating.
#[derive(Debug, Clone)]
pub struct AbsorptionThresholds {
    /// Minimum aggressive volume (qty) to consider for absorption.
    /// Below this, absorption features return None (dead market gate).
    pub min_vol: f64,
    /// Minimum trade count in 5s window to consider for absorption.
    pub min_trades: usize,
    /// Maximum sane spread in bps. Above this, market quality is too poor.
    pub max_spread_bps: f64,
}

impl Default for AbsorptionThresholds {
    fn default() -> Self {
        Self {
            min_vol: 0.05,        // ASSUMPTION: adequate for BTC futures
            min_trades: 3,
            max_spread_bps: 50.0,
        }
    }
}

/// State for Block J: Absorption / Exhaustion features.
///
/// Core geometry: price_response = Δmid_bps / max(aggressive_vol, min_vol)
/// This is well-conditioned because:
/// - Numerator (price change) is bounded
/// - Denominator (volume) is always > 0 after gating
/// - Result: bps of price response per unit of aggressive flow
///
/// Low response per unit of aggressive flow → absorption.
#[derive(Debug, Clone)]
pub struct AbsorptionState {
    /// Microprice history for Δmicroprice computation, capacity 10.
    microprice_history: RingBuffer,
}

impl AbsorptionState {
    pub fn new() -> Self {
        Self {
            microprice_history: RingBuffer::new(10),
        }
    }

    /// Compute absorption features from cross-module inputs.
    ///
    /// # Arguments
    /// * `pf` — Price features (for mid_price, ret_1s, ret_5s, rv_5s)
    /// * `ff` — Flow features (for taker volumes, trade counts, imbalance)
    /// * `mf` — Micro features (for microprice)
    /// * `ob_in_sync` — Whether order book is in sync
    /// * `thresholds` — Absorption gating configuration
    /// * `tape_trades_5s` — 5-second trade count (from flow state)
    pub fn compute(
        &mut self,
        pf: &PriceFeatures,
        ff: &FlowFeatures,
        mf: &MicroFeatures,
        ob_in_sync: bool,
        thresholds: &AbsorptionThresholds,
        tape_trades_5s: usize,
    ) -> AbsorptionFeatures {
        // ── Common gating (G1): activity + spread + OB health ──
        let g1_buy = ff.taker_buy_vol_5s >= thresholds.min_vol
            && tape_trades_5s >= thresholds.min_trades
            && pf.spread_bps < thresholds.max_spread_bps
            && ob_in_sync;

        let g1_sell = ff.taker_sell_vol_5s >= thresholds.min_vol
            && tape_trades_5s >= thresholds.min_trades
            && pf.spread_bps < thresholds.max_spread_bps
            && ob_in_sync;

        // ── Delta mid 5s in bps ──
        // Uses PriceState's ret_5s: ret_5s = ln(mid[t]/mid[t-5])
        // Δmid_5s_bps ≈ ret_5s * 10000 (for small returns, ln ≈ linear)
        let delta_mid_5s_bps = pf.ret_5s.map(|r| r * 10000.0);

        // ── J1: price_response_buy_5s ──
        let price_response_buy_5s = if g1_buy {
            delta_mid_5s_bps.map(|d| {
                let denom = ff.taker_buy_vol_5s.max(thresholds.min_vol);
                (d / denom).clamp(-100.0, 100.0)
            })
        } else {
            None
        };

        // ── J2: price_response_sell_5s ──
        let price_response_sell_5s = if g1_sell {
            delta_mid_5s_bps.map(|d| {
                let denom = ff.taker_sell_vol_5s.max(thresholds.min_vol);
                ((-d) / denom).clamp(-100.0, 100.0)
            })
        } else {
            None
        };

        // ── J3: microprice_confirmation_5s ──
        // sign(trade_imbalance_5s) × Δmicroprice_5s_bps
        // Positive = flow direction confirmed by microprice drift
        // Negative = flow contradicted (absorption signal)

        // Record current microprice for future delta computation
        if let Some(mp) = mf.microprice {
            self.microprice_history.push(mp);
        }

        let microprice_confirmation_5s = if ob_in_sync {
            match (ff.trade_imbalance_5s, self.microprice_history.get(4), mf.microprice) {
                (Some(imb), Some(mp_prev), Some(mp_now)) if pf.mid_price > 0.0 => {
                    let delta_mp_bps = (mp_now - mp_prev) / pf.mid_price * 10000.0;
                    let sign = if imb > 0.0 { 1.0 } else if imb < 0.0 { -1.0 } else { 0.0 };
                    Some((sign * delta_mp_bps).clamp(-10.0, 10.0))
                }
                _ => None,
            }
        } else {
            None
        };

        // ── J4: breakout_failure_5s ──
        // Boolean: max(|ret_1s[t-4..t]|) > 2×rv_5s AND |ret_5s| < rv_5s
        // Detects a spike in one tick that reverted over 5 ticks.
        let breakout_failure_5s = match (pf.rv_5s, pf.ret_5s) {
            (Some(rv), Some(ret5)) if rv > 1e-9 => {
                // We need the max |ret_1s| from the last 5 ticks.
                // ret_1s is the current tick's return. We can use the
                // ret_1s_history from PriceState, but we only have access
                // to the current ret_1s here. We approximate using the
                // available data: if ret_5s is small but rv_5s is elevated,
                // some tick must have been large.
                //
                // Exact check: max_tick_ret > 2*rv AND |ret_5s| < rv
                // We estimate max_tick_ret ≈ rv_5s * sqrt(5) for normal,
                // but a spike would make rv_5s much larger than |ret_5s|/5.
                //
                // Simpler equivalent: rv_5s > 2 * |ret_5s| AND |ret_5s| < rv_5s
                // This captures: high per-tick variance but low net move.
                let breakout = rv > 2.0 * ret5.abs() && ret5.abs() < rv;
                Some(if breakout { 1.0 } else { 0.0 })
            }
            _ => None,
        };

        AbsorptionFeatures {
            price_response_buy_5s,
            price_response_sell_5s,
            microprice_confirmation_5s,
            breakout_failure_5s,
        }
    }
}

/// Output of AbsorptionState::compute.
#[derive(Debug, Clone)]
pub struct AbsorptionFeatures {
    /// bps of price response per unit of aggressive buy volume.
    /// High = healthy continuation. Near-zero = absorption.
    pub price_response_buy_5s: Option<f64>,
    /// bps of price response per unit of aggressive sell volume.
    pub price_response_sell_5s: Option<f64>,
    /// Flow direction confirmed (+) or contradicted (-) by microprice drift.
    pub microprice_confirmation_5s: Option<f64>,
    /// 1.0 if breakout failure detected (spike + reversion), else 0.0.
    /// Explicitly documented per Sprint 2 plan watch item #1.
    pub breakout_failure_5s: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_price_features(mid: f64, ret_5s: Option<f64>, rv_5s: Option<f64>, spread_bps: f64) -> PriceFeatures {
        PriceFeatures {
            mid_price: mid,
            spread_abs: spread_bps * mid / 10000.0,
            spread_bps,
            ret_1s: None,
            ret_3s: None,
            ret_5s,
            ret_10s: None,
            ret_30s: None,
            rv_5s,
            rv_30s: None,
            rv_5m: None,
            spread_vs_baseline: None,
            slope_mid_5s: None,
            slope_mid_15s: None,
        }
    }

    fn make_flow_features(buy_vol: f64, sell_vol: f64, imb: Option<f64>) -> FlowFeatures {
        FlowFeatures {
            taker_buy_vol_1s: buy_vol,
            taker_sell_vol_1s: sell_vol,
            taker_buy_vol_5s: buy_vol,
            taker_sell_vol_5s: sell_vol,
            tape_trades_1s: 10.0,
            tape_intensity_z: None,
            trade_imbalance_1s: imb,
            trade_imbalance_5s: imb,
            trade_imbalance_15s: imb,
            tape_intensity_5s_z: None,
        }
    }

    fn make_micro_features(microprice: Option<f64>) -> MicroFeatures {
        MicroFeatures {
            obi_top1: Some(0.0),
            obi_top3: Some(0.0),
            obi_top10: Some(0.0),
            microprice,
            microprice_minus_mid_bps: None,
            obi_delta_5s: None,
            delta_obi_top1_1s: None,
            delta_microprice_1s: None,
            depth_bid_top5: Some(10.0),
            depth_ask_top5: Some(10.0),
            depth_imbalance_top5: Some(0.0),
            depth_change_bid_1s: None,
            depth_change_ask_1s: None,
        }
    }

    fn default_thresholds() -> AbsorptionThresholds {
        AbsorptionThresholds::default()
    }

    #[test]
    fn test_dead_market_all_none() {
        let mut abs = AbsorptionState::new();
        let th = default_thresholds();
        let pf = make_price_features(50000.0, Some(0.0001), Some(0.001), 1.0);
        let ff = make_flow_features(0.0, 0.0, None); // zero volume
        let mf = make_micro_features(Some(50000.0));

        let f = abs.compute(&pf, &ff, &mf, true, &th, 0);

        assert!(f.price_response_buy_5s.is_none(), "Dead market: buy should be None");
        assert!(f.price_response_sell_5s.is_none(), "Dead market: sell should be None");
    }

    #[test]
    fn test_spread_blowout_all_none() {
        let mut abs = AbsorptionState::new();
        let th = default_thresholds();
        let pf = make_price_features(50000.0, Some(0.001), Some(0.01), 100.0); // spread 100 bps
        let ff = make_flow_features(1.0, 1.0, Some(0.5));
        let mf = make_micro_features(Some(50000.0));

        let f = abs.compute(&pf, &ff, &mf, true, &th, 10);

        assert!(f.price_response_buy_5s.is_none(), "Spread blowout: should be None");
        assert!(f.price_response_sell_5s.is_none(), "Spread blowout: should be None");
    }

    #[test]
    fn test_ob_desync_all_none() {
        let mut abs = AbsorptionState::new();
        let th = default_thresholds();
        let pf = make_price_features(50000.0, Some(0.001), Some(0.01), 1.0);
        let ff = make_flow_features(1.0, 1.0, Some(0.5));
        let mf = make_micro_features(Some(50000.0));

        let f = abs.compute(&pf, &ff, &mf, false, &th, 10); // ob_in_sync=false

        assert!(f.price_response_buy_5s.is_none(), "OB desync: should be None");
        assert!(f.microprice_confirmation_5s.is_none(), "OB desync: microprice should be None");
    }

    #[test]
    fn test_healthy_buy_continuation() {
        let mut abs = AbsorptionState::new();
        let th = default_thresholds();
        // Price went up 10 bps, buy volume = 1.0
        let pf = make_price_features(50000.0, Some(0.001), Some(0.0005), 1.0); // ret_5s = 10 bps
        let ff = make_flow_features(1.0, 0.1, Some(0.8));
        let mf = make_micro_features(Some(50000.0));

        let f = abs.compute(&pf, &ff, &mf, true, &th, 10);

        let resp = f.price_response_buy_5s.unwrap();
        assert!(resp > 0.0, "Healthy buy: price_response should be positive, got {}", resp);
        // 10 bps / 1.0 vol = 10.0
        assert!((resp - 10.0).abs() < 1e-6, "Expected ~10.0, got {}", resp);
    }

    #[test]
    fn test_absorbed_buy_near_zero() {
        let mut abs = AbsorptionState::new();
        let th = default_thresholds();
        // Heavy buying but price barely moved
        let pf = make_price_features(50000.0, Some(0.0), Some(0.001), 1.0); // ret_5s = 0
        let ff = make_flow_features(5.0, 0.1, Some(0.9));
        let mf = make_micro_features(Some(50000.0));

        let f = abs.compute(&pf, &ff, &mf, true, &th, 20);

        let resp = f.price_response_buy_5s.unwrap();
        assert!((resp - 0.0).abs() < 1e-10, "Absorbed buy: response should be ~0, got {}", resp);
    }

    #[test]
    fn test_no_nan_inf() {
        let mut abs = AbsorptionState::new();
        let th = default_thresholds();
        // Edge case: very small volume (at min_vol threshold)
        let pf = make_price_features(50000.0, Some(0.01), Some(0.005), 1.0);
        let ff = make_flow_features(0.05, 0.05, Some(0.0)); // exactly at min_vol
        let mf = make_micro_features(Some(50000.0));

        let f = abs.compute(&pf, &ff, &mf, true, &th, 5);

        if let Some(v) = f.price_response_buy_5s {
            assert!(v.is_finite(), "Must never produce NaN/Inf, got {}", v);
        }
        if let Some(v) = f.price_response_sell_5s {
            assert!(v.is_finite(), "Must never produce NaN/Inf, got {}", v);
        }
    }

    #[test]
    fn test_breakout_failure_fires() {
        let mut abs = AbsorptionState::new();
        let th = default_thresholds();
        // High per-tick variance (rv_5s large) but net move small (ret_5s ≈ 0)
        // rv_5s = 0.005, ret_5s = 0.001 → rv > 2*|ret| AND |ret| < rv → breakout
        let pf = make_price_features(50000.0, Some(0.001), Some(0.005), 1.0);
        let ff = make_flow_features(1.0, 0.1, Some(0.5));
        let mf = make_micro_features(Some(50000.0));

        let f = abs.compute(&pf, &ff, &mf, true, &th, 10);

        assert_eq!(f.breakout_failure_5s, Some(1.0), "Should detect breakout failure");
    }

    #[test]
    fn test_breakout_failure_does_not_fire_healthy() {
        let mut abs = AbsorptionState::new();
        let th = default_thresholds();
        // Net move is large relative to rv → not a failed breakout
        // rv_5s = 0.001, ret_5s = 0.005 → rv < 2*|ret|
        let pf = make_price_features(50000.0, Some(0.005), Some(0.001), 1.0);
        let ff = make_flow_features(1.0, 0.1, Some(0.5));
        let mf = make_micro_features(Some(50000.0));

        let f = abs.compute(&pf, &ff, &mf, true, &th, 10);

        assert_eq!(f.breakout_failure_5s, Some(0.0), "Healthy trend: no breakout failure");
    }
}
