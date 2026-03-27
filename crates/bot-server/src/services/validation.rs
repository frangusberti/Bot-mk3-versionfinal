use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ValidationWindowSpec {
    pub window_id: usize,
    pub start_ts: i64,
    pub end_ts: i64,
    pub regime_distribution: HashMap<String, f64>,
    pub reported_sharpe: f64,
    pub reported_net_pnl: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AblationRunResult {
    pub disabled_module: String,
    pub window_results: Vec<ValidationWindowSpec>,
    pub total_net_pnl: f64,
    pub combined_sharpe: f64,
    pub max_drawdown: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum AblationKind { 
    Baseline, 
    NoRegime, 
    NoExecQuality, 
    NoCostModel, 
    NoGateCooldowns,
    FullSystem 
}

impl Default for AblationKind {
    fn default() -> Self {
        Self::FullSystem
    }
}

pub struct WalkForwardEngine {
    pub dataset_id: String,
    pub step_days: usize,
    pub window_days: usize,
    pub bounds_ts: (i64, i64),
}

impl WalkForwardEngine {
    pub fn new(dataset_id: String, bounds_ts: (i64, i64), step_days: usize, window_days: usize) -> Self {
        Self { dataset_id, bounds_ts, step_days, window_days }
    }
    
    pub fn generate_windows(&self) -> Vec<(i64, i64)> {
        let mut windows = Vec::new();
        let step_ms = self.step_days as i64 * 86_400_000;
        let window_ms = self.window_days as i64 * 86_400_000;
        
        let mut start = self.bounds_ts.0;
        while start + window_ms <= self.bounds_ts.1 {
            let end = start + window_ms;
            windows.push((start, end));
            start += step_ms;
        }
        
        // If the dataset is too small, guarantee at least 1 window
        if windows.is_empty() {
            windows.push((self.bounds_ts.0, self.bounds_ts.1));
        }
        
        windows
    }
    
    pub async fn run_ablation_suite(&self, ablations: Vec<AblationKind>) -> Vec<AblationRunResult> {
        let windows = self.generate_windows();
        let mut results = Vec::new();
        
        for ablation in ablations {
            let mut window_specs = Vec::new();
            let mut combined_pnl = 0.0;
            
            for (i, (start_ts, end_ts)) in windows.iter().enumerate() {
                // Here we would run the exact episode runner restricted to [start_ts, end_ts]
                // and passing down the AblationKind configuration. For now we record the intention.
                let mock_pnl = -1.0; // Simulated loss/gain placeholder
                combined_pnl += mock_pnl;
                
                window_specs.push(ValidationWindowSpec {
                    window_id: i,
                    start_ts: *start_ts,
                    end_ts: *end_ts,
                    regime_distribution: HashMap::new(),
                    reported_sharpe: 0.0,
                    reported_net_pnl: mock_pnl,
                });
            }
            
            results.push(AblationRunResult {
                disabled_module: format!("{:?}", ablation),
                window_results: window_specs,
                total_net_pnl: combined_pnl,
                combined_sharpe: 0.0,
                max_drawdown: 0.0,
            });
        }
        
        results
    }
}
