use super::{Strategy, StrategyContext, Observation};
use bot_core::proto::{Action, ActionType};
use std::collections::VecDeque;

pub struct MeanReversionStrategy {
    pub window: usize,
    pub std_dev_mult: f64,
    pub qty_fraction: f64,
    history: VecDeque<f64>,
}

impl MeanReversionStrategy {
    pub fn new(window: usize, std_dev_mult: f64, qty_fraction: f64) -> Self {
        Self {
            window,
            std_dev_mult,
            qty_fraction,
            history: VecDeque::with_capacity(window),
        }
    }
}

impl Strategy for MeanReversionStrategy {
    fn act(&mut self, obs: &Observation, _ctx: &mut StrategyContext) -> Action {
        let price = obs.mid_price;
        
        if self.history.len() >= self.window {
            self.history.pop_front();
        }
        self.history.push_back(price);
        
        if self.history.len() < self.window {
             return Action { r#type: ActionType::Hold as i32, ..Default::default() };
        }
        
        // Calculate MA and StdDev
        let sum: f64 = self.history.iter().sum();
        let mean = sum / self.history.len() as f64;
        
        let variance: f64 = self.history.iter().map(|p| (p - mean).powi(2)).sum();
        let std_dev = (variance / self.history.len() as f64).sqrt();
        
        let upper = mean + self.std_dev_mult * std_dev;
        let lower = mean - self.std_dev_mult * std_dev;
        
        let mut action_type = ActionType::Hold;
        
        // Reversion:
        // Price > Upper -> Overbought -> Short
        // Price < Lower -> Oversold -> Long
        
        if price > upper {
            action_type = ActionType::OpenShort;
        } else if price < lower {
            action_type = ActionType::OpenLong;
        } else {
            // Close if mean reverted? Or hold?
            // "Mean Reversion": Exit at Mean.
            if obs.is_long() && price >= mean {
                action_type = ActionType::CloseAll;
            } else if obs.is_short() && price <= mean {
                action_type = ActionType::CloseAll;
            }
        }
        
        Action {
            r#type: action_type as i32,
            // scaling_factor removed as it is not in proto
            ..Default::default()
        }
    }
     fn name(&self) -> &str { "MeanReversion" }
     
     fn reset(&mut self) {
         self.history.clear();
     }
}
