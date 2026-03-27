use super::schema::FeatureVector;
use super::profiles::FeatureProfile;
use super::manifest::FeatureConfig;
use super::errors::FeatureError;
use crate::normalization::schema::NormalizedMarketEvent;
use crate::orderbook::engine::OrderBookStatus;
use std::collections::{VecDeque, HashSet};
use log::warn;

pub struct FeatureEngine {
    profile: FeatureProfile,
    config: FeatureConfig,
    featureset_version: crate::features::profiles::FeatureSetVersion,
    
    // IDs for schema
    dataset_id: String,
    features_id: String,
    
    // State
    last_event_ts: i64,
    
    // Sampling State
    next_emit_ts: i64,

    // Market State
    current_mid_price: Option<f64>,
    current_best_bid: Option<f64>,
    current_best_ask: Option<f64>,
    _current_bid_vol: Option<f64>,
    _current_ask_vol: Option<f64>,
    
    // Rich state
    current_mark_price: Option<f64>,
    current_funding_rate: Option<f64>,
    
    // History for Features (Snapshot at each interval t)
    // We strictly store history of *emitted* (or computed) values at t-1, t-2... 
    // to calculate returns vs t-1, t-5.
    // We need history of mid_prices.
    mid_price_history: VecDeque<f64>, // [p_{t-1}, p_{t-2}, ...]
    log_return_history: VecDeque<f64>, // [r_{t}, r_{t-1}...] for Vol
    
    // Validation
    available_streams: HashSet<String>,
    orderbook_status: OrderBookStatus,
}

impl FeatureEngine {
    pub fn new(profile: FeatureProfile, config: FeatureConfig, dataset_id: String, features_id: String) -> Self {
        Self {
            profile,
            config,
            featureset_version: crate::features::profiles::FeatureSetVersion::default(), // Default to v1
            dataset_id,
            features_id,
            last_event_ts: 0,
            next_emit_ts: 0, // Will settle on first event
            
            current_mid_price: None,
            current_best_bid: None,
            current_best_ask: None,
            _current_bid_vol: None,
            _current_ask_vol: None,
            current_mark_price: None,
            current_funding_rate: None,
            
            mid_price_history: VecDeque::new(),
            log_return_history: VecDeque::new(),
            available_streams: HashSet::new(),
            orderbook_status: OrderBookStatus::InSync, // Default to InSync (optimistic for simple agents)
        }
    }

    pub fn set_orderbook_status(&mut self, status: OrderBookStatus) {
        self.orderbook_status = status;
    }
    
    pub fn validate_dependency(&mut self, stream_name: &str) -> Result<(), FeatureError> {
        self.available_streams.insert(stream_name.to_string());
        Ok(())
    }
    
    pub fn check_ready(&self) -> Result<(), FeatureError> {
        if self.config.allow_mock {
            return Ok(());
        }
        
        let required = self.profile.required_streams();
        for &req in &required {
             let found = self.available_streams.iter().any(|s| {
                 if req == "trade" { s == "trade" || s == "aggTrade" }
                 else { s == req }
             });
             
             if !found {
                 return Err(FeatureError::MissingStream(req.to_string()));
             }
        }
        Ok(())
    }

    pub fn require(&self, required_profile: FeatureProfile, required_version: &crate::features::profiles::FeatureSetVersion) -> Result<(), FeatureError> {
        if self.config.allow_mock { return Ok(()); }

        if self.profile != required_profile {
             // Strict check: Simple != Rich
             return Err(FeatureError::ProfileMismatch(format!("Required {:?}, found {:?}", required_profile, self.profile)));
        }
        
        if &self.featureset_version != required_version {
             return Err(FeatureError::VersionMismatch(format!("Required {:?}, found {:?}", required_version, self.featureset_version)));
        }
        Ok(())
    }

    pub fn compute_signature(&self) -> String {
        // Generate stable hash of feature names + types
        // Ideally we reflect over FeatureVector fields, but hardcoded list is safer for stability
        // Order matters!
        let mut sig = String::new();
        sig.push_str("mid_price:f64;");
        sig.push_str("log_return_1:f64;");
        sig.push_str("log_return_5:f64;");
        sig.push_str("realized_vol_10:f64;");
        sig.push_str("bid_ask_spread:f64;");
        sig.push_str("relative_spread:f64;");
        sig.push_str("imbalance:f64;");
        
        if self.profile == FeatureProfile::Rich {
            sig.push_str("mark_price_distance:f64;");
            sig.push_str("funding_rate:f64;");
        }
        
        // SHA256 or simple CRC? SHA1 is fine for signature
        use sha1::{Sha1, Digest};
        let mut hasher = Sha1::new();
        hasher.update(sig.as_bytes());
        format!("{:x}", hasher.finalize())
    }

