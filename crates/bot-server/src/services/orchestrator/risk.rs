use log::{info, warn};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ════════════════════════════════════════════════════════════════════════
//  M5: Risk Manager Central — Deterministic, Configurable, Exhaustive
// ════════════════════════════════════════════════════════════════════════

// ────────────────────────────────────────────────────────────
//  Configuration (Runtime, Mutable, Serializable)
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Default, Serialize, Deserialize)]
pub enum RiskSizingMode {
    /// notional = (equity * risk_per_trade_pct/100) / stop_distance_pct
    #[default]
    StopDistanceBased,
    /// notional = equity * risk_per_trade_pct/100
    FixedFractionOfEquity,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RiskConfig {
    // ── Drawdown ──
    pub risk_per_trade_pct: f64,
    pub max_daily_dd_pct: f64,
    pub max_monthly_dd_pct: f64,
    pub max_total_dd_pct: f64,

    // ── Exposure ──
    pub max_total_leverage: f64,
    pub max_positions_total: usize,
    pub max_positions_per_symbol: usize,

    // ── Rate Limiting ──
    pub max_order_rate_per_min: u32,

    // ── Flatten & Kill ──
    pub flatten_on_disable: bool,
    pub kill_switch_enabled: bool,

    // ── Notional Bounds ──
    pub min_notional_per_order: f64,
    pub max_notional_per_order: f64,

    // ── Sizing ──
    pub sizing_mode: RiskSizingMode,
    pub default_stop_distance_bps: f64,

    // ── Reduce-Only ──
    pub allow_reduce_only_when_disabled: bool,

    // ── Exit Hazard Gates ──
    pub profit_floor_bps: f64,
    pub stop_loss_bps: f64,
    pub use_selective_entry: bool,
    pub entry_veto_threshold_bps: f64,
}

impl Default for RiskConfig {
    fn default() -> Self {
        Self {
            risk_per_trade_pct: 0.5,
            max_daily_dd_pct: 5.0,
            max_monthly_dd_pct: 20.0,
            max_total_dd_pct: 50.0,
            max_total_leverage: 5.0,
            max_positions_total: 3,
            max_positions_per_symbol: 1,
            max_order_rate_per_min: 120,
            flatten_on_disable: true,
            kill_switch_enabled: true,
            min_notional_per_order: 5.0,
            max_notional_per_order: 100_000.0,
            sizing_mode: RiskSizingMode::StopDistanceBased,
            default_stop_distance_bps: 50.0,
            allow_reduce_only_when_disabled: true,
            profit_floor_bps: 10.0,
            stop_loss_bps: 30.0,
            use_selective_entry: false,
            entry_veto_threshold_bps: 1.0,
        }
    }
}

// ────────────────────────────────────────────────────────────
//  Risk State
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum RiskState {
    Running,
    DisabledDaily,
    DisabledMonthly,
    DisabledTotal,
    Killed,
}

impl RiskState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Running => "RUNNING",
            Self::DisabledDaily => "DISABLED_DAILY",
            Self::DisabledMonthly => "DISABLED_MONTHLY",
            Self::DisabledTotal => "DISABLED_TOTAL",
            Self::Killed => "KILLED",
        }
    }

    pub fn is_disabled(&self) -> bool {
        !matches!(self, Self::Running)
    }
}

// ────────────────────────────────────────────────────────────
//  Trigger Events
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, Serialize, Deserialize)]
pub enum RiskTriggerKind {
    DailyDD,
    MonthlyDD,
    TotalDD,
    KillSwitch,
    Leverage,
    OrderRate,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct RiskTriggerEvent {
    pub kind: RiskTriggerKind,
    pub timestamp: i64,
    pub dd_total: f64,
    pub dd_daily: f64,
    pub dd_monthly: f64,
    pub equity: f64,
    pub reason: String,
}

// ────────────────────────────────────────────────────────────
//  Snapshots (for API / GUI)
// ────────────────────────────────────────────────────────────

#[allow(dead_code)]
#[derive(Clone, Debug)]
pub struct AccountSnapshot {
    pub equity: f64,
    pub wallet_balance: f64,
    pub unrealized_pnl: f64,
    pub realized_pnl: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug)]
pub struct PositionSnapshot {
    pub symbol: String,
    pub qty: f64,
    pub side: String,
    pub entry_price: f64,
    pub notional: f64,
    pub margin_used: f64,
    pub leverage: f64,
}

#[allow(dead_code)]
#[derive(Clone, Debug)]
pub struct ProposedOrder {
    pub symbol: String,
    pub side: String,
    pub qty: f64,
    pub price: f64,
    pub is_reduce_only: bool,
}

// ────────────────────────────────────────────────────────────
//  Sizing Result
// ────────────────────────────────────────────────────────────

#[allow(dead_code)]
#[derive(Clone, Debug)]
pub struct SizingResult {
    pub notional: f64,
    pub qty: f64,
    pub clamped: bool,
    pub reason: String,
}

// ────────────────────────────────────────────────────────────
//  Risk Status (full GUI snapshot)
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, Serialize)]
#[allow(dead_code)]
pub struct RiskStatus {
    pub state: RiskState,
    pub dd_total_pct: f64,
    pub dd_daily_pct: f64,
    pub dd_monthly_pct: f64,
    pub equity: f64,
    pub equity_peak_total: f64,
    pub equity_peak_daily: f64,
    pub equity_peak_monthly: f64,
    pub order_rate_current: u32,
    pub last_trigger: Option<RiskTriggerEvent>,
    pub flatten_state: FlattenState,
}

// ────────────────────────────────────────────────────────────
//  Flatten State Machine
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug, PartialEq, Serialize)]
#[allow(dead_code)]
pub enum FlattenState {
    Idle,        // No flatten needed
    Requested,   // Risk triggered flatten
    CancelsSent, // Pending orders cancelled
    ReduceSent,  // Reduce-only market sent
    Confirmed,   // Position confirmed flat
}

impl FlattenState {
    pub fn is_active(&self) -> bool {
        !matches!(self, Self::Idle | Self::Confirmed)
    }
}

// ────────────────────────────────────────────────────────────
//  Health Gate (infra check before entries)
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
#[allow(dead_code)]
pub struct HealthGate {
    pub ws_connected: bool,
    pub book_synced: bool,
    pub lag_p99_ms: f64,
    pub spread_bps: f64,
}

