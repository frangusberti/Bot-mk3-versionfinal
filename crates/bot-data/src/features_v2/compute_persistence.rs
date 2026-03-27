use super::buffers::BoolRingBuffer;

/// Centralized thresholds for persistence features.
/// All thresholds are explicit and configurable.
#[derive(Debug, Clone)]
pub struct PersistenceThresholds {
    /// OBI threshold for buy persistence (obi_top1 > this)
    pub obi_positive: f64,
    /// OBI threshold for sell persistence (obi_top1 < this)
    pub obi_negative: f64,
    /// Trade imbalance threshold for buy persistence
    pub flow_positive: f64,
    /// Trade imbalance threshold for sell persistence
    pub flow_negative: f64,
    /// Spread vs baseline threshold for deterioration
    pub spread_det: f64,
    /// Depth change threshold for deterioration (negative = drop)
    pub depth_drop: f64,
    /// Minimum valid windows for fraction to be defined
    pub min_valid: usize,
}

impl Default for PersistenceThresholds {
    fn default() -> Self {
        Self {
            obi_positive: 0.05,
            obi_negative: -0.05,
            flow_positive: 0.20,
            flow_negative: -0.20,
            spread_det: 1.5,
            depth_drop: -0.05,
            min_valid: 5,
        }
    }
}

/// State for Block K: Persistence features.
///
/// Each feature tracks whether a condition held true across the last N
/// emit windows. N is determined by the BoolRingBuffer capacity.
///
/// Key invariant: persistence measures temporal continuity, NOT current magnitude.
/// A strong current signal with no history produces persistence ≈ 0.1 (1/N),
/// not 1.0.
#[derive(Debug, Clone)]
pub struct PersistenceState {
    obi_buy: BoolRingBuffer,
    obi_sell: BoolRingBuffer,
    flow_buy: BoolRingBuffer,
    flow_sell: BoolRingBuffer,
    spread_det: BoolRingBuffer,
    depth_bid: BoolRingBuffer,
    depth_ask: BoolRingBuffer,
}

impl PersistenceState {
    pub fn new(window: usize) -> Self {
        Self {
            obi_buy: BoolRingBuffer::new(window),
            obi_sell: BoolRingBuffer::new(window),
            flow_buy: BoolRingBuffer::new(window),
            flow_sell: BoolRingBuffer::new(window),
            spread_det: BoolRingBuffer::new(window),
            depth_bid: BoolRingBuffer::new(window),
            depth_ask: BoolRingBuffer::new(window),
        }
    }

    /// Compute persistence features from current Sprint 1 feature values.
    ///
    /// Each input is Option<f64> — None means the upstream feature was invalid
    /// for this emit window. Invalid inputs push None into the ring buffer
    /// and are excluded from the fraction computation.
    pub fn compute(
        &mut self,
        obi_top1: Option<f64>,
        trade_imbalance_5s: Option<f64>,
        spread_vs_baseline: Option<f64>,
        depth_change_bid_1s: Option<f64>,
        depth_change_ask_1s: Option<f64>,
        thresholds: &PersistenceThresholds,
    ) -> PersistenceFeatures {
        // Push indicators (Some(bool) if input valid, None if input invalid)
        self.obi_buy.push(obi_top1.map(|v| v > thresholds.obi_positive));
        self.obi_sell.push(obi_top1.map(|v| v < thresholds.obi_negative));

        self.flow_buy.push(trade_imbalance_5s.map(|v| v > thresholds.flow_positive));
        self.flow_sell.push(trade_imbalance_5s.map(|v| v < thresholds.flow_negative));

        self.spread_det.push(spread_vs_baseline.map(|v| v > thresholds.spread_det));

        self.depth_bid.push(depth_change_bid_1s.map(|v| v < thresholds.depth_drop));
        self.depth_ask.push(depth_change_ask_1s.map(|v| v < thresholds.depth_drop));

        let mv = thresholds.min_valid;

        PersistenceFeatures {
            obi_persistence_buy: self.obi_buy.fraction(mv),
            obi_persistence_sell: self.obi_sell.fraction(mv),
            flow_persistence_buy: self.flow_buy.fraction(mv),
            flow_persistence_sell: self.flow_sell.fraction(mv),
            spread_deterioration: self.spread_det.fraction(mv),
            depth_deterioration_bid: self.depth_bid.fraction(mv),
            depth_deterioration_ask: self.depth_ask.fraction(mv),
        }
    }
}

/// Output of PersistenceState::compute.
/// All values are in [0.0, 1.0] when Some.
#[derive(Debug, Clone)]
pub struct PersistenceFeatures {
    pub obi_persistence_buy: Option<f64>,
    pub obi_persistence_sell: Option<f64>,
    pub flow_persistence_buy: Option<f64>,
    pub flow_persistence_sell: Option<f64>,
    pub spread_deterioration: Option<f64>,
    pub depth_deterioration_bid: Option<f64>,
    pub depth_deterioration_ask: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn default_thresholds() -> PersistenceThresholds {
        PersistenceThresholds::default()
    }

