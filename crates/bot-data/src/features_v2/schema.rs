use serde::{Serialize, Deserialize};

/// Complete feature row emitted every Δt (1 second).
/// All feature fields are Option<f64>: None means the feature is not yet
/// warmed up or the required data stream is unavailable.
/// This struct is written to Parquet for offline training.
///
/// ## Schema v6 (Sprint 2)
/// - Added 15 new features: absorption (4), persistence (7), regime (4)
/// - Total: 74 features (was 59 in v5.1).
/// - OBS_DIM: 148 (74 values + 74 masks)
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeatureRow {
    pub symbol: String,
    pub t_emit: i64,  // UTC milliseconds

    // ── A) Price / Spread (4 features) ──
    pub mid_price: Option<f64>,
    pub spread_abs: Option<f64>,
    pub spread_bps: Option<f64>,
    pub spread_vs_baseline: Option<f64>,     // z-score of spread vs 2-min EWMA

    // ── B) Returns & Volatility (10 features) ──
    pub ret_1s: Option<f64>,
    pub ret_3s: Option<f64>,
    pub ret_5s: Option<f64>,
    pub ret_10s: Option<f64>,
    pub ret_30s: Option<f64>,
    pub rv_5s: Option<f64>,
    pub rv_30s: Option<f64>,
    pub rv_5m: Option<f64>,
    pub slope_mid_5s: Option<f64>,           // bps/sec over 5s
    pub slope_mid_15s: Option<f64>,          // bps/sec over 15s

    // ── C) Taker Flow / Tape (10 features) ──
    // NOTE: taker_buy_ratio_5s removed — algebraically redundant with trade_imbalance_5s
    //       (ratio = 0.5 + imbalance/2, ρ=1.000 confirmed)
    pub taker_buy_vol_1s: Option<f64>,
    pub taker_sell_vol_1s: Option<f64>,
    pub taker_buy_vol_5s: Option<f64>,
    pub taker_sell_vol_5s: Option<f64>,
    pub tape_trades_1s: Option<f64>,
    pub tape_intensity_z: Option<f64>,
    pub trade_imbalance_1s: Option<f64>,     // (buy-sell)/(buy+sell) 1s
    pub trade_imbalance_5s: Option<f64>,     // canonical flow direction signal
    pub trade_imbalance_15s: Option<f64>,
    pub tape_intensity_5s_z: Option<f64>,    // z-score of 5s trade count

    // ── D) Microstructure (Book) (13 features) ──
    pub obi_top1: Option<f64>,
    pub obi_top3: Option<f64>,
    pub obi_top10: Option<f64>,
    pub microprice: Option<f64>,
    pub microprice_minus_mid_bps: Option<f64>,
    pub obi_delta_5s: Option<f64>,
    pub delta_obi_top1_1s: Option<f64>,
    pub delta_microprice_1s: Option<f64>,    // bps change
    pub depth_bid_top5: Option<f64>,         // total bid qty top 5
    pub depth_ask_top5: Option<f64>,         // total ask qty top 5
    pub depth_imbalance_top5: Option<f64>,   // (bid-ask)/(bid+ask) depth
    pub depth_change_bid_1s: Option<f64>,    // pct change bid depth
    pub depth_change_ask_1s: Option<f64>,    // pct change ask depth

    // ── E) Shocks / Derivatives (7 features) ──
    pub liq_buy_vol_30s: Option<f64>,
    pub liq_sell_vol_30s: Option<f64>,
    pub liq_net_30s: Option<f64>,
    pub liq_count_30s: Option<f64>,
    pub mark_minus_mid_bps: Option<f64>,
    pub funding_rate: Option<f64>,
    pub funding_zscore: Option<f64>,         // z-score, warmup ~24 min

    // ── F) Technicals (slow gating) (4 features) ──
    pub ema200_distance_pct: Option<f64>,
    pub rsi_14: Option<f64>,
    pub bb_width: Option<f64>,
    pub bb_pos: Option<f64>,

    // ── G) Account State (injected externally) (4 features) ──
    pub position_flag: Option<f64>,     // -1, 0, 1
    pub latent_pnl_pct: Option<f64>,
    pub max_pnl_pct: Option<f64>,
    pub current_drawdown_pct: Option<f64>,

    // ── H) Time (2 features) ──
    pub time_sin: Option<f64>,
    pub time_cos: Option<f64>,

    // ── I) Open Interest (5 features) ──
    // NOTE: oi_delta_* are percentage changes: e.g., 5.0 = 5% increase.
    //       Clamped at ±20% to cover normal and extreme events.
    pub oi_value: Option<f64>,
    pub oi_delta_30s: Option<f64>,           // % change, clamped ±20
    pub oi_delta_1m: Option<f64>,            // % change, clamped ±20
    pub oi_delta_5m: Option<f64>,            // % change, clamped ±20
    pub oi_zscore_30m: Option<f64>,

    // ── J) Absorption (4 features) — Sprint 2 ──
    pub price_response_buy_5s: Option<f64>,    // bps/qty, clamp ±100
    pub price_response_sell_5s: Option<f64>,   // bps/qty, clamp ±100
    pub microprice_confirmation_5s: Option<f64>, // bps, clamp ±10
    pub breakout_failure_5s: Option<f64>,        // {0, 1}

    // ── K) Persistence (7 features) — Sprint 2 ──
    pub obi_persistence_buy: Option<f64>,          // [0, 1]
    pub obi_persistence_sell: Option<f64>,         // [0, 1]
    pub flow_persistence_buy: Option<f64>,         // [0, 1]
    pub flow_persistence_sell: Option<f64>,        // [0, 1]
    pub spread_deterioration: Option<f64>,         // [0, 1]
    pub depth_deterioration_bid: Option<f64>,      // [0, 1]
    pub depth_deterioration_ask: Option<f64>,      // [0, 1]

    // ── L) Regime (4 features) — Sprint 2 ──
    pub regime_trend: Option<f64>,    // [0, 1] score
    pub regime_range: Option<f64>,    // [0, 1] residual score
    pub regime_shock: Option<f64>,    // [0, 1] score
    pub regime_dead: Option<f64>,     // [0, 1] score
}