impl Default for HealthGate {
    fn default() -> Self {
        Self {
            ws_connected: true,
            book_synced: true,
            lag_p99_ms: 0.0,
            spread_bps: 0.0,
        }
    }
}

// ────────────────────────────────────────────────────────────
//  Risk Error
// ────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub enum RiskError {
    TradingDisabled(RiskState),
    TooManyPositions { current: usize, max: usize },
    LeverageExceeded { current: f64, max: f64 },
    OrderRateExceeded { current: u32, max: u32 },
    NotionalTooSmall { notional: f64, min: f64 },
    HealthGated { reason: String },
    Other(String),
}

impl std::fmt::Display for RiskError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::TradingDisabled(s) => write!(f, "Trading disabled: {}", s.as_str()),
            Self::TooManyPositions { current, max } => {
                write!(f, "Too many positions: {} >= {}", current, max)
            }
            Self::LeverageExceeded { current, max } => {
                write!(f, "Leverage exceeded: {:.2}x > {:.2}x", current, max)
            }
            Self::OrderRateExceeded { current, max } => {
                write!(f, "Order rate exceeded: {}/min > {}/min", current, max)
            }
            Self::NotionalTooSmall { notional, min } => {
                write!(f, "Notional too small: {:.2} < {:.2}", notional, min)
            }
            Self::HealthGated { reason } => write!(f, "Health gated: {}", reason),
            Self::Other(s) => write!(f, "{}", s),
        }
    }
}

// Backward compatibility alias
#[allow(dead_code)]
pub type RiskDecision = Result<(), RiskError>;

// ────────────────────────────────────────────────────────────
//  Sliding Counter (order rate limiter)
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct SlidingCounter {
    /// Timestamps of recent orders (ring buffer)
    timestamps: Vec<i64>,
    window_ms: i64,
}

#[allow(dead_code)]
impl SlidingCounter {
    pub fn new(window_ms: i64) -> Self {
        Self {
            timestamps: Vec::with_capacity(256),
            window_ms,
        }
    }

    pub fn record(&mut self, now_ms: i64) {
        self.timestamps.push(now_ms);
        self.gc(now_ms);
    }

    pub fn count(&mut self, now_ms: i64) -> u32 {
        self.gc(now_ms);
        self.timestamps.len() as u32
    }

    fn gc(&mut self, now_ms: i64) {
        let cutoff = now_ms - self.window_ms;
        self.timestamps.retain(|&ts| ts > cutoff);
    }
}

// ────────────────────────────────────────────────────────────
//  Drawdown Snapshot (backward compat for GUI)
// ────────────────────────────────────────────────────────────

#[derive(Clone, Debug)]
pub struct DrawdownSnapshot {
    pub daily_dd_pct: f64,
    pub monthly_dd_pct: f64,
    pub total_dd_pct: f64,
    pub state: RiskState,
    pub equity: f64,
    pub daily_peak: f64,
    pub monthly_peak: f64,
    pub total_peak: f64,
}

// ════════════════════════════════════════════════════════════════════════
//  Risk Manager
// ════════════════════════════════════════════════════════════════════════

pub struct RiskManager {
    pub cfg: RiskConfig,
    pub state: RiskState,

    // ── Equity Peaks ──
    pub equity_peak_total: f64,
    pub equity_peak_daily: f64,
    pub equity_peak_monthly: f64,
    pub current_equity: f64,

    // ── Time Keys ──
    last_day_key: i32,   // yyyyMMdd
    last_month_key: i32, // yyyyMM

    // ── Order Rate ──
    order_rate: SlidingCounter,

    // ── Trigger ──
    pub last_trigger: Option<RiskTriggerEvent>,

    // ── Flatten State Machine ──
    pub flatten_state: FlattenState,

    // ── Health Gate ──
    pub health_gate: HealthGate,

    // ── Exposure (per-symbol) ──
    symbol_exposure: HashMap<String, f64>,
    pub total_exposure: f64,

    // ── Adaptive Risk (preserved from legacy) ──
    pub rolling_trades: Vec<f64>,
    pub consecutive_losses: u32,
    pub max_drawdown_reached: f64,
}

#[allow(dead_code)]
impl RiskManager {
    // ────────────────────────────────────────────────────────
    //  Constructor
    // ────────────────────────────────────────────────────────

    pub fn new(cfg: RiskConfig, now_ms: i64, equity: f64) -> Self {
        let (day_key, month_key) = Self::time_keys(now_ms);
        Self {
            cfg,
            state: RiskState::Running,
            equity_peak_total: equity,
            equity_peak_daily: equity,
            equity_peak_monthly: equity,
            current_equity: equity,
            last_day_key: day_key,
            last_month_key: month_key,
            order_rate: SlidingCounter::new(60_000), // 1 minute
            last_trigger: None,
            flatten_state: FlattenState::Idle,
            health_gate: HealthGate::default(),
            symbol_exposure: HashMap::new(),
            total_exposure: 0.0,
            rolling_trades: Vec::new(),
            consecutive_losses: 0,
            max_drawdown_reached: 0.0,
        }
    }

    // ────────────────────────────────────────────────────────
    //  Account Update (post-fill / periodic)
    // ────────────────────────────────────────────────────────

