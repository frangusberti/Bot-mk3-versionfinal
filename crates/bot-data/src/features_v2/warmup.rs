/// Warmup tracking for the FeatureEngineV2.
/// Tracks whether each feature group has received enough data to produce valid outputs.
#[derive(Debug, Clone, Default)]
pub struct WarmupTracker {
    /// Number of emit cycles seen so far
    pub emit_count: u64,
    /// Whether we have valid BBO data
    pub has_bbo: bool,
    /// Whether we have received any trade data
    pub has_trades: bool,
    /// Whether orderbook is in sync
    pub orderbook_in_sync: bool,
}

impl WarmupTracker {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn record_emit(&mut self) {
        self.emit_count += 1;
    }

    /// Minimum readiness: we have BBO data and orderbook is in sync.
    /// Trading should not start until this returns true.
    pub fn is_ready(&self) -> bool {
        self.has_bbo && self.orderbook_in_sync
    }

    /// Whether returns are available (need >= 2 emit cycles for ret_1s).
    pub fn has_returns(&self) -> bool {
        self.emit_count >= 2
    }

    /// Whether rv_30s is available (need >= 31 samples).
    pub fn has_rv_30s(&self) -> bool {
        self.emit_count >= 31
    }

    /// Whether technicals (EMA200) are fully warmed up.
    pub fn has_technicals(&self) -> bool {
        self.emit_count >= 201
    }
}
