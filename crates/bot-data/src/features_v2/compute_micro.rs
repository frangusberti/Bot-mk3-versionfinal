use super::buffers::RingBuffer;

/// State for Group D: Microstructure features from the order book.
#[derive(Debug, Clone)]
pub struct MicroState {
    /// History of OBI top1 values for computing obi_delta
    obi_top1_history: RingBuffer,  // 6 entries for 5s delta
    /// History of microprice for computing deltas
    microprice_history: RingBuffer, // 6 entries
    /// History of bid depth sums for depth_change
    depth_bid_history: RingBuffer,  // 2 entries (current + prev)
    /// History of ask depth sums for depth_change
    depth_ask_history: RingBuffer,  // 2 entries
}

impl MicroState {
    pub fn new() -> Self {
        Self::default()
    }
}

impl Default for MicroState {
    fn default() -> Self {
        Self {
            obi_top1_history: RingBuffer::new(6),
            microprice_history: RingBuffer::new(6),
            depth_bid_history: RingBuffer::new(2),
            depth_ask_history: RingBuffer::new(2),
        }
    }
}

impl MicroState {

    /// Compute microstructure features from current orderbook state.
    ///
    /// # Arguments
    /// * `top_bids` — top N bid levels [(price, qty), ...] sorted best→worst
    /// * `top_asks` — top N ask levels [(price, qty), ...] sorted best→worst
    /// * `mid_price` — current mid price
    pub fn compute(
        &mut self,
        top_bids: &[(f64, f64)],
        top_asks: &[(f64, f64)],
        mid_price: f64,
        strict_in_sync: bool,
    ) -> MicroFeatures {
        if !strict_in_sync {
            return MicroFeatures {
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
            };
        }

        // ── OBI at multiple depths ──
        let obi_top1 = if let (Some(&(_, bq)), Some(&(_, aq))) = (top_bids.first(), top_asks.first()) {
            let total = bq + aq;
            if total > 0.0 { Some((bq - aq) / total) } else { None }
        } else {
            None
        };

        let obi_top3 = {
            let bid_qty: f64 = top_bids.iter().take(3).map(|(_, q)| q).sum();
            let ask_qty: f64 = top_asks.iter().take(3).map(|(_, q)| q).sum();
            let total = bid_qty + ask_qty;
            if total > 0.0 { Some((bid_qty - ask_qty) / total) } else { None }
        };

        let obi_top10 = {
            let bid_qty: f64 = top_bids.iter().take(10).map(|(_, q)| q).sum();
            let ask_qty: f64 = top_asks.iter().take(10).map(|(_, q)| q).sum();
            let total = bid_qty + ask_qty;
            if total > 0.0 { Some((bid_qty - ask_qty) / total) } else { None }
        };

        // ── Microprice ──
        let (microprice, microprice_minus_mid_bps) = if let (Some(&(bp, bq)), Some(&(ap, aq))) = (top_bids.first(), top_asks.first()) {
            let total = bq + aq;
            if total > 0.0 && mid_price > 0.0 {
                let mp = (bp * aq + ap * bq) / total;
                let dist_bps = (mp - mid_price) / mid_price * 10_000.0;
                (Some(mp), Some(dist_bps))
            } else {
                (None, None)
            }
        } else {
            (None, None)
        };

        // ── Depth sums (top 5 levels) ──
        let depth_bid_top5: f64 = top_bids.iter().take(5).map(|(_, q)| q).sum();
        let depth_ask_top5: f64 = top_asks.iter().take(5).map(|(_, q)| q).sum();
        let depth_imbalance_top5 = {
            let total = depth_bid_top5 + depth_ask_top5;
            if total > 0.0 { Some((depth_bid_top5 - depth_ask_top5) / total) } else { None }
        };

        // ── OBI Delta 5s & 1s ──
        let obi_delta_5s = if let Some(obi) = obi_top1 {
            let prev_5s = self.obi_top1_history.get(4); // 5 ticks ago
            self.obi_top1_history.push(obi);
            prev_5s.map(|p| obi - p)
        } else {
            None
        };

        let delta_obi_top1_1s = if self.obi_top1_history.len() >= 2 {
            match (self.obi_top1_history.get(0), self.obi_top1_history.get(1)) {
                (Some(now), Some(prev)) => Some(now - prev),
                _ => None,
            }
        } else {
            None
        };

        // ── Microprice delta 1s ──
        let delta_microprice_1s = if let Some(mp) = microprice {
            let prev = self.microprice_history.get(0); // previous tick
            self.microprice_history.push(mp);
            prev.and_then(|p| {
                if mid_price > 0.0 {
                    Some((mp - p) / mid_price * 10_000.0) // in bps
                } else {
                    None
                }
            })
        } else {
            None
        };

        // ── Depth change 1s ──
        let depth_change_bid_1s = {
            let prev = self.depth_bid_history.get(0);
            self.depth_bid_history.push(depth_bid_top5);
            prev.map(|p| if p > 0.0 { (depth_bid_top5 - p) / p } else { 0.0 })
        };

        let depth_change_ask_1s = {
            let prev = self.depth_ask_history.get(0);
            self.depth_ask_history.push(depth_ask_top5);
            prev.map(|p| if p > 0.0 { (depth_ask_top5 - p) / p } else { 0.0 })
        };

        MicroFeatures {
            obi_top1,
            obi_top3,
            obi_top10,
            microprice,
            microprice_minus_mid_bps,
            obi_delta_5s,
            delta_obi_top1_1s,
            delta_microprice_1s,
            depth_bid_top5: Some(depth_bid_top5),
            depth_ask_top5: Some(depth_ask_top5),
            depth_imbalance_top5,
            depth_change_bid_1s,
            depth_change_ask_1s,
        }
    }
}