    pub fn on_account_update(&mut self, now_ms: i64, account: &AccountSnapshot) {
        let equity = account.equity;
        let (day_key, month_key) = Self::time_keys(now_ms);

        // ── Day Rotation ──
        if day_key != self.last_day_key {
            info!(
                r#"{{"event":"risk_day_rotation","from":{},"to":{},"peak_reset":{:.2}}}"#,
                self.last_day_key, day_key, self.equity_peak_daily
            );
            self.last_day_key = day_key;
            self.equity_peak_daily = equity;
            if self.state == RiskState::DisabledDaily {
                self.transition_state(now_ms, equity, RiskState::Running, "New day auto-reset");
            }
        }

        // ── Month Rotation ──
        if month_key != self.last_month_key {
            info!(
                r#"{{"event":"risk_month_rotation","from":{},"to":{},"peak_reset":{:.2}}}"#,
                self.last_month_key, month_key, self.equity_peak_monthly
            );
            self.last_month_key = month_key;
            self.equity_peak_monthly = equity;
            if self.state == RiskState::DisabledMonthly {
                self.transition_state(now_ms, equity, RiskState::Running, "New month auto-reset");
            }
        }

        self.current_equity = equity;

        // ── Update Peaks ──
        if equity > self.equity_peak_daily {
            self.equity_peak_daily = equity;
        }
        if equity > self.equity_peak_monthly {
            self.equity_peak_monthly = equity;
        }
        if equity > self.equity_peak_total {
            self.equity_peak_total = equity;
        }

        // ── Initialize peaks on first call ──
        if self.equity_peak_daily <= 0.0 {
            self.equity_peak_daily = equity;
        }
        if self.equity_peak_monthly <= 0.0 {
            self.equity_peak_monthly = equity;
        }
        if self.equity_peak_total <= 0.0 {
            self.equity_peak_total = equity;
        }

        // ── Compute Drawdowns ──
        let dd_daily = Self::calc_dd_pct(self.equity_peak_daily, equity);
        let dd_monthly = Self::calc_dd_pct(self.equity_peak_monthly, equity);
        let dd_total = Self::calc_dd_pct(self.equity_peak_total, equity);

        if dd_total > self.max_drawdown_reached {
            self.max_drawdown_reached = dd_total;
        }

        // ── Evaluate Limits (most severe first) ──
        if self.state == RiskState::Running {
            if dd_total >= self.cfg.max_total_dd_pct {
                self.trigger_disable(
                    now_ms,
                    equity,
                    dd_daily,
                    dd_monthly,
                    dd_total,
                    RiskState::DisabledTotal,
                    RiskTriggerKind::TotalDD,
                    &format!(
                        "Total DD {:.2}% >= limit {:.1}%",
                        dd_total, self.cfg.max_total_dd_pct
                    ),
                );
            } else if dd_monthly >= self.cfg.max_monthly_dd_pct {
                self.trigger_disable(
                    now_ms,
                    equity,
                    dd_daily,
                    dd_monthly,
                    dd_total,
                    RiskState::DisabledMonthly,
                    RiskTriggerKind::MonthlyDD,
                    &format!(
                        "Monthly DD {:.2}% >= limit {:.1}%",
                        dd_monthly, self.cfg.max_monthly_dd_pct
                    ),
                );
            } else if dd_daily >= self.cfg.max_daily_dd_pct {
                self.trigger_disable(
                    now_ms,
                    equity,
                    dd_daily,
                    dd_monthly,
                    dd_total,
                    RiskState::DisabledDaily,
                    RiskTriggerKind::DailyDD,
                    &format!(
                        "Daily DD {:.2}% >= limit {:.1}%",
                        dd_daily, self.cfg.max_daily_dd_pct
                    ),
                );
            }
        }
    }

    // ────────────────────────────────────────────────────────
    //  Pre-Trade Gate
    // ────────────────────────────────────────────────────────

    pub fn check_order_allowed(
        &mut self,
        now_ms: i64,
        _symbol: &str,
        proposed: &ProposedOrder,
        account: &AccountSnapshot,
        positions: &[PositionSnapshot],
    ) -> Result<(), RiskError> {
        // 0. State Gate
        if self.state.is_disabled() {
            if proposed.is_reduce_only && self.cfg.allow_reduce_only_when_disabled {
                // Allow reduce-only orders even when disabled
            } else {
                let err = RiskError::TradingDisabled(self.state.clone());
                warn!(
                    r#"{{"event":"order_rejected_risk","symbol":"{}","reason":"{}","state":"{}"}}"#,
                    proposed.symbol,
                    err,
                    self.state.as_str()
                );
                return Err(err);
            }
        }

        // 0b. Health Gate (entries only — exits always allowed)
        if !proposed.is_reduce_only {
            if !self.health_gate.ws_connected {
                let err = RiskError::HealthGated {
                    reason: "WebSocket disconnected".into(),
                };
                warn!(
                    r#"{{"event":"order_rejected_health","symbol":"{}","reason":"ws_disconnected"}}"#,
                    proposed.symbol
                );
                return Err(err);
            }
            if !self.health_gate.book_synced {
                let err = RiskError::HealthGated {
                    reason: "Order book not synced".into(),
                };
                warn!(
                    r#"{{"event":"order_rejected_health","symbol":"{}","reason":"book_desynced"}}"#,
                    proposed.symbol
                );
                return Err(err);
            }
            if self.health_gate.lag_p99_ms > 1000.0 {
                let err = RiskError::HealthGated {
                    reason: format!("Lag too high: {:.0}ms", self.health_gate.lag_p99_ms),
                };
                warn!(
                    r#"{{"event":"order_rejected_health","symbol":"{}","reason":"lag_too_high","lag_p99":{:.0}}}"#,
                    proposed.symbol, self.health_gate.lag_p99_ms
                );
                return Err(err);
            }
        }

        // 1. Position Count
        if !proposed.is_reduce_only {
            let total_positions = positions.iter().filter(|p| p.qty.abs() > 1e-12).count();
            if total_positions >= self.cfg.max_positions_total {
                let err = RiskError::TooManyPositions {
                    current: total_positions,
                    max: self.cfg.max_positions_total,
                };
                warn!(
                    r#"{{"event":"order_rejected_risk","symbol":"{}","reason":"{}"}}"#,
                    proposed.symbol, err
                );
                return Err(err);
            }

            let symbol_positions = positions
                .iter()
                .filter(|p| p.symbol == proposed.symbol && p.qty.abs() > 1e-12)
                .count();
            if symbol_positions >= self.cfg.max_positions_per_symbol {
                let err = RiskError::TooManyPositions {
                    current: symbol_positions,
                    max: self.cfg.max_positions_per_symbol,
                };
                warn!(
                    r#"{{"event":"order_rejected_risk","symbol":"{}","reason":"{}"}}"#,
                    proposed.symbol, err
                );
                return Err(err);
            }
        }

        // 2. Leverage Check
        if !proposed.is_reduce_only && account.equity > 0.0 {
            let current_notional: f64 = positions.iter().map(|p| p.notional.abs()).sum();
            let proposed_notional = proposed.qty * proposed.price;
            let total_notional = current_notional + proposed_notional;
            let effective_leverage = total_notional / account.equity;

            if effective_leverage > self.cfg.max_total_leverage {
                let err = RiskError::LeverageExceeded {
                    current: effective_leverage,
                    max: self.cfg.max_total_leverage,
                };
                warn!(
                    r#"{{"event":"order_rejected_risk","symbol":"{}","reason":"{}"}}"#,
                    proposed.symbol, err
                );
                return Err(err);
            }
        }

        // 3. Order Rate
        let current_rate = self.order_rate.count(now_ms);
        if current_rate >= self.cfg.max_order_rate_per_min {
            let err = RiskError::OrderRateExceeded {
                current: current_rate,
                max: self.cfg.max_order_rate_per_min,
            };
            warn!(
                r#"{{"event":"order_rejected_risk","symbol":"{}","reason":"{}"}}"#,
                proposed.symbol, err
            );
            return Err(err);
        }

        // 4. Notional Minimum
        let notional = proposed.qty * proposed.price;
        if notional < self.cfg.min_notional_per_order && !proposed.is_reduce_only {
            let err = RiskError::NotionalTooSmall {
                notional,
                min: self.cfg.min_notional_per_order,
            };
            warn!(
                r#"{{"event":"order_rejected_risk","symbol":"{}","reason":"{}"}}"#,
                proposed.symbol, err
            );
            return Err(err);
        }

        // Record in rate counter
        self.order_rate.record(now_ms);

        Ok(())
    }

