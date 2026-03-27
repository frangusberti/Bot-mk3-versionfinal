/// Sprint 1 Feature Diagnostics
/// Simulates 600 ticks of realistic BTC market data, runs the feature engine,
/// and computes: per-feature descriptive stats, None rates, clamp rates,
/// pairwise correlation for new features, and block-level ablation coverage.
///
/// Run: cargo test -p bot-data --test feature_diagnostics -- --nocapture

use bot_data::features_v2::{FeatureEngineV2, FeatureEngineV2Config};
use bot_data::features_v2::schema::FeatureRow;
use bot_data::features_v2::compute_account::AccountState;
use bot_data::normalization::schema::NormalizedMarketEvent;

const NUM_TICKS: usize = 600; // 10 minutes of 1-second ticks

fn make_bookticker(ts: i64, bid: f64, ask: f64) -> NormalizedMarketEvent {
    NormalizedMarketEvent {
        time_canonical: ts,
        stream_name: "bookTicker".to_string(),
        event_type: "bookTicker".to_string(),
        best_bid: Some(bid),
        best_ask: Some(ask),
        payload_json: format!("{{\"b\":\"{}\",\"a\":\"{}\",\"B\":\"1.0\",\"A\":\"1.0\"}}", bid, ask),
        ..Default::default()
    }
}

fn make_aggtrade(ts: i64, price: f64, qty: f64, is_buyer_maker: bool) -> NormalizedMarketEvent {
    NormalizedMarketEvent {
        time_canonical: ts,
        stream_name: "aggTrade".to_string(),
        event_type: "trade".to_string(),
        price: Some(price),
        qty: Some(qty),
        payload_json: format!("{{\"m\":{}}}", is_buyer_maker),
        ..Default::default()
    }
}

fn make_mark_price(ts: i64, mark: f64, funding: f64) -> NormalizedMarketEvent {
    NormalizedMarketEvent {
        time_canonical: ts,
        stream_name: "markPrice".to_string(),
        event_type: "markPrice".to_string(),
        mark_price: Some(mark),
        funding_rate: Some(funding),
        ..Default::default()
    }
}

fn make_oi(ts: i64, oi_val: f64) -> NormalizedMarketEvent {
    NormalizedMarketEvent {
        time_canonical: ts,
        stream_name: "openInterest".to_string(),
        event_type: "openInterest".to_string(),
        open_interest: Some(oi_val),
        ..Default::default()
    }
}

/// Generate a synthetic orderbook with 10 levels on each side.
fn make_orderbook(mid: f64, tick_size: f64, base_qty: f64, imbalance: f64)
    -> (Vec<(f64, f64)>, Vec<(f64, f64)>)
{
    let mut bids = Vec::with_capacity(10);
    let mut asks = Vec::with_capacity(10);
    for i in 0..10 {
        let offset = tick_size * (i + 1) as f64;
        let bid_qty = base_qty * (1.0 + imbalance * 0.3) * (1.0 - i as f64 * 0.05);
        let ask_qty = base_qty * (1.0 - imbalance * 0.3) * (1.0 - i as f64 * 0.05);
        bids.push((mid - offset, bid_qty.max(0.01)));
        asks.push((mid + offset, ask_qty.max(0.01)));
    }
    (bids, asks)
}