impl Default for FeatureRow {
    fn default() -> Self {
        Self::new(String::new(), 0)
    }
}

impl FeatureRow {
    pub fn new(symbol: String, t_emit: i64) -> Self {
        Self {
            symbol,
            t_emit,
            mid_price: None,
            spread_abs: None,
            spread_bps: None,
            spread_vs_baseline: None,
            ret_1s: None,
            ret_3s: None,
            ret_5s: None,
            ret_10s: None,
            ret_30s: None,
            rv_5s: None,
            rv_30s: None,
            rv_5m: None,
            slope_mid_5s: None,
            slope_mid_15s: None,
            taker_buy_vol_1s: None,
            taker_sell_vol_1s: None,
            taker_buy_vol_5s: None,
            taker_sell_vol_5s: None,
            tape_trades_1s: None,
            tape_intensity_z: None,
            trade_imbalance_1s: None,
            trade_imbalance_5s: None,
            trade_imbalance_15s: None,
            tape_intensity_5s_z: None,
            obi_top1: None,
            obi_top3: None,
            obi_top10: None,
            microprice: None,
            microprice_minus_mid_bps: None,
            obi_delta_5s: None,
            delta_obi_top1_1s: None,
            delta_microprice_1s: None,
            depth_bid_top5: None,
            depth_ask_top5: None,
            depth_imbalance_top5: None,
            depth_change_bid_1s: None,
            depth_change_ask_1s: None,
            liq_buy_vol_30s: None,
            liq_sell_vol_30s: None,
            liq_net_30s: None,
            liq_count_30s: None,
            mark_minus_mid_bps: None,
            funding_rate: None,
            funding_zscore: None,
            ema200_distance_pct: None,
            rsi_14: None,
            bb_width: None,
            bb_pos: None,
            position_flag: None,
            latent_pnl_pct: None,
            max_pnl_pct: None,
            current_drawdown_pct: None,
            time_sin: None,
            time_cos: None,
            oi_value: None,
            oi_delta_30s: None,
            oi_delta_1m: None,
            oi_delta_5m: None,
            oi_zscore_30m: None,
            // Sprint 2
            price_response_buy_5s: None,
            price_response_sell_5s: None,
            microprice_confirmation_5s: None,
            breakout_failure_5s: None,
            obi_persistence_buy: None,
            obi_persistence_sell: None,
            flow_persistence_buy: None,
            flow_persistence_sell: None,
            spread_deterioration: None,
            depth_deterioration_bid: None,
            depth_deterioration_ask: None,
            regime_trend: None,
            regime_range: None,
            regime_shock: None,
            regime_dead: None,
        }
    }