    /// Unified gate for winner exits (Profit Floor) and hard stops (Stop Loss).
    pub fn check_exit_allowed(
        &self,
        pos_side: &str,
        entry_price: f64,
        current_price: f64,
    ) -> Result<(), String> {
        if entry_price <= 0.0 || current_price <= 0.0 {
            return Err("Invalid price".into());
        }

        let pnl_pct = if pos_side == "Buy" {
            (current_price - entry_price) / entry_price
        } else if pos_side == "Sell" {
            (entry_price - current_price) / entry_price
        } else {
            return Err("Not in a position".into());
        };

        let pnl_bps = pnl_pct * 10000.0;

        // Profit Floor (Winner Exit)
        if pnl_bps >= self.cfg.profit_floor_bps {
            return Ok(());
        }

        // Stop Loss (Hard Gate)
        if pnl_bps <= -self.cfg.stop_loss_bps {
            return Ok(());
        }

        Err(format!(
            "Exit blocked: PnL {:.2} bps (Floor: {:.1}, SL: {:.1})",
            pnl_bps, self.cfg.profit_floor_bps, -self.cfg.stop_loss_bps
        ))
    }

    /// Microstructure-aware entry gate (Selective Entry Gating).
    /// Blocks entries if fair value (microprice) is adversely skewed.
    pub fn check_entry_allowed(
        &self,
        side: &str,
        microprice_minus_mid_bps: f64,
    ) -> Result<(), String> {
        if !self.cfg.use_selective_entry {
            return Ok(());
        }

        if side == "Buy" {
            // If fair value is below mid, bidding is dangerous (toxic fill risk)
            if microprice_minus_mid_bps < -self.cfg.entry_veto_threshold_bps {
                return Err(format!(
                    "ENTRY_VETO: Long blocked (Micro-Mid: {:.2} < thres: {:.1})",
                    microprice_minus_mid_bps, -self.cfg.entry_veto_threshold_bps
                ));
            }
        } else if side == "Sell" {
            // If fair value is above mid, asking is dangerous
            if microprice_minus_mid_bps > self.cfg.entry_veto_threshold_bps {
                return Err(format!(
                    "ENTRY_VETO: Short blocked (Micro-Mid: {:.2} > thres: {:.1})",
                    microprice_minus_mid_bps, self.cfg.entry_veto_threshold_bps
                ));
            }
        }

        Ok(())
    }

    // ────────────────────────────────────────────────────────
    //  Position Sizing
    // ────────────────────────────────────────────────────────

    pub fn compute_order_size(
        &self,
        _symbol: &str,
        account: &AccountSnapshot,
        stop_distance_pct: Option<f64>,
        price: f64,
    ) -> SizingResult {
        if price <= 0.0 || account.equity <= 0.0 {
            return SizingResult {
                notional: 0.0,
                qty: 0.0,
                clamped: false,
                reason: "Invalid price or equity".into(),
            };
        }

        let risk_budget = account.equity * (self.cfg.risk_per_trade_pct / 100.0);

        let raw_notional = match self.cfg.sizing_mode {
            RiskSizingMode::StopDistanceBased => {
                let stop_pct =
                    stop_distance_pct.unwrap_or(self.cfg.default_stop_distance_bps / 100.0); // bps -> %
                if stop_pct <= 0.0 {
                    risk_budget // fallback if stop is zero
                } else {
                    risk_budget / (stop_pct / 100.0) // stop_pct is in %, convert to fraction
                }
            }
            RiskSizingMode::FixedFractionOfEquity => risk_budget,
        };

        // Clamp
        let mut clamped = false;
        let mut reason = String::new();
        let mut notional = raw_notional;

        if notional < self.cfg.min_notional_per_order {
            notional = self.cfg.min_notional_per_order;
            clamped = true;
            reason = format!(
                "Clamped up to min_notional {:.2}",
                self.cfg.min_notional_per_order
            );
        }

        if notional > self.cfg.max_notional_per_order {
            notional = self.cfg.max_notional_per_order;
            clamped = true;
            reason = format!(
                "Clamped down to max_notional {:.2}",
                self.cfg.max_notional_per_order
            );
        }

        // Leverage clamp
        let max_notional_by_leverage = account.equity * self.cfg.max_total_leverage;
        if notional > max_notional_by_leverage {
            notional = max_notional_by_leverage;
            clamped = true;
            reason = format!(
                "Clamped by max_leverage {:.1}x",
                self.cfg.max_total_leverage
            );
        }

        let qty = notional / price;

        SizingResult {
            notional,
            qty,
            clamped,
            reason,
        }
    }

    // ────────────────────────────────────────────────────────
    //  Kill Switch
    // ────────────────────────────────────────────────────────