/// Feature names matching the obs vector order in schema.rs v6
const FEATURE_NAMES: [&str; 74] = [
    // A) Price/Spread (4)
    "mid_price", "spread_abs", "spread_bps", "spread_vs_baseline",
    // B) Returns & Volatility (10)
    "ret_1s", "ret_3s", "ret_5s", "ret_10s", "ret_30s",
    "rv_5s", "rv_30s", "rv_5m",
    "slope_mid_5s", "slope_mid_15s",
    // C) Taker Flow (10)
    "taker_buy_vol_1s", "taker_sell_vol_1s", "taker_buy_vol_5s", "taker_sell_vol_5s",
    "tape_trades_1s", "tape_intensity_z",
    "trade_imbalance_1s", "trade_imbalance_5s", "trade_imbalance_15s",
    "tape_intensity_5s_z",
    // D) Microstructure (13)
    "obi_top1", "obi_top3", "obi_top10",
    "microprice", "microprice_minus_mid_bps", "obi_delta_5s",
    "delta_obi_top1_1s", "delta_microprice_1s",
    "depth_bid_top5", "depth_ask_top5", "depth_imbalance_top5",
    "depth_change_bid_1s", "depth_change_ask_1s",
    // E) Shocks (7)
    "liq_buy_vol_30s", "liq_sell_vol_30s", "liq_net_30s", "liq_count_30s",
    "mark_minus_mid_bps", "funding_rate", "funding_zscore",
    // F) Technicals (4)
    "ema200_distance_pct", "rsi_14", "bb_width", "bb_pos",
    // G) Account (4)
    "position_flag", "latent_pnl_pct", "max_pnl_pct", "current_drawdown_pct",
    // H) Time (2)
    "time_sin", "time_cos",
    // I) OI (5)
    "oi_value", "oi_delta_30s", "oi_delta_1m", "oi_delta_5m", "oi_zscore_30m",
    // J) Absorption (4) — Sprint 2
    "price_response_buy_5s", "price_response_sell_5s",
    "microprice_confirmation_5s", "breakout_failure_5s",
    // K) Persistence (7) — Sprint 2
    "obi_persistence_buy", "obi_persistence_sell",
    "flow_persistence_buy", "flow_persistence_sell",
    "spread_deterioration",
    "depth_deterioration_bid", "depth_deterioration_ask",
    // L) Regime (4) — Sprint 2
    "regime_trend", "regime_range", "regime_shock", "regime_dead",
];

/// Block definitions for ablation grouping (v6)
const BLOCKS: [(&str, usize, usize); 12] = [
    ("A_Price/Spread", 0, 4),
    ("B_Returns/Vol", 4, 14),
    ("C_Taker_Flow", 14, 24),
    ("D_Micro/Book", 24, 37),
    ("E_Shocks", 37, 44),
    ("F_Technicals", 44, 48),
    ("G_Account", 48, 52),
    ("H_Time", 52, 54),
    ("I_OI", 54, 59),
    ("J_Absorption", 59, 63),
    ("K_Persistence", 63, 70),
    ("L_Regime", 70, 74),
];

/// New features indices (Sprint 2 additions, v6 slots)
const NEW_FEATURE_INDICES: [usize; 15] = [
    59, 60,     // price_response_buy/sell_5s
    61, 62,     // microprice_confirmation_5s, breakout_failure_5s
    63, 64,     // obi_persistence_buy/sell
    65, 66,     // flow_persistence_buy/sell
    67,         // spread_deterioration
    68, 69,     // depth_deterioration_bid/ask
    70, 71, 72, 73, // regime_trend/range/shock/dead
];

struct FeatureStats {
    count: usize,
    none_count: usize,
    clamp_count: usize,
    sum: f64,
    sum_sq: f64,
    min: f64,
    max: f64,
    values: Vec<f64>,
}

impl FeatureStats {
    fn new() -> Self {
        Self {
            count: 0, none_count: 0, clamp_count: 0,
            sum: 0.0, sum_sq: 0.0,
            min: f64::MAX, max: f64::MIN,
            values: Vec::new(),
        }
    }

    fn add(&mut self, val: f32, mask: f32, clamped: bool) {
        self.count += 1;
        if mask < 0.5 {
            self.none_count += 1;
        } else {
            let v = val as f64;
            self.sum += v;
            self.sum_sq += v * v;
            if v < self.min { self.min = v; }
            if v > self.max { self.max = v; }
            self.values.push(v);
        }
        if clamped {
            self.clamp_count += 1;
        }
    }

    fn mean(&self) -> f64 {
        if self.values.is_empty() { return 0.0; }
        self.sum / self.values.len() as f64
    }

    fn std(&self) -> f64 {
        let n = self.values.len();
        if n < 2 { return 0.0; }
        let mean = self.mean();
        let var = self.sum_sq / n as f64 - mean * mean;
        var.max(0.0).sqrt()
    }

    fn percentile(&self, p: f64) -> f64 {
        if self.values.is_empty() { return 0.0; }
        let mut sorted = self.values.clone();
        sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let idx = ((p / 100.0) * (sorted.len() - 1) as f64).round() as usize;
        sorted[idx.min(sorted.len() - 1)]
    }

    fn none_rate(&self) -> f64 {
        if self.count == 0 { return 1.0; }
        self.none_count as f64 / self.count as f64
    }

    fn clamp_rate(&self) -> f64 {
        if self.count == 0 { return 0.0; }
        self.clamp_count as f64 / self.count as f64
    }
}