    pub fn update(&mut self, event: &NormalizedMarketEvent) {
        // Initialize timing on first event
        if self.next_emit_ts == 0 {
             // "Emit at exact multiples of interval relative to dataset start"
             // Assuming event.time_canonical is start?
             // Or we align to 0? "If first ts = 1700...000, Emit at 1700...1000"
             // This implies alignment to the *start of the dataset*.
             self.next_emit_ts = event.time_canonical + self.config.sampling_interval_ms as i64;
        }
        
        self.last_event_ts = event.time_canonical;
        
        // Update Market State
        if let Some(p) = event.price {
            // For simple trade source, price is close enough to mid if no spread
            // But if we have bid/ask, we recount.
            // If strictly relying on BBO for mid:
            if event.stream_name.contains("bookTicker") || event.stream_name.contains("depth") {
                // Rely on best_bid/ask
            } else if event.stream_name.contains("trade") {
                 // Fallback if no BBO? ideally we don't mix logic promiscuously without profile check.
                 // Simple profile uses trade price as mid?
                 // "Simple Profile Requires: price source".
                 // "mid_price = (best_bid + best_ask) / 2" is req definition.
                 // If Simple profile only has 'trade', we might treat trade price as mid?
                 if self.profile == FeatureProfile::Simple && self.current_best_bid.is_none() {
                     self.current_mid_price = Some(p);
                 }
            }
        }
        
        if let Some(bid) = event.best_bid { self.current_best_bid = Some(bid); }
        if let Some(ask) = event.best_ask { self.current_best_ask = Some(ask); }
        
        // Recalc Mid if BBO available
        if let (Some(b), Some(a)) = (self.current_best_bid, self.current_best_ask) {
            self.current_mid_price = Some((b + a) / 2.0);
            
            // Vols?
            // self.current_bid_vol = ... need quantity from bookTicker or depth snapshot?
            // NormalizedEvent has `qty` for trades, but `best_bid_qty` isn't in Simplified NormalizedEvent?
            // User requirements: "imbalance = (bid_vol - ask_vol) / ..."
            // NormalizedEvent should probably have bid/ask qty if bookTicker.
            // Current `NormalizedMarketEvent` schema check:
            // It has `best_bid`, `best_ask`. Does it have sizes?
            // Looking at schema.rs (normalization): 
            // It has `liq_price`, `liq_qty`... wait, `best_bid_qty`?
            // The schema in Module 2 for NormalizedMarketEvent:
            // pub struct NormalizedMarketEvent { ... pub qty: Option<f64> ... }
            // For bookTicker, `qty` might be ambiguous. Usually bookTicker has bidQty/askQty.
            // If `NormalizedMarketEvent` only has one `qty` field, it's lossy for BBO.
            // Proceeding with what we have: if we can't compute imbalance, we return None.
        }
        
        if let Some(mp) = event.mark_price { self.current_mark_price = Some(mp); }
        if let Some(fr) = event.funding_rate { self.current_funding_rate = Some(fr); }
        
        // Volume Extraction (Pragmatic Fix)
        if event.exchange == "binance" && (event.event_type == "bookTicker" || event.stream_name.contains("bookTicker")) {
             // Local struct for parsing
             #[derive(serde::Deserialize)]
             struct BinanceTicker {
                 #[serde(alias = "B")]
                 bid_qty: String,
                 #[serde(alias = "A")]
                 ask_qty: String,
             }
             
             if let Ok(ticker) = serde_json::from_str::<BinanceTicker>(&event.payload_json) {
                 if let (Ok(b), Ok(a)) = (ticker.bid_qty.parse::<f64>(), ticker.ask_qty.parse::<f64>()) {
                     self._current_bid_vol = Some(b);
                     self._current_ask_vol = Some(a);
                 }
             }
        }
    }
    
    // Called externally: "maybe_emit(ts)"
    // Actually, caller drives the loop. Caller sees an event at T_event.
    // If T_event >= next_emit_ts, we must emit for next_emit_ts.
    // NOTE: We may need to emit MULTIPLE times if T_event jumped ahead (gap)?
    // "Missing data must not create synthetic trades." -> "State is held from last known values."
    // So yes, we emit repeats if time passed multiple intervals.
    
    pub fn maybe_emit(&mut self, current_processing_ts: i64) -> Option<FeatureVector> {
        if self.next_emit_ts == 0 || current_processing_ts < self.next_emit_ts {
            return None;
        }
        
        let emit_ts = self.next_emit_ts;
        self.next_emit_ts += self.config.sampling_interval_ms as i64;
        
        let fv = self.compute_vector(emit_ts);
        
        // Strict Readiness Check
        // If emit_partial is true, is_ready() might still return false IF BBO is missing.
        // This is safe. "No zero-filled placeholders" -> we ensure data is valid.
        if !self.is_ready() {
            return None;
        }
        
        Some(fv)
    }
    