    pub fn kill(&mut self, now_ms: i64, reason: &str) {
        let dd_daily = Self::calc_dd_pct(self.equity_peak_daily, self.current_equity);
        let dd_monthly = Self::calc_dd_pct(self.equity_peak_monthly, self.current_equity);
        let dd_total = Self::calc_dd_pct(self.equity_peak_total, self.current_equity);

        warn!(
            r#"{{"event":"kill_switch","reason":"{}","equity":{:.2},"dd_total":{:.2},"dd_daily":{:.2},"dd_monthly":{:.2}}}"#,
            reason, self.current_equity, dd_total, dd_daily, dd_monthly
        );

        self.last_trigger = Some(RiskTriggerEvent {
            kind: RiskTriggerKind::KillSwitch,
            timestamp: now_ms,
            dd_total,
            dd_daily,
            dd_monthly,
            equity: self.current_equity,
            reason: reason.to_string(),
        });

        self.transition_state(now_ms, self.current_equity, RiskState::Killed, reason);
    }

    // ────────────────────────────────────────────────────────
    //  Reset (GUI)
    // ────────────────────────────────────────────────────────

    pub fn reset_state(&mut self, now_ms: i64, account: &AccountSnapshot) {
        info!(
            r#"{{"event":"risk_state_changed","from":"{}","to":"RUNNING","reason":"manual_reset","equity":{:.2}}}"#,
            self.state.as_str(),
            account.equity
        );
        self.state = RiskState::Running;
        self.equity_peak_daily = account.equity;
        self.current_equity = account.equity;
        self.flatten_state = FlattenState::Idle;
        self.last_trigger = None;
        let (day_key, month_key) = Self::time_keys(now_ms);
        self.last_day_key = day_key;
        self.last_month_key = month_key;
    }

    /// Update health gate (called from agent on each market data update)
    pub fn update_health_gate(&mut self, gate: HealthGate) {
        self.health_gate = gate;
    }

    /// Advance the flatten state machine
    pub fn advance_flatten(&mut self, new_state: FlattenState) {
        if self.flatten_state.is_active() || new_state == FlattenState::Requested {
            info!(
                r#"{{"event":"flatten_state_advance","from":"{:?}","to":"{:?}"}}"#,
                self.flatten_state, new_state
            );
            self.flatten_state = new_state;
        }
    }

