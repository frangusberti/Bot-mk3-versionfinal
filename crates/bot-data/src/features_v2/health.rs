use std::collections::VecDeque;
use serde::{Serialize, Deserialize};

/// Diagnostic snapshot of feature health and data quality.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FeatureHealthReport {
    pub symbol: String,
    pub ts: i64,
    pub window_ms: i64,
    pub sample_count: usize,

    // Per-feature statistics (0..37)
    pub mask0_rate: Vec<f32>,
    pub clamp_rate: Vec<f32>,
    pub means: Vec<f32>,
    pub stds: Vec<f32>,
    pub mins: Vec<f32>,
    pub maxs: Vec<f32>,

    // Staleness (age in ms)
    pub book_age_ms: i64,
    pub trades_age_ms: i64,
    pub mark_age_ms: i64,
    pub funding_age_ms: i64,
    pub oi_age_ms: i64,

    // Global Score
    pub obs_quality: f32, // 0.0 to 1.0
    pub health_state: String, // "NORMAL", "DEGRADED"
}

const NUM_FEATURES: usize = 74;

#[derive(Clone)]
struct FeatureSample {
    ts: i64,
    values: [f32; NUM_FEATURES],
    masks: [f32; NUM_FEATURES],
    clamped: [bool; NUM_FEATURES],
}

pub struct FeatureHealthAggregator {
    symbol: String,
    window_ms: i64,
    samples: VecDeque<FeatureSample>,

    // Last update timestamps per group
    pub last_book_ts: i64,
    pub last_trades_ts: i64,
    pub last_mark_ts: i64,
    pub last_funding_ts: i64,
    pub last_oi_ts: i64,
}

impl FeatureHealthAggregator {
    pub fn new(symbol: String, window_ms: i64) -> Self {
        Self {
            symbol,
            window_ms,
            samples: VecDeque::with_capacity(600), // ~10 mins at 1Hz
            last_book_ts: 0,
            last_trades_ts: 0,
            last_mark_ts: 0,
            last_funding_ts: 0,
            last_oi_ts: 0,
        }
    }

    pub fn ingest(&mut self, ts: i64, values: Vec<f32>, masks: Vec<f32>, clamped: [bool; NUM_FEATURES]) {
        if values.len() < NUM_FEATURES || masks.len() < NUM_FEATURES {
            return;
        }

        let mut v_arr = [0.0; NUM_FEATURES];
        let mut m_arr = [0.0; NUM_FEATURES];
        v_arr.copy_from_slice(&values[0..NUM_FEATURES]);
        m_arr.copy_from_slice(&masks[0..NUM_FEATURES]);

        self.samples.push_back(FeatureSample {
            ts,
            values: v_arr,
            masks: m_arr,
            clamped,
        });

        // Purge old samples
        let cutoff = ts - self.window_ms;
        while let Some(front) = self.samples.front() {
            if front.ts < cutoff {
                self.samples.pop_front();
            } else {
                break;
            }
        }
    }

    pub fn snapshot(&self, current_ts: i64) -> FeatureHealthReport {
        let n = self.samples.len();
        let mut mask0_counts = [0usize; NUM_FEATURES];
        let mut clamp_counts = [0usize; NUM_FEATURES];
        let mut sums = [0.0f64; NUM_FEATURES];
        let mut sq_sums = [0.0f64; NUM_FEATURES];
        let mut mask1_counts = [0usize; NUM_FEATURES];
        let mut mins = [f32::MAX; NUM_FEATURES];
        let mut maxs = [f32::MIN; NUM_FEATURES];

        for s in &self.samples {
            for i in 0..NUM_FEATURES {
                if s.masks[i] < 0.5 {
                    mask0_counts[i] += 1;
                } else {
                    mask1_counts[i] += 1;
                    let v = s.values[i] as f64;
                    sums[i] += v;
                    sq_sums[i] += v * v;
                    if s.values[i] < mins[i] { mins[i] = s.values[i]; }
                    if s.values[i] > maxs[i] { maxs[i] = s.values[i]; }
                }

                if s.clamped[i] {
                    clamp_counts[i] += 1;
                }
            }
        }

        let mut m0_rates = Vec::with_capacity(NUM_FEATURES);
        let mut c_rates = Vec::with_capacity(NUM_FEATURES);
        let mut means = Vec::with_capacity(NUM_FEATURES);
        let mut stds = Vec::with_capacity(NUM_FEATURES);
        let mut mins_vec = Vec::with_capacity(NUM_FEATURES);
        let mut maxs_vec = Vec::with_capacity(NUM_FEATURES);

        for i in 0..NUM_FEATURES {
            let m0_rate = if n > 0 { mask0_counts[i] as f32 / n as f32 } else { 1.0 };
            let c_rate = if n > 0 { clamp_counts[i] as f32 / n as f32 } else { 0.0 };
            
            let m1_n = mask1_counts[i];
            let mean = if m1_n > 0 { (sums[i] / m1_n as f64) as f32 } else { 0.0 };
            let variance = if m1_n > 1 {
                (sq_sums[i] / m1_n as f64 - (mean as f64 * mean as f64)).max(0.0)
            } else {
                0.0
            };
            let std = (variance.sqrt()) as f32;

            m0_rates.push(m0_rate);
            c_rates.push(c_rate);
            means.push(mean);
            stds.push(std);
            mins_vec.push(if m1_n > 0 { mins[i] } else { 0.0 });
            maxs_vec.push(if m1_n > 0 { maxs[i] } else { 0.0 });
        }

        let book_age = if self.last_book_ts > 0 { (current_ts - self.last_book_ts).max(0) } else { 999999 };
        let trades_age = if self.last_trades_ts > 0 { (current_ts - self.last_trades_ts).max(0) } else { 999999 };
        let mark_age = if self.last_mark_ts > 0 { (current_ts - self.last_mark_ts).max(0) } else { 999999 };
        let funding_age = if self.last_funding_ts > 0 { (current_ts - self.last_funding_ts).max(0) } else { 999999 };
        let oi_age = if self.last_oi_ts > 0 { (current_ts - self.last_oi_ts).max(0) } else { 999999 };

        // Simple quality heuristic: average of non-mask0 rate for critical features
        // mid_price(0), spread_bps(2), obi_top1(14), microprice(16)
        let critical_indices = [0, 2, 14, 16];
        let mut crit_sum = 0.0;
        for &idx in &critical_indices {
            crit_sum += 1.0 - m0_rates[idx];
        }
        let obs_quality = crit_sum / critical_indices.len() as f32;

        let mut health_state = "NORMAL".to_string();
        if obs_quality < 0.95 || book_age > 5000 || trades_age > 60000 {
            health_state = "DEGRADED".to_string();
        }

        FeatureHealthReport {
            symbol: self.symbol.clone(),
            ts: current_ts,
            window_ms: self.window_ms,
            sample_count: n,
            mask0_rate: m0_rates,
            clamp_rate: c_rates,
            means,
            stds,
            mins: mins_vec,
            maxs: maxs_vec,
            book_age_ms: book_age,
            trades_age_ms: trades_age,
            mark_age_ms: mark_age,
            funding_age_ms: funding_age,
            oi_age_ms: oi_age,
            obs_quality,
            health_state,
        }
    }
}
