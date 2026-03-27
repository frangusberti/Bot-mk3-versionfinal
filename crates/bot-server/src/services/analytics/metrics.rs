use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EquityPoint {
    pub timestamp: i64,
    pub equity: f64,
    pub cash: f64,
    pub unrealized_pnl: f64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SessionMetrics {
    // Core
    pub total_trades: u64,
    pub total_fees: f64,
    pub start_equity: f64,
    pub end_equity: f64,
    
    // Performance
    pub total_return_pct: f64,
    pub max_drawdown_pct: f64,
    pub profit_factor: f64,
    pub win_rate: f64,
    pub sharpe_ratio: f64,
    pub sortino_ratio: f64,
    
    // Internal tracking
    #[serde(skip)]
    pub peak_equity: f64,
}

impl SessionMetrics {
    pub fn update_equity(&mut self, equity: f64) {
        if self.start_equity == 0.0 {
            self.start_equity = equity;
            self.peak_equity = equity;
        }
        self.end_equity = equity;
    }

    pub fn calculate_final(&mut self, curve: &[EquityPoint]) {
        if curve.is_empty() { return; }
        
        // Return
        if self.start_equity > 0.0 {
            self.total_return_pct = (self.end_equity - self.start_equity) / self.start_equity * 100.0;
        }
        
        // Max DD
        let mut peak = self.start_equity;
        let mut max_dd = 0.0;
        
        for p in curve {
            if p.equity > peak { peak = p.equity; }
            let dd = (peak - p.equity) / peak;
            if dd > max_dd { max_dd = dd; }
        }
        self.max_drawdown_pct = max_dd * 100.0;
        
        // Sharpe (simplified daily)
        // Need returns per period. For now, use a simple proxy or skip if not enough data.
        self.sharpe_ratio = self.calculate_sharpe(curve);
        self.sortino_ratio = self.calculate_sortino(curve);
    }
    
    fn calculate_sharpe(&self, curve: &[EquityPoint]) -> f64 {
        if curve.len() < 2 { return 0.0; }
        
        let returns = self.get_returns(curve);
        if returns.is_empty() { return 0.0; }
        
        let n = returns.len() as f64;
        let mean = returns.iter().sum::<f64>() / n;
        let variance = returns.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / n;
        let std_dev = variance.sqrt();
        
        if std_dev == 0.0 { return 0.0; }
        // Simple raw sharpe
        mean / std_dev
    }

    pub fn calculate_sortino(&self, curve: &[EquityPoint]) -> f64 {
        let returns = self.get_returns(curve);
        if returns.is_empty() { return 0.0; }

        let n = returns.len() as f64;
        let mean = returns.iter().sum::<f64>() / n;
        
        // Downside deviation: sum(min(0, r - target)^2)
        // Target is usually 0 (risk free) or MAR. We use 0.
        let downside_sq_sum: f64 = returns.iter()
            .map(|r| if *r < 0.0 { r.powi(2) } else { 0.0 })
            .sum();
            
        let downside_dev = (downside_sq_sum / n).sqrt();
        
        if downside_dev == 0.0 { return 0.0; }
        mean / downside_dev
    }

    fn get_returns(&self, curve: &[EquityPoint]) -> Vec<f64> {
        let mut returns = Vec::with_capacity(curve.len());
        for i in 1..curve.len() {
            let prev = curve[i-1].equity;
            let curr = curve[i].equity;
            if prev > 0.0 {
                returns.push((curr - prev) / prev);
            }
        }
        returns
    }
}
