use super::{Strategy, StrategyContext, Observation};
use bot_core::proto::{Action, ActionType};
use std::collections::VecDeque;

pub struct RangeBreakoutStrategy {
    pub window: usize,
    pub threshold_bps: f64,
    pub qty_fraction: f64,
    history: VecDeque<f64>,
}

impl RangeBreakoutStrategy {
    pub fn new(window: usize, threshold_bps: f64, qty_fraction: f64) -> Self {
        Self {
            window,
            threshold_bps,
            qty_fraction,
            history: VecDeque::with_capacity(window),
        }
    }
}

impl Strategy for RangeBreakoutStrategy {
    fn act(&mut self, obs: &Observation, _ctx: &mut StrategyContext) -> Action {
        let price = obs.mid_price;
        
        // Maintain history
        if self.history.len() >= self.window {
            self.history.pop_front();
        }
        self.history.push_back(price);
        
        if self.history.len() < self.window {
             return Action { r#type: ActionType::Hold as i32, ..Default::default() };
        }
        
        // Calculate Range (High/Low of PREVIOUS N bars, excluding current?)
        // Usually breakout is against PAST range.
        // Let's us history excluding current?
        // history has current pushed.
        
        let mut high = f64::MIN;
        let mut low = f64::MAX;
        
        // Check window-1
        for i in 0..self.history.len()-1 {
             let p = self.history[i];
             if p > high { high = p; }
             if p < low { low = p; }
        }
        
        // Threshold
        let upper = high * (1.0 + self.threshold_bps / 10000.0);
        let lower = low * (1.0 - self.threshold_bps / 10000.0);
        
        let mut action_type = ActionType::Hold;
        
        if price > upper {
            action_type = ActionType::OpenLong;
        } else if price < lower {
            action_type = ActionType::OpenShort;
        }
        
        // Close logic?
        // If we are Long and price < lower? Stop loss?
        // Or pure reversal?
        // "Breakout" usually implies trend following.
        // If Long and price drops below lower (breakout down), flip short.
        
        // Simple implementation: Always try to be in direction of breakout.
        
        Action {
            r#type: action_type as i32,
            // scaling_factor removed as it is not in proto
            ..Default::default()
        }
    }
    
    fn name(&self) -> &str { "RangeBreakout" }
    
    fn reset(&mut self) {
        self.history.clear();
    }
}