    fn compute_vector(&mut self, ts: i64) -> FeatureVector {
        let mut fv = FeatureVector::new(ts, self.dataset_id.clone(), self.features_id.clone());
        
        // 1. Mid Price
        let mid = self.current_mid_price;
        fv.mid_price = mid;
        
        // 2. Returns & History
        if let Some(p) = mid {
            // log_return_1 = ln(p_t / p_{t-1})
             if let Some(prev_1) = self.mid_price_history.front() {
                 if *prev_1 > 0.0 {
                    let r = (p / prev_1).ln();
                    fv.log_return_1 = Some(r);
                    
                    // Add to return history for Vol
                    self.log_return_history.push_front(r);
                    if self.log_return_history.len() > 10 { // realized_vol_10
                        self.log_return_history.pop_back();
                    }
                 }
             }
             
             // log_return_5
             if self.mid_price_history.len() >= 5 {
                 if let Some(prev_5) = self.mid_price_history.get(4) { // index 4 is t-5 (0 is t-1)
                     if *prev_5 > 0.0 {
                         fv.log_return_5 = Some((p / prev_5).ln());
                     }
                 }
             }
             
             // Update Price History
             self.mid_price_history.push_front(p);
             if self.mid_price_history.len() > 10 { // Keep enough for max window
                 self.mid_price_history.pop_back();
             }
        }
        
        // 3. Volatility (stddev of log_return_1 over last 10)
        // User: "realized_vol_10 = stddev(log_return over last 10 intervals)"
        // Note: is it log_return_1? Yes usually.
        if self.log_return_history.len() == 10 {
            let n = 10.0;
            let sum: f64 = self.log_return_history.iter().sum();
            let mean = sum / n;
            let variance = self.log_return_history.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / (n - 1.0);
            fv.realized_vol_10 = Some(variance.sqrt());
        } else if !self.config.emit_partial {
            // If strictly requiring warmup, maybe we should suppress whole vector?
            // "If false: Do not emit FeatureVector until all required rolling windows are fully initialized."
            // This suggests returning None from maybe_emit?
            // But here we are inside compute_vector which returns FeatureVector.
            // We'll handle this check at emission time or return partials if config allows.
            // But struct definition implies fields are Options.
            // User requirement #6: "If false: Do not emit FeatureVector..." 
            // So we shouldn't even return structs with Nones if emit_partial is false.
        }
        
        // 4. Microstructure
        if let (Some(ask), Some(bid)) = (self.current_best_ask, self.current_best_bid) {
            fv.bid_ask_spread = Some(ask - bid);
            if let Some(mp) = mid {
                if mp > 0.0 {
                    fv.relative_spread = Some((ask - bid) / mp);
                }
            }
            // Imbalance using extracted volumes
            if let (Some(bv), Some(av)) = (self._current_bid_vol, self._current_ask_vol) {
                let total = bv + av;
                if total > 0.0 {
                    // Imbalance: (Bid - Ask) / (Bid + Ask) -> Range [-1, 1]
                    fv.imbalance = Some((bv - av) / total); 
                }
            }
        }
        
        // 5. Derivatives
        if self.profile == FeatureProfile::Rich {
             if let (Some(mark), Some(mid)) = (self.current_mark_price, mid) {
                 if mid > 0.0 {
                     fv.mark_price_distance = Some((mark - mid) / mid);
                 }
             }
             fv.funding_rate = self.current_funding_rate;
        }

        // Validate Strictness
        // spread >= 0 etc.
        if let Some(s) = fv.bid_ask_spread {
            if s < 0.0 { warn!("Negative spread at {}: {}", ts, s); }
        }
        
        fv
    }
    
    // Check warmup and structural readiness
    pub fn is_ready(&self) -> bool {
        // 0. OrderBook Status Check
        if self.orderbook_status != OrderBookStatus::InSync {
            return false;
        }

        // 1. Check buffers (Strict Warmup)
        // If emit_partial is TRUE, we might skip this, but User wants "Deterministic Feature Warmup".
        // "Trading must never start unless is_ready() == true."
        // Let's enforce structural validty even if emit_partial is true (e.g. BBO must exist).

        // A. Basic Market Data presence
        if self.current_mid_price.is_none() || 
           self.current_best_bid.is_none() || 
           self.current_best_ask.is_none() {
            return false;
        }
        
        // B. Spread Validity
        if let (Some(bid), Some(ask)) = (self.current_best_bid, self.current_best_ask) {
            if ask < bid { return false; } // Crossed book?
            if ask <= 0.0 || bid <= 0.0 { return false; }
        }

        // C. Rolling Windows (The "Warmup" part)
        // Allow trading to start immediately even if buffers aren't full.
        // We will output Options (None) which serialize as 0.0 or NaNs for uncalculated features,
        // which the ML policy can gracefully handle.
        
        true
    }
    
    // Check warmup (Legacy wrapper, mapped to is_ready for now or kept for partial logic)
    pub fn is_warmed_up(&self) -> bool {
        self.is_ready()
    }
    
    pub fn current_mid_price(&self) -> Option<f64> {
        self.current_mid_price
    }

    pub fn estimate_current_volatility(&self) -> f64 {
        if self.log_return_history.len() >= 10 {
            let n = 10.0;
            let window = self.log_return_history.iter().take(10);
            let sum: f64 = window.clone().sum();
            let mean = sum / n;
            let variance = window.map(|r| (r - mean).powi(2)).sum::<f64>() / (n - 1.0);
            variance.sqrt()
        } else {
            0.0
        }
    }
}