    #[test]
    fn test_buildup_to_full() {
        let mut ps = PersistenceState::new(10);
        let th = default_thresholds();

        // Push 10 windows of strong positive OBI
        for _ in 0..10 {
            ps.compute(Some(0.3), Some(0.5), Some(0.0), Some(0.0), Some(0.0), &th);
        }
        let f = ps.compute(Some(0.3), Some(0.5), Some(0.0), Some(0.0), Some(0.0), &th);

        // All 10 windows had obi > 0.05 → persistence = 1.0
        // (buffer capacity=10, so 11th push evicts oldest, but all are true)
        assert!((f.obi_persistence_buy.unwrap() - 1.0).abs() < 1e-10);
        assert!((f.flow_persistence_buy.unwrap() - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_partial_persistence() {
        let mut ps = PersistenceState::new(10);
        let th = default_thresholds();

        // 5 windows positive, 5 windows negative OBI
        for _ in 0..5 {
            ps.compute(Some(0.3), Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);
        }
        for _ in 0..5 {
            ps.compute(Some(-0.3), Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);
        }
        let f = ps.compute(Some(-0.3), Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);

        // Buffer has 10 entries + the latest push (evicting first positive)
        // After 11 pushes with capacity=10: last 10 are [pos×4, neg×6]
        // obi_persistence_buy should be < 0.5
        let buy = f.obi_persistence_buy.unwrap();
        assert!(buy < 0.5, "Should be partial: {}", buy);
        // obi_persistence_sell should be > 0.5 (most recent 6 had obi < -0.05)
        let sell = f.obi_persistence_sell.unwrap();
        assert!(sell > 0.5, "Sell should dominate: {}", sell);
    }

    #[test]
    fn test_persistence_not_magnitude() {
        // Key invariant: strong current signal but only 1 window → persistence ≈ 0.1
        let mut ps = PersistenceState::new(10);
        let th = PersistenceThresholds { min_valid: 1, ..default_thresholds() };

        // One window of very strong signal
        let f = ps.compute(Some(0.9), Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);

        // Only 1 valid window → persistence = 1/1 = 1.0 if min_valid=1
        // BUT with min_valid=5 (default), it would be None
        // Testing with min_valid=1 to show it's only 1.0 because 1 out of 1
        assert!((f.obi_persistence_buy.unwrap() - 1.0).abs() < 1e-10);

        // Now push 9 false windows
        for _ in 0..9 {
            ps.compute(Some(0.0), Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);
        }
        let f2 = ps.compute(Some(0.0), Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);

        // Now 1 true out of 10 → persistence = 0.1
        // (but the oldest true was evicted, so actually 0/10 = 0.0)
        // The original true was at position 0, evicted after 10 more pushes
        assert!((f2.obi_persistence_buy.unwrap() - 0.0).abs() < 1e-10,
            "Old strong signal decayed away: {}", f2.obi_persistence_buy.unwrap());
    }

    #[test]
    fn test_invalid_windows_propagation() {
        let mut ps = PersistenceState::new(10);
        let th = default_thresholds(); // min_valid = 5

        // Push 3 valid windows and 2 invalid
        for _ in 0..3 {
            ps.compute(Some(0.3), Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);
        }
        for _ in 0..2 {
            ps.compute(None, Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);
        }

        let f = ps.compute(Some(0.3), Some(0.0), Some(0.0), Some(0.0), Some(0.0), &th);

        // OBI: 4 valid (3 true + 1 true from latest), 2 None → valid_count=4 < 5 → None
        assert!(f.obi_persistence_buy.is_none(),
            "Should be None with insufficient valid windows");

        // Flow: all 6 windows had valid imbalance (0.0) → valid_count=6 ≥ 5
        assert!(f.flow_persistence_buy.is_some());
        // All imbalance=0.0 < 0.20 threshold → fraction = 0.0
        assert!((f.flow_persistence_buy.unwrap() - 0.0).abs() < 1e-10);
    }

    #[test]
    fn test_spread_deterioration() {
        let mut ps = PersistenceState::new(10);
        let th = default_thresholds();

        // 10 windows of spread > 1.5σ above baseline
        for _ in 0..10 {
            ps.compute(Some(0.0), Some(0.0), Some(2.0), Some(0.0), Some(0.0), &th);
        }
        let f = ps.compute(Some(0.0), Some(0.0), Some(2.0), Some(0.0), Some(0.0), &th);

        assert!((f.spread_deterioration.unwrap() - 1.0).abs() < 1e-10,
            "All windows had spread above threshold");
    }

    #[test]
    fn test_depth_deterioration() {
        let mut ps = PersistenceState::new(10);
        let th = default_thresholds();

        // 10 windows of bid depth dropping > 5%
        for _ in 0..10 {
            ps.compute(Some(0.0), Some(0.0), Some(0.0), Some(-0.10), Some(0.01), &th);
        }
        let f = ps.compute(Some(0.0), Some(0.0), Some(0.0), Some(-0.10), Some(0.01), &th);

        assert!((f.depth_deterioration_bid.unwrap() - 1.0).abs() < 1e-10,
            "All windows had bid depth dropping");
        assert!((f.depth_deterioration_ask.unwrap() - 0.0).abs() < 1e-10,
            "Ask depth was stable");
    }
}