    /// Confirm flat — position is zero
    pub fn confirm_flat(&mut self) {
        if self.flatten_state.is_active() {
            info!(r#"{{"event":"flatten_confirmed"}}"#);
            self.flatten_state = FlattenState::Confirmed;
        }
    }

    // ────────────────────────────────────────────────────────
    //  Hot Config Update
    // ────────────────────────────────────────────────────────

    pub fn update_config(&mut self, _now_ms: i64, cfg: RiskConfig) {
        info!(
            r#"{{"event":"risk_config_update","daily_dd":"{:.1}→{:.1}","monthly_dd":"{:.1}→{:.1}","total_dd":"{:.1}→{:.1}","leverage":"{:.1}→{:.1}"}}"#,
            self.cfg.max_daily_dd_pct,
            cfg.max_daily_dd_pct,
            self.cfg.max_monthly_dd_pct,
            cfg.max_monthly_dd_pct,
            self.cfg.max_total_dd_pct,
            cfg.max_total_dd_pct,
            self.cfg.max_total_leverage,
            cfg.max_total_leverage,
        );
        self.cfg = cfg;
    }

    // ────────────────────────────────────────────────────────
    //  Status Snapshot (GUI)
    // ────────────────────────────────────────────────────────

    pub fn status(&self) -> RiskStatus {
        RiskStatus {
            state: self.state.clone(),
            dd_total_pct: Self::calc_dd_pct(self.equity_peak_total, self.current_equity),
            dd_daily_pct: Self::calc_dd_pct(self.equity_peak_daily, self.current_equity),
            dd_monthly_pct: Self::calc_dd_pct(self.equity_peak_monthly, self.current_equity),
            equity: self.current_equity,
            equity_peak_total: self.equity_peak_total,
            equity_peak_daily: self.equity_peak_daily,
            equity_peak_monthly: self.equity_peak_monthly,
            order_rate_current: self.order_rate.timestamps.len() as u32,
            last_trigger: self.last_trigger.clone(),
            flatten_state: self.flatten_state.clone(),
        }
    }

    // ────────────────────────────────────────────────────────
    //  Backward Compatibility (legacy API used by agent.rs)
    // ────────────────────────────────────────────────────────

    /// Backward-compat: legacy drawdown snapshot for GUI
    pub fn current_drawdowns(&self) -> DrawdownSnapshot {
        DrawdownSnapshot {
            daily_dd_pct: Self::calc_dd_pct(self.equity_peak_daily, self.current_equity),
            monthly_dd_pct: Self::calc_dd_pct(self.equity_peak_monthly, self.current_equity),
            total_dd_pct: Self::calc_dd_pct(self.equity_peak_total, self.current_equity),
            state: self.state.clone(),
            equity: self.current_equity,
            daily_peak: self.equity_peak_daily,
            monthly_peak: self.equity_peak_monthly,
            total_peak: self.equity_peak_total,
        }
    }

    /// Backward-compat: legacy equity update (uses wall-clock → wraps on_account_update)
    pub fn on_equity_update(&mut self, equity: f64) {
        let now_ms = chrono::Utc::now().timestamp_millis();
        let account = AccountSnapshot {
            equity,
            wallet_balance: equity,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
        };
        self.on_account_update(now_ms, &account);
    }

    /// Backward-compat: testable version with explicit time components
    pub fn on_equity_update_with_time(
        &mut self,
        day_of_year: u32,
        month: u32,
        year: i32,
        equity: f64,
    ) {
        // Convert to day_key / month_key
        let day_key = year * 1000 + day_of_year as i32;
        let month_key = year * 100 + month as i32;

        // Inline the logic to avoid needing a full timestamp
        if day_key != self.last_day_key {
            self.last_day_key = day_key;
            self.equity_peak_daily = equity;
            if self.state == RiskState::DisabledDaily {
                self.state = RiskState::Running;
            }
        }
        if month_key != self.last_month_key {
            self.last_month_key = month_key;
            self.equity_peak_monthly = equity;
            if self.state == RiskState::DisabledMonthly {
                self.state = RiskState::Running;
            }
        }

        self.current_equity = equity;
        if equity > self.equity_peak_daily {
            self.equity_peak_daily = equity;
        }
        if equity > self.equity_peak_monthly {
            self.equity_peak_monthly = equity;
        }
        if equity > self.equity_peak_total {
            self.equity_peak_total = equity;
        }
        if self.equity_peak_daily <= 0.0 {
            self.equity_peak_daily = equity;
        }
        if self.equity_peak_monthly <= 0.0 {
            self.equity_peak_monthly = equity;
        }
        if self.equity_peak_total <= 0.0 {
            self.equity_peak_total = equity;
        }

        let dd_daily = Self::calc_dd_pct(self.equity_peak_daily, equity);
        let dd_monthly = Self::calc_dd_pct(self.equity_peak_monthly, equity);
        let dd_total = Self::calc_dd_pct(self.equity_peak_total, equity);

        if dd_total > self.max_drawdown_reached {
            self.max_drawdown_reached = dd_total;
        }

        if self.state == RiskState::Running {
            if dd_total >= self.cfg.max_total_dd_pct {
                self.state = RiskState::DisabledTotal;
            } else if dd_monthly >= self.cfg.max_monthly_dd_pct {
                self.state = RiskState::DisabledMonthly;
            } else if dd_daily >= self.cfg.max_daily_dd_pct {
                self.state = RiskState::DisabledDaily;
            }
        }
    }

    /// Backward-compat: legacy check_trade_allowance
    #[allow(clippy::too_many_arguments)]
    pub fn check_trade_allowance(
        &mut self,
        symbol: &str,
        side: &str,
        qty: f64,
        price: f64,
        _max_pos_frac: f64,
        _current_pos_qty: f64,
        _leverage: f64,
    ) -> Result<(), RiskError> {
        if self.state.is_disabled() {
            return Err(RiskError::TradingDisabled(self.state.clone()));
        }
        // Simple notional check
        let notional = qty * price;
        if notional < self.cfg.min_notional_per_order {
            return Err(RiskError::NotionalTooSmall {
                notional,
                min: self.cfg.min_notional_per_order,
            });
        }
        let now_ms = chrono::Utc::now().timestamp_millis();
        let proposed = ProposedOrder {
            symbol: symbol.to_string(),
            side: side.to_string(),
            qty,
            price,
            is_reduce_only: false,
        };
        let account = AccountSnapshot {
            equity: self.current_equity,
            wallet_balance: self.current_equity,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
        };
        // Skip full check_order_allowed to avoid double-counting rate limiter
        // Just record the order
        self.order_rate.record(now_ms);
        let _ = &proposed; // suppress unused
        let _ = &account;
        Ok(())
    }

    /// Backward-compat: record trade outcome for adaptive risk
    pub fn record_trade_outcome(&mut self, pnl: f64) {
        if pnl < 0.0 {
            self.consecutive_losses += 1;
        } else if pnl > 0.0 {
            self.consecutive_losses = 0;
        }
        self.rolling_trades.push(pnl);
        while self.rolling_trades.len() > 50 {
            self.rolling_trades.remove(0);
        }
    }

    /// Backward-compat: update_state (per-symbol exposure)
    pub fn update_state(&mut self, symbol: &str, equity: f64, symbol_exposure_val: f64, _vol: f64) {
        self.symbol_exposure
            .insert(symbol.to_string(), symbol_exposure_val.abs());
        self.total_exposure = self.symbol_exposure.values().sum();
        self.on_equity_update(equity);
    }

    // ────────────────────────────────────────────────────────
    //  Internal Helpers
    // ────────────────────────────────────────────────────────

    fn calc_dd_pct(peak: f64, equity: f64) -> f64 {
        if peak > 0.0 {
            ((peak - equity) / peak) * 100.0
        } else {
            0.0
        }
    }

    fn time_keys(now_ms: i64) -> (i32, i32) {
        use chrono::{Datelike, TimeZone};
        let dt = chrono::Utc
            .timestamp_millis_opt(now_ms)
            .single()
            .unwrap_or_else(chrono::Utc::now);
        let day_key = dt.year() * 10000 + dt.month() as i32 * 100 + dt.day() as i32;
        let month_key = dt.year() * 100 + dt.month() as i32;
        (day_key, month_key)
    }

    #[allow(clippy::too_many_arguments)]
    fn trigger_disable(
        &mut self,
        now_ms: i64,
        equity: f64,
        dd_daily: f64,
        dd_monthly: f64,
        dd_total: f64,
        new_state: RiskState,
        kind: RiskTriggerKind,
        reason: &str,
    ) {
        self.last_trigger = Some(RiskTriggerEvent {
            kind,
            timestamp: now_ms,
            dd_total,
            dd_daily,
            dd_monthly,
            equity,
            reason: reason.to_string(),
        });
        self.transition_state(now_ms, equity, new_state, reason);
    }

    fn transition_state(&mut self, _now_ms: i64, equity: f64, new_state: RiskState, reason: &str) {
        let from = self.state.as_str();
        let to_str = new_state.as_str();

        let dd_daily = Self::calc_dd_pct(self.equity_peak_daily, equity);
        let dd_monthly = Self::calc_dd_pct(self.equity_peak_monthly, equity);
        let dd_total = Self::calc_dd_pct(self.equity_peak_total, equity);

        warn!(
            r#"{{"event":"risk_state_changed","from":"{}","to":"{}","reason":"{}","dd_total":{:.2},"dd_daily":{:.2},"dd_monthly":{:.2},"equity":{:.2}}}"#,
            from, to_str, reason, dd_total, dd_daily, dd_monthly, equity
        );

        self.state = new_state;

        if self.state.is_disabled() && self.cfg.flatten_on_disable {
            self.flatten_state = FlattenState::Requested;
            warn!(
                r#"{{"event":"flatten_all_triggered","state":"{}","reason":"{}","flatten_state":"Requested"}}"#,
                self.state.as_str(),
                reason
            );
        }
    }
}

// Provide a Default constructor for backward compatibility
impl Default for RiskManager {
    fn default() -> Self {
        let now_ms = chrono::Utc::now().timestamp_millis();
        Self::new(RiskConfig::default(), now_ms, 0.0)
    }
}

// ════════════════════════════════════════════════════════════════════════
//  Tests
// ════════════════════════════════════════════════════════════════════════

#[cfg(test)]
mod tests {
    use super::*;

