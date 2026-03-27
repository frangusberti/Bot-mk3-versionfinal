use crate::features_v2::schema::FeatureRow;
use serde::{Serialize, Deserialize};

// ============================================================================
//  Order Intent & Urgency
// ============================================================================

/// Why this order is being placed — affects fee strategy and priority.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderIntent {
    Entry,
    Exit,
    StopLoss,
    TakeProfit,
    RiskFlatten,
}

/// How urgently the order should be filled.
/// High = use MARKET/taker; Normal = prefer LIMIT/maker.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Urgency {
    Normal,
    High,
}

// ============================================================================
//  Strategy Action — Rich output from strategies
// ============================================================================

/// The action returned by a strategy's `on_observation` call.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum StrategyAction {
    /// No-op: do nothing this tick.
    Flat { reason: String },

    /// Open a long position.
    EnterLong {
        /// Fraction of available equity to use (0.0–1.0).
        qty_frac: f64,
        intent: OrderIntent,
        urgency: Urgency,
        reason: String,
    },

    /// Open a short position.
    EnterShort {
        qty_frac: f64,
        intent: OrderIntent,
        urgency: Urgency,
        reason: String,
    },

    /// Exit (partially or fully) an existing position.
    Exit {
        /// Fraction of current position to close (0.0–1.0).
        qty_frac: f64,
        intent: OrderIntent,
        urgency: Urgency,
        reason: String,
    },
}

impl StrategyAction {
    pub fn reason(&self) -> &str {
        match self {
            Self::Flat { reason } => reason,
            Self::EnterLong { reason, .. } => reason,
            Self::EnterShort { reason, .. } => reason,
            Self::Exit { reason, .. } => reason,
        }
    }

    pub fn is_flat(&self) -> bool { matches!(self, Self::Flat { .. }) }
}

// ============================================================================
//  Observation — What the strategy sees each tick
// ============================================================================

/// Snapshot of the account at decision time.
#[derive(Debug, Clone, Default)]
pub struct AccountSnapshot {
    pub equity: f64,
    pub cash: f64,
    pub margin_used: f64,
    pub available_margin: f64,
    pub drawdown_pct: f64,
}

/// Snapshot of the current position (if any).
#[derive(Debug, Clone, Default)]
pub struct PositionSnapshot {
    pub qty: f64,          // >0 long, <0 short, 0 flat
    pub entry_price: f64,
    pub unrealized_pnl: f64,
    pub latent_pnl_pct: f64,
    pub max_pnl_pct: f64,
    pub holding_ms: i64,
}

/// Full observation passed to strategy each tick.
#[derive(Debug, Clone)]
pub struct Observation {
    pub ts: i64,
    pub symbol: String,
    pub features: FeatureRow,
    pub account: AccountSnapshot,
    pub position: PositionSnapshot,
}

impl Observation {
    pub fn is_long(&self) -> bool { self.position.qty > 1e-9 }
    pub fn is_short(&self) -> bool { self.position.qty < -1e-9 }
    pub fn is_flat(&self) -> bool { self.position.qty.abs() <= 1e-9 }
    pub fn mid_price(&self) -> f64 { self.features.mid_price.unwrap_or(0.0) }
}

// ============================================================================
//  Strategy Trait
// ============================================================================

/// Context passed to strategy (symbol, config, etc.).
pub struct StrategyContext {
    pub symbol: String,
}

/// The core strategy trait. Implementations must be deterministic.
pub trait Strategy: Send + Sync {
    fn name(&self) -> &str;

    /// Called every emission tick with the latest observation.
    /// Returns a StrategyAction describing what to do.
    fn on_observation(&mut self, obs: &Observation, ctx: &mut StrategyContext) -> StrategyAction;

    /// Reset internal state (between episodes/backtests).
    fn reset(&mut self);
}

// ============================================================================
//  Sub-modules
// ============================================================================

pub mod microstructure_momentum;
pub mod mean_reversion_v2;
pub mod rule_policy_baseline;

pub use microstructure_momentum::MicrostructureMomentumStrategy;
pub use mean_reversion_v2::MeanReversionV2Strategy;
pub use rule_policy_baseline::{RulePolicyBaseline, RulePolicyConfig};