#[derive(Debug, Clone)]
pub struct MicroFeatures {
    pub obi_top1: Option<f64>,
    pub obi_top3: Option<f64>,
    pub obi_top10: Option<f64>,
    pub microprice: Option<f64>,
    pub microprice_minus_mid_bps: Option<f64>,
    pub obi_delta_5s: Option<f64>,
    // New dynamic features
    pub delta_obi_top1_1s: Option<f64>,
    pub delta_microprice_1s: Option<f64>,
    pub depth_bid_top5: Option<f64>,
    pub depth_ask_top5: Option<f64>,
    pub depth_imbalance_top5: Option<f64>,
    pub depth_change_bid_1s: Option<f64>,
    pub depth_change_ask_1s: Option<f64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_book() -> (Vec<(f64, f64)>, Vec<(f64, f64)>) {
        let bids = vec![
            (50000.0, 10.0), (49999.0, 5.0), (49998.0, 3.0),
            (49997.0, 8.0), (49996.0, 4.0), (49995.0, 2.0),
            (49994.0, 1.0), (49993.0, 6.0), (49992.0, 3.0),
            (49991.0, 7.0),
        ];
        let asks = vec![
            (50001.0, 5.0), (50002.0, 8.0), (50003.0, 2.0),
            (50004.0, 6.0), (50005.0, 3.0), (50006.0, 4.0),
            (50007.0, 1.0), (50008.0, 5.0), (50009.0, 2.0),
            (50010.0, 9.0),
        ];
        (bids, asks)
    }

    #[test]
    fn test_obi_top1() {
        let mut ms = MicroState::new();
        let (bids, asks) = sample_book();
        let f = ms.compute(&bids, &asks, 50000.5, true);

        let obi = f.obi_top1.unwrap();
        assert!((obi - 0.3333).abs() < 0.01);
    }

    #[test]
    fn test_obi_top10() {
        let mut ms = MicroState::new();
        let (bids, asks) = sample_book();
        let f = ms.compute(&bids, &asks, 50000.5, true);

        assert!(f.obi_top10.is_some());
        let bid_sum: f64 = bids.iter().take(10).map(|(_, q)| q).sum();
        let ask_sum: f64 = asks.iter().take(10).map(|(_, q)| q).sum();
        let expected = (bid_sum - ask_sum) / (bid_sum + ask_sum);
        assert!((f.obi_top10.unwrap() - expected).abs() < 1e-10);
    }

    #[test]
    fn test_depth_tracking() {
        let mut ms = MicroState::new();
        let (bids, asks) = sample_book();
        let f = ms.compute(&bids, &asks, 50000.5, true);

        // Depth top 5: bids = 10+5+3+8+4 = 30, asks = 5+8+2+6+3 = 24
        assert!((f.depth_bid_top5.unwrap() - 30.0).abs() < 1e-10);
        assert!((f.depth_ask_top5.unwrap() - 24.0).abs() < 1e-10);
        // Imbalance = (30-24)/(30+24) = 6/54
        let expected_imb = 6.0 / 54.0;
        assert!((f.depth_imbalance_top5.unwrap() - expected_imb).abs() < 1e-6);
    }

    #[test]
    fn test_depth_change() {
        let mut ms = MicroState::new();
        let (bids, asks) = sample_book();

        // First tick: no previous
        let f1 = ms.compute(&bids, &asks, 50000.5, true);
        assert!(f1.depth_change_bid_1s.is_none());

        // Second tick: should have change
        let f2 = ms.compute(&bids, &asks, 50000.5, true);
        assert!(f2.depth_change_bid_1s.is_some());
        assert!((f2.depth_change_bid_1s.unwrap()).abs() < 1e-10); // Same depth → 0 change
    }

    #[test]
    fn test_microprice() {
        let mut ms = MicroState::new();
        let bids = vec![(100.0, 10.0)];
        let asks = vec![(102.0, 5.0)];
        let f = ms.compute(&bids, &asks, 101.0, true);

        let mp = f.microprice.unwrap();
        assert!((mp - 101.333).abs() < 0.01);
    }

    #[test]
    fn test_delta_microprice() {
        let mut ms = MicroState::new();
        let bids1 = vec![(100.0, 10.0)];
        let asks1 = vec![(102.0, 5.0)];
        ms.compute(&bids1, &asks1, 101.0, true);

        // Shift prices slightly
        let bids2 = vec![(100.5, 10.0)];
        let asks2 = vec![(102.5, 5.0)];
        let f2 = ms.compute(&bids2, &asks2, 101.5, true);

        assert!(f2.delta_microprice_1s.is_some());
        assert!(f2.delta_microprice_1s.unwrap() > 0.0, "Price up → delta > 0");
    }

    #[test]
    fn test_strict_gating() {
        let mut ms = MicroState::new();
        let bids = vec![(100.0, 10.0)];
        let asks = vec![(102.0, 5.0)];

        let f = ms.compute(&bids, &asks, 101.0, false);
        assert!(f.microprice.is_none());
        assert!(f.obi_top1.is_none());
        assert!(f.depth_bid_top5.is_none());
        assert!(f.depth_imbalance_top5.is_none());

        let f2 = ms.compute(&bids, &asks, 101.0, true);
        assert!(f2.microprice.is_some());
    }
}