fn pearson_corr(a: &[f64], b: &[f64]) -> Option<f64> {
    if a.len() != b.len() || a.len() < 3 { return None; }
    let n = a.len() as f64;
    let sum_a: f64 = a.iter().sum();
    let sum_b: f64 = b.iter().sum();
    let sum_ab: f64 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let sum_a2: f64 = a.iter().map(|x| x * x).sum();
    let sum_b2: f64 = b.iter().map(|x| x * x).sum();
    let denom = ((n * sum_a2 - sum_a * sum_a) * (n * sum_b2 - sum_b * sum_b)).sqrt();
    if denom < 1e-12 { return None; }
    Some((n * sum_ab - sum_a * sum_b) / denom)
}

#[test]
fn feature_diagnostics_report() {
    // ── Build engine ──
    let config = FeatureEngineV2Config {
        interval_ms: 1000,
        symbol: "BTCUSDT".to_string(),
        telemetry_enabled: false,
        ..Default::default()
    };
    let mut engine = FeatureEngineV2::new(config);
    engine.set_orderbook_in_sync(true);
    engine.set_account_state(AccountState {
        position_flag: 0.0,
        latent_pnl_pct: 0.0,
        max_pnl_pct: 0.0,
        current_drawdown_pct: 0.0,
    });

    // ── Simulate realistic BTC-like market ──
    let base_price = 50000.0;
    let tick_size = 0.1;
    let base_spread = 0.5;
    let base_oi = 1_000_000.0;
    let base_funding = 0.0001;

    let mut rng_state: u64 = 42;
    let mut next_rng = |state: &mut u64| -> f64 {
        // Simple LCG for deterministic pseudo-random
        *state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
        ((*state >> 33) as f64) / (u32::MAX as f64) - 0.5
    };

    let mut mid = base_price;
    let mut oi = base_oi;
    let mut funding = base_funding;
    let mut rows: Vec<FeatureRow> = Vec::new();
    let mut all_obs: Vec<Vec<f32>> = Vec::new();
    let mut all_clamped: Vec<[bool; 74]> = Vec::new();

    for tick in 0..NUM_TICKS {
        let t_ms = 1000 + tick as i64 * 1000;

        // Random walk for mid price (mean-reverting around base)
        let noise = next_rng(&mut rng_state);
        mid += noise * 5.0; // ~5 USD per tick noise
        mid = mid * 0.999 + base_price * 0.001; // Mean revert

        // Spread: occasionally widens
        let spread_mul = if tick % 50 == 0 { 3.0 } else { 1.0 + (next_rng(&mut rng_state) * 0.4).abs() };
        let spread = base_spread * spread_mul;
        let bid = mid - spread / 2.0;
        let ask = mid + spread / 2.0;

        // Orderbook with time-varying imbalance
        let ob_imbalance = (tick as f64 * 0.1).sin() * 0.5; // oscillating imbalance
        let base_qty = 5.0 + next_rng(&mut rng_state).abs() * 3.0;
        let (ob_bids, ob_asks) = make_orderbook(mid, tick_size, base_qty, ob_imbalance);
        engine.set_orderbook_levels(ob_bids, ob_asks);

        // BookTicker event
        let ev_book = make_bookticker(t_ms, bid, ask);
        engine.update(&ev_book);

        // Trades: 1-5 per tick, alternating buy/sell dominance
        let n_trades = 1 + (next_rng(&mut rng_state).abs() * 4.0) as usize;
        let buy_bias = if tick % 20 < 10 { 0.7 } else { 0.3 };
        for j in 0..n_trades {
            let t_trade = t_ms + j as i64 * 50;
            let qty = 0.01 + next_rng(&mut rng_state).abs() * 0.5;
            let is_buyer_maker = next_rng(&mut rng_state) + 0.5 > buy_bias;
            let trade_price = mid + next_rng(&mut rng_state) * 0.5;
            let ev = make_aggtrade(t_trade, trade_price, qty, is_buyer_maker);
            engine.update(&ev);
        }

        // Mark price + funding: every 3 ticks
        if tick % 3 == 0 {
            let mark = mid + next_rng(&mut rng_state) * 2.0;
            funding = base_funding + next_rng(&mut rng_state) * 0.00005;
            let ev = make_mark_price(t_ms + 500, mark, funding);
            engine.update(&ev);
        }

        // OI: updates every 3 ticks with small drift
        if tick % 3 == 0 {
            oi += next_rng(&mut rng_state) * 5000.0;
            let ev = make_oi(t_ms + 600, oi);
            engine.update(&ev);
        }

        // Try emit
        let emit_ts = t_ms + 999;
        if let Some(row) = engine.maybe_emit(emit_ts) {
            let (obs, clamped) = row.to_obs_vec();
            all_obs.push(obs);
            all_clamped.push(clamped);
            rows.push(row);
        }
    }

    // ── Compute per-feature stats ──
    let n_features = 74;
    let mut stats: Vec<FeatureStats> = (0..n_features).map(|_| FeatureStats::new()).collect();

    for (obs, clamped) in all_obs.iter().zip(all_clamped.iter()) {
        for i in 0..n_features {
            stats[i].add(obs[i], obs[n_features + i], clamped[i]);
        }
    }

    println!("\n{}", "=".repeat(80));
    println!("SPRINT 1 FEATURE DIAGNOSTICS — {} emitted rows from {} ticks", rows.len(), NUM_TICKS);
    println!("{}\n", "=".repeat(80));

    // ── Table 1: All 60 features descriptive stats ──
    println!("{:<30} {:>8} {:>8} {:>10} {:>10} {:>10} {:>10} {:>10} {:>8} {:>8}",
        "Feature", "N_valid", "None%", "Mean", "Std", "P5", "P50", "P95", "Min", "Clamp%");
    println!("{}", "-".repeat(132));

    for i in 0..n_features {
        let s = &stats[i];
        let n_valid = s.values.len();
        println!("{:<30} {:>8} {:>7.1}% {:>10.4} {:>10.4} {:>10.4} {:>10.4} {:>10.4} {:>8.4} {:>7.1}%",
            FEATURE_NAMES[i],
            n_valid,
            s.none_rate() * 100.0,
            s.mean(),
            s.std(),
            s.percentile(5.0),
            s.percentile(50.0),
            s.percentile(95.0),
            if s.min == f64::MAX { 0.0 } else { s.min },
            s.clamp_rate() * 100.0,
        );
    }

    // ── Table 2: Correlation matrix for new features ──
    println!("\n\n{}", "=".repeat(80));
    println!("CORRELATION MATRIX (New Sprint 1 Features, |ρ| > 0.5 shown)");
    println!("{}\n", "=".repeat(80));

    println!("{:<28} {:<28} {:>8}", "Feature A", "Feature B", "ρ");
    println!("{}", "-".repeat(68));

    for (idx_a, &a_idx) in NEW_FEATURE_INDICES.iter().enumerate() {
        for &b_idx in NEW_FEATURE_INDICES.iter().skip(idx_a + 1) {
            // Align paired values (only where both have valid data)
            let mut vals_a = Vec::new();
            let mut vals_b = Vec::new();
            for obs in &all_obs {
                let mask_a = obs[n_features + a_idx];
                let mask_b = obs[n_features + b_idx];
                if mask_a > 0.5 && mask_b > 0.5 {
                    vals_a.push(obs[a_idx] as f64);
                    vals_b.push(obs[b_idx] as f64);
                }
            }
            if let Some(r) = pearson_corr(&vals_a, &vals_b) {
                if r.abs() > 0.5 {
                    println!("{:<28} {:<28} {:>8.3}", FEATURE_NAMES[a_idx], FEATURE_NAMES[b_idx], r);
                }
            }
        }
    }

    // ── Table 3: Block-level ablation coverage ──
    println!("\n\n{}", "=".repeat(80));
    println!("BLOCK-LEVEL ABLATION COVERAGE");
    println!("{}\n", "=".repeat(80));

    println!("{:<20} {:>6} {:>8} {:>8} {:>12} {:>12}",
        "Block", "Count", "V4cnt", "V5new", "AvgNone%", "AvgClamp%");
    println!("{}", "-".repeat(70));

    for &(name, start, end) in &BLOCKS {
        let count = end - start;
        let v5_new = NEW_FEATURE_INDICES.iter().filter(|&&i| i >= start && i < end).count();
        let v4_cnt = count - v5_new;
        let avg_none: f64 = (start..end).map(|i| stats[i].none_rate()).sum::<f64>() / count as f64;
        let avg_clamp: f64 = (start..end).map(|i| stats[i].clamp_rate()).sum::<f64>() / count as f64;
        println!("{:<20} {:>6} {:>8} {:>8} {:>11.1}% {:>11.1}%",
            name, count, v4_cnt, v5_new, avg_none * 100.0, avg_clamp * 100.0);
    }

    // ── Table 4: Specific formula review targets ──
    println!("\n\n{}", "=".repeat(80));
    println!("FORMULA REVIEW TARGETS");
    println!("{}\n", "=".repeat(80));

    let review_indices = [
        (3, "spread_vs_baseline"),
        (12, "slope_mid_5s"),
        (13, "slope_mid_15s"),
        (35, "depth_change_bid_1s"),
        (36, "depth_change_ask_1s"),
        (43, "funding_zscore"),
        (59, "price_response_buy_5s"),
        (60, "price_response_sell_5s"),
        (70, "regime_trend"),
    ];

    for &(idx, name) in &review_indices {
        let s = &stats[idx];
        println!("{}:", name);
        println!("  N_valid={}, None%={:.1}%, Clamp%={:.1}%",
            s.values.len(), s.none_rate() * 100.0, s.clamp_rate() * 100.0);
        println!("  Mean={:.6}, Std={:.6}", s.mean(), s.std());
        println!("  P1={:.6}, P5={:.6}, P25={:.6}, P50={:.6}, P75={:.6}, P95={:.6}, P99={:.6}",
            s.percentile(1.0), s.percentile(5.0), s.percentile(25.0), s.percentile(50.0),
            s.percentile(75.0), s.percentile(95.0), s.percentile(99.0));
        println!("  Range=[{:.6}, {:.6}]", if s.min == f64::MAX { 0.0 } else { s.min }, s.max);
        println!();
    }

    // ── Assertions: sanity checks ──
    assert!(rows.len() >= 500, "Should emit at least 500 rows from 600 ticks");

    // Trade imbalance must be bounded [-1, 1]
    for obs in &all_obs {
        for &idx in &[20, 21, 22] { // trade_imbalance_*
            let mask = obs[n_features + idx];
            if mask > 0.5 {
                assert!(obs[idx] >= -1.0 && obs[idx] <= 1.0,
                    "trade_imbalance out of [-1,1]: {}", obs[idx]);
            }
        }
    }

    // Depth values must be positive
    for obs in &all_obs {
        for &idx in &[32, 33] { // depth_bid/ask_top5
            let mask = obs[n_features + idx];
            if mask > 0.5 {
                assert!(obs[idx] > 0.0, "depth must be positive: {}", obs[idx]);
            }
        }
    }

    // OBI values bounded [-1, 1]
    for obs in &all_obs {
        for &idx in &[24, 25, 26] { // obi_top1/3/10
            let mask = obs[n_features + idx];
            if mask > 0.5 {
                assert!(obs[idx] >= -1.0 && obs[idx] <= 1.0,
                    "OBI out of [-1,1]: {}", obs[idx]);
            }
        }
    }

    // Sprint 2: Persistence bounded [0, 1]
    for obs in &all_obs {
        for &idx in &[63, 64, 65, 66, 67, 68, 69] { // persistence features
            let mask = obs[n_features + idx];
            if mask > 0.5 {
                assert!(obs[idx] >= 0.0 && obs[idx] <= 1.0,
                    "persistence[{}] out of [0,1]: {}", idx, obs[idx]);
            }
        }
    }

    // Sprint 2: Regime scores bounded [0, 1]
    for obs in &all_obs {
        for &idx in &[70, 71, 72, 73] { // regime scores
            let mask = obs[n_features + idx];
            if mask > 0.5 {
                assert!(obs[idx] >= 0.0 && obs[idx] <= 1.0,
                    "regime_score[{}] out of [0,1]: {}", idx, obs[idx]);
            }
        }
    }

    // Sprint 2: No NaN/Inf in absorption features
    for obs in &all_obs {
        for &idx in &[59, 60, 61, 62] { // absorption features
            let mask = obs[n_features + idx];
            if mask > 0.5 {
                assert!(obs[idx].is_finite(),
                    "absorption[{}] is NaN/Inf: {}", idx, obs[idx]);
            }
        }
    }

    println!("\n✅ All sanity checks passed.");
    println!("✅ {} features × {} rows = {} data points analyzed.", n_features, rows.len(), n_features * rows.len());
}
