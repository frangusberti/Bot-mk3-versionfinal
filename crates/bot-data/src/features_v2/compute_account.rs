/// Group G: Account state features injected externally by the agent.
/// These are NOT computed internally by FeatureEngineV2 — they are set
/// by the agent/orchestrator which has access to position and equity data.

#[derive(Debug, Clone, Default)]
pub struct AccountState {
    /// -1.0 (short), 0.0 (flat), 1.0 (long)
    pub position_flag: f64,
    /// Unrealized PnL as % of equity
    pub latent_pnl_pct: f64,
    /// Max PnL reached in current position as % of equity
    pub max_pnl_pct: f64,
    /// Current equity drawdown from peak as %
    pub current_drawdown_pct: f64,
}

#[derive(Debug, Clone)]
pub struct AccountFeatures {
    pub position_flag: f64,
    pub latent_pnl_pct: f64,
    pub max_pnl_pct: f64,
    pub current_drawdown_pct: f64,
}

impl AccountState {
    pub fn to_features(&self) -> AccountFeatures {
        AccountFeatures {
            position_flag: self.position_flag,
            latent_pnl_pct: self.latent_pnl_pct,
            max_pnl_pct: self.max_pnl_pct,
            current_drawdown_pct: self.current_drawdown_pct,
        }
    }
}