    pub fn to_obs_vec(&self) -> (Vec<f32>, [bool; 74]) {
        let mut values = Vec::with_capacity(74);
        let mut masks = Vec::with_capacity(74);
        let mut clamped = [false; 74];
        let mut idx = 0;

        let raw = |v: f64, _c: &mut bool| v as f32;
        
        let clamp_pct = |v: f64, c: &mut bool| {
            if v < -1.0 || v > 1.0 { *c = true; }
            v.clamp(-1.0, 1.0) as f32
        };

        let clamp_z = |v: f64, c: &mut bool| {
            if v < -10.0 || v > 10.0 { *c = true; }
            v.clamp(-10.0, 10.0) as f32
        };

        // For OI percentage deltas. Semantic: percentage change (e.g., 5.0 = 5%).
        // Clamped at ±20% to cover both normal variation and extreme liquidation cascades.
        let clamp_oi_delta = |v: f64, c: &mut bool| {
            if v < -20.0 || v > 20.0 { *c = true; }
            v.clamp(-20.0, 20.0) as f32
        };

        macro_rules! add {
            ($opt:expr, $tr:expr) => {
                let curr_idx = idx;
                idx += 1;
                if let Some(val) = $opt {
                    values.push($tr(val, &mut clamped[curr_idx]));
                    masks.push(1.0);
                } else {
                    values.push(0.0);
                    masks.push(0.0);
                }
            };
        }

        macro_rules! add_fallback {
            ($opt:expr, $fallback:expr, $tr:expr) => {
                let curr_idx = idx;
                idx += 1;
                if let Some(val) = $opt {
                    values.push($tr(val, &mut clamped[curr_idx]));
                    masks.push(1.0);
                } else {
                    values.push($tr($fallback, &mut clamped[curr_idx]));
                    masks.push(0.0);
                }
            };
        }

        // A) Price/Spread (4)
        add!(self.mid_price, raw);
        add!(self.spread_abs, raw);
        add!(self.spread_bps, raw);
        add!(self.spread_vs_baseline, clamp_z);

        // B) Returns & Volatility (10)
        add!(self.ret_1s, clamp_pct);
        add!(self.ret_3s, clamp_pct);
        add!(self.ret_5s, clamp_pct);
        add!(self.ret_10s, clamp_pct);
        add!(self.ret_30s, clamp_pct);
        add!(self.rv_5s, clamp_pct);
        add!(self.rv_30s, clamp_pct);
        add!(self.rv_5m, clamp_pct);
        add!(self.slope_mid_5s, clamp_z);
        add!(self.slope_mid_15s, clamp_z);

        // C) Taker Flow (10) — taker_buy_ratio_5s removed
        add!(self.taker_buy_vol_1s, raw);
        add!(self.taker_sell_vol_1s, raw);
        add!(self.taker_buy_vol_5s, raw);
        add!(self.taker_sell_vol_5s, raw);
        add!(self.tape_trades_1s, raw);
        add!(self.tape_intensity_z, clamp_z);
        add!(self.trade_imbalance_1s, clamp_pct);
        add!(self.trade_imbalance_5s, clamp_pct);
        add!(self.trade_imbalance_15s, clamp_pct);
        add!(self.tape_intensity_5s_z, clamp_z);

        // D) Microstructure (13)
        add!(self.obi_top1, clamp_pct);
        add!(self.obi_top3, clamp_pct);
        add!(self.obi_top10, clamp_pct);
        add!(self.microprice, raw);
        add!(self.microprice_minus_mid_bps, clamp_pct);
        add!(self.obi_delta_5s, clamp_pct);
        add!(self.delta_obi_top1_1s, clamp_pct);
        add!(self.delta_microprice_1s, clamp_z);
        add!(self.depth_bid_top5, raw);
        add!(self.depth_ask_top5, raw);
        add!(self.depth_imbalance_top5, clamp_pct);
        add!(self.depth_change_bid_1s, clamp_pct);
        add!(self.depth_change_ask_1s, clamp_pct);

        // E) Shocks (7)
        add!(self.liq_buy_vol_30s, raw);
        add!(self.liq_sell_vol_30s, raw);
        add!(self.liq_net_30s, raw);
        add!(self.liq_count_30s, raw);
        add!(self.mark_minus_mid_bps, clamp_pct);
        add!(self.funding_rate, clamp_pct);
        add!(self.funding_zscore, clamp_z);

        // F) Technicals (4)
        add!(self.ema200_distance_pct, clamp_pct);
        add_fallback!(self.rsi_14, 50.0, raw);
        add!(self.bb_width, raw);
        add!(self.bb_pos, clamp_z);

        // G) Account (4)
        add!(self.position_flag, raw);
        add!(self.latent_pnl_pct, clamp_pct);
        add!(self.max_pnl_pct, clamp_pct);
        add!(self.current_drawdown_pct, clamp_pct);

        // H) Time (2)
        add!(self.time_sin, raw);
        add!(self.time_cos, raw);

        // I) Open Interest (5) — oi_delta_* use clamp_oi_delta (±20%)
        add!(self.oi_value, raw);
        add!(self.oi_delta_30s, clamp_oi_delta);
        add!(self.oi_delta_1m, clamp_oi_delta);
        add!(self.oi_delta_5m, clamp_oi_delta);
        add!(self.oi_zscore_30m, clamp_z);

        // Clamp for absorption response features (±100 bps/qty)
        let clamp_abs = |v: f64, c: &mut bool| {
            if v < -100.0 || v > 100.0 { *c = true; }
            v.clamp(-100.0, 100.0) as f32
        };

        // Bounded [0,1] — no clamping needed, use raw
        let bounded01 = |v: f64, _c: &mut bool| v.clamp(0.0, 1.0) as f32;

        // J) Absorption (4)
        add!(self.price_response_buy_5s, clamp_abs);
        add!(self.price_response_sell_5s, clamp_abs);
        add!(self.microprice_confirmation_5s, clamp_z);
        add!(self.breakout_failure_5s, raw);  // {0, 1} binary

        // K) Persistence (7) — all [0, 1]
        add!(self.obi_persistence_buy, bounded01);
        add!(self.obi_persistence_sell, bounded01);
        add!(self.flow_persistence_buy, bounded01);
        add!(self.flow_persistence_sell, bounded01);
        add!(self.spread_deterioration, bounded01);
        add!(self.depth_deterioration_bid, bounded01);
        add!(self.depth_deterioration_ask, bounded01);

        // L) Regime (4) — all [0, 1]
        add!(self.regime_trend, bounded01);
        add!(self.regime_range, bounded01);
        add!(self.regime_shock, bounded01);
        add!(self.regime_dead, bounded01);

        // Concatenate Masks after Values
        values.extend(masks);
        (values, clamped)
    }

    /// Number of features in the observation vector (74 values + 74 masks).
    pub const OBS_DIM: usize = 148;
    pub const OBS_SCHEMA_VERSION: u16 = 6;
}