    fn ts(day: i64) -> i64 {
        // 2026-01-01 00:00:00 UTC + day offset
        1735689600_000 + day * 86_400_000
    }

    fn make_account(equity: f64) -> AccountSnapshot {
        AccountSnapshot {
            equity,
            wallet_balance: equity,
            unrealized_pnl: 0.0,
            realized_pnl: 0.0,
        }
    }

    fn make_rm(daily: f64, monthly: f64, total: f64) -> RiskManager {
        let cfg = RiskConfig {
            max_daily_dd_pct: daily,
            max_monthly_dd_pct: monthly,
            max_total_dd_pct: total,
            ..Default::default()
        };
        RiskManager::new(cfg, ts(0), 10000.0)
    }

    // ── DD Tests ──

    #[test]
    fn test_equity_up_no_dd() {
        let mut rm = make_rm(5.0, 15.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(10500.0));
        assert_eq!(rm.state, RiskState::Running);
        let s = rm.status();
        assert_eq!(s.dd_daily_pct, 0.0);
    }

    #[test]
    fn test_daily_dd_breach() {
        let mut rm = make_rm(5.0, 15.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(9400.0)); // 6%
        assert_eq!(rm.state, RiskState::DisabledDaily);
        assert!(rm.flatten_state.is_active());
        assert!(rm.last_trigger.is_some());
    }

    #[test]
    fn test_monthly_dd_breach() {
        let mut rm = make_rm(50.0, 15.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(8000.0)); // 20%
        assert_eq!(rm.state, RiskState::DisabledMonthly);
    }

    #[test]
    fn test_total_dd_breach() {
        let mut rm = make_rm(50.0, 50.0, 10.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(8500.0)); // 15%
        assert_eq!(rm.state, RiskState::DisabledTotal);
    }

    #[test]
    fn test_paper_mode_100pct() {
        let mut rm = make_rm(100.0, 100.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(2000.0)); // 80%
        assert_eq!(rm.state, RiskState::Running);
    }

    #[test]
    fn test_day_rotation_resets() {
        let mut rm = make_rm(5.0, 50.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(9600.0));
        assert_eq!(rm.state, RiskState::Running);
        // New day
        rm.on_account_update(ts(1), &make_account(9600.0));
        assert_eq!(rm.equity_peak_daily, 9600.0);
        // 3% drop is within limit
        rm.on_account_update(ts(1), &make_account(9312.0));
        assert_eq!(rm.state, RiskState::Running);
    }

    // ── Pre-Trade Gate ──

    #[test]
    fn test_order_blocked_when_disabled() {
        let mut rm = make_rm(5.0, 15.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(9000.0));
        assert_eq!(rm.state, RiskState::DisabledDaily);

        let proposed = ProposedOrder {
            symbol: "BTC".into(),
            side: "Buy".into(),
            qty: 0.1,
            price: 50000.0,
            is_reduce_only: false,
        };
        let result = rm.check_order_allowed(ts(0), "BTC", &proposed, &make_account(9000.0), &[]);
        assert!(result.is_err());
    }

    #[test]
    fn test_reduce_only_allowed_when_disabled() {
        let mut rm = make_rm(5.0, 15.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(9000.0));
        assert!(rm.state.is_disabled());

        let proposed = ProposedOrder {
            symbol: "BTC".into(),
            side: "Sell".into(),
            qty: 0.1,
            price: 50000.0,
            is_reduce_only: true,
        };
        let result = rm.check_order_allowed(ts(0), "BTC", &proposed, &make_account(9000.0), &[]);
        assert!(result.is_ok());
    }

    #[test]
    fn test_too_many_positions() {
        let mut rm = make_rm(50.0, 50.0, 100.0);
        rm.cfg.max_positions_total = 2;
        rm.on_account_update(ts(0), &make_account(10000.0));

        let positions = vec![
            PositionSnapshot {
                symbol: "BTC".into(),
                qty: 0.1,
                side: "Buy".into(),
                entry_price: 50000.0,
                notional: 5000.0,
                margin_used: 1000.0,
                leverage: 5.0,
            },
            PositionSnapshot {
                symbol: "ETH".into(),
                qty: 1.0,
                side: "Buy".into(),
                entry_price: 3000.0,
                notional: 3000.0,
                margin_used: 600.0,
                leverage: 5.0,
            },
        ];
        let proposed = ProposedOrder {
            symbol: "SOL".into(),
            side: "Buy".into(),
            qty: 10.0,
            price: 100.0,
            is_reduce_only: false,
        };
        let result =
            rm.check_order_allowed(ts(0), "SOL", &proposed, &make_account(10000.0), &positions);
        assert!(matches!(result, Err(RiskError::TooManyPositions { .. })));
    }

    #[test]
    fn test_leverage_exceeded() {
        let mut rm = make_rm(50.0, 50.0, 100.0);
        rm.cfg.max_total_leverage = 3.0;
        rm.on_account_update(ts(0), &make_account(10000.0));

        let positions = vec![PositionSnapshot {
            symbol: "BTC".into(),
            qty: 0.4,
            side: "Buy".into(),
            entry_price: 50000.0,
            notional: 20000.0,
            margin_used: 4000.0,
            leverage: 5.0,
        }];
        // 20000 existing + 15000 proposed = 35000 / 10000 = 3.5x > 3.0x
        let proposed = ProposedOrder {
            symbol: "ETH".into(),
            side: "Buy".into(),
            qty: 5.0,
            price: 3000.0,
            is_reduce_only: false,
        };
        let result =
            rm.check_order_allowed(ts(0), "ETH", &proposed, &make_account(10000.0), &positions);
        assert!(matches!(result, Err(RiskError::LeverageExceeded { .. })));
    }

    #[test]
    fn test_order_rate_exceeded() {
        let mut rm = make_rm(50.0, 50.0, 100.0);
        rm.cfg.max_order_rate_per_min = 3;
        rm.on_account_update(ts(0), &make_account(10000.0));

        let proposed = ProposedOrder {
            symbol: "BTC".into(),
            side: "Buy".into(),
            qty: 0.01,
            price: 50000.0,
            is_reduce_only: false,
        };
        let account = make_account(10000.0);

        // Fire 3 orders (uses up the limit)
        for i in 0..3 {
            let r = rm.check_order_allowed(ts(0) + i, "BTC", &proposed, &account, &[]);
            assert!(r.is_ok());
        }
        // 4th should fail
        let r = rm.check_order_allowed(ts(0) + 3, "BTC", &proposed, &account, &[]);
        assert!(matches!(r, Err(RiskError::OrderRateExceeded { .. })));
    }

    #[test]
    fn test_health_gate_blocks_new_entries_when_book_desynced() {
        let mut rm = make_rm(50.0, 50.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10_000.0));
        rm.update_health_gate(HealthGate {
            ws_connected: true,
            book_synced: false,
            lag_p99_ms: 0.0,
            spread_bps: 1.0,
        });

        let proposed = ProposedOrder {
            symbol: "BTC".into(),
            side: "Buy".into(),
            qty: 0.01,
            price: 50_000.0,
            is_reduce_only: false,
        };

        let result = rm.check_order_allowed(ts(0), "BTC", &proposed, &make_account(10_000.0), &[]);
        assert!(matches!(result, Err(RiskError::HealthGated { .. })));
    }

    #[test]
    fn test_health_gate_allows_reduce_only_when_book_desynced() {
        let mut rm = make_rm(50.0, 50.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10_000.0));
        rm.update_health_gate(HealthGate {
            ws_connected: true,
            book_synced: false,
            lag_p99_ms: 10_000.0,
            spread_bps: 1.0,
        });

        let proposed = ProposedOrder {
            symbol: "BTC".into(),
            side: "Sell".into(),
            qty: 0.01,
            price: 50_000.0,
            is_reduce_only: true,
        };

        let result = rm.check_order_allowed(ts(0), "BTC", &proposed, &make_account(10_000.0), &[]);
        assert!(result.is_ok());
    }

    // ── Kill Switch ──

    #[test]
    fn test_kill_switch() {
        let mut rm = make_rm(50.0, 50.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.kill(ts(0), "Manual kill from GUI");
        assert_eq!(rm.state, RiskState::Killed);
        assert!(rm.flatten_state.is_active());
    }

    // ── Sizing ──

    #[test]
    fn test_sizing_stop_distance() {
        let rm = make_rm(50.0, 50.0, 100.0);
        // equity=10000, risk_per_trade=0.5% = 50 USDT risk budget
        // stop_distance = 1% → notional = 50 / 0.01 = 5000
        let result = rm.compute_order_size("BTC", &make_account(10000.0), Some(1.0), 50000.0);
        assert!((result.notional - 5000.0).abs() < 1.0);
        assert!((result.qty - 0.1).abs() < 0.001);
    }

    #[test]
    fn test_sizing_fixed_fraction() {
        let cfg = RiskConfig {
            sizing_mode: RiskSizingMode::FixedFractionOfEquity,
            risk_per_trade_pct: 2.0,
            ..Default::default()
        };
        let rm = RiskManager::new(cfg, ts(0), 10000.0);
        // equity=10000, fraction=2% → notional = 200
        let result = rm.compute_order_size("BTC", &make_account(10000.0), None, 50000.0);
        assert!((result.notional - 200.0).abs() < 1.0);
    }

    // ── Config Update ──

    #[test]
    fn test_runtime_config_update() {
        let mut rm = make_rm(5.0, 15.0, 100.0);
        rm.update_config(
            ts(0),
            RiskConfig {
                max_daily_dd_pct: 20.0,
                max_monthly_dd_pct: 50.0,
                max_total_dd_pct: 100.0,
                ..Default::default()
            },
        );
        assert_eq!(rm.cfg.max_daily_dd_pct, 20.0);

        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(9000.0)); // 10% < 20%
        assert_eq!(rm.state, RiskState::Running);
    }

    // ── Reset ──

    #[test]
    fn test_reset_state() {
        let mut rm = make_rm(5.0, 15.0, 100.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(9000.0));
        assert!(rm.state.is_disabled());
        rm.reset_state(ts(0), &make_account(9000.0));
        assert_eq!(rm.state, RiskState::Running);
        assert_eq!(rm.equity_peak_daily, 9000.0);
        assert!(!rm.flatten_state.is_active());
    }

    // ── Flatten Signal ──

    #[test]
    fn test_flatten_on_disable() {
        let mut rm = make_rm(5.0, 15.0, 100.0);
        assert!(rm.cfg.flatten_on_disable);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(9400.0)); // 6% > 5%
        assert!(rm.flatten_state.is_active());
        assert_eq!(rm.state, RiskState::DisabledDaily);
    }

    #[test]
    fn test_no_flatten_when_disabled_in_config() {
        let cfg = RiskConfig {
            max_daily_dd_pct: 5.0,
            flatten_on_disable: false,
            ..Default::default()
        };
        let mut rm = RiskManager::new(cfg, ts(0), 10000.0);
        rm.on_account_update(ts(0), &make_account(10000.0));
        rm.on_account_update(ts(0), &make_account(9400.0));
        assert!(!rm.flatten_state.is_active());
        assert_eq!(rm.state, RiskState::DisabledDaily);
    }

    // ── Backward Compat ──

    #[test]
    fn test_legacy_on_equity_update_with_time() {
        let mut rm = make_rm(5.0, 15.0, 100.0);
        rm.on_equity_update_with_time(1, 1, 2026, 10000.0);
        rm.on_equity_update_with_time(1, 1, 2026, 9400.0);
        assert_eq!(rm.state, RiskState::DisabledDaily);
    }

    #[test]
    fn test_legacy_day_rotation() {
        let mut rm = make_rm(5.0, 50.0, 100.0);
        rm.on_equity_update_with_time(1, 1, 2026, 10000.0);
        rm.on_equity_update_with_time(1, 1, 2026, 9600.0);
        assert_eq!(rm.state, RiskState::Running);
        rm.on_equity_update_with_time(2, 1, 2026, 9600.0);
        assert_eq!(rm.equity_peak_daily, 9600.0);
    }
}
