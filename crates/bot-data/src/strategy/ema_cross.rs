use super::{Strategy, Observation, StrategyContext};
use bot_core::proto::{Action, ActionType};

pub struct EmaCrossStrategy {
    fast_period: usize,
    slow_period: usize,
    
    // State
    fast_ema: f64,
    slow_ema: f64,
    initialized: bool,
    last_cross_up: bool,
    cooldown_steps: usize,
    steps_since_action: usize,
}

impl EmaCrossStrategy {
    pub fn new(fast_period: usize, slow_period: usize, cooldown: usize) -> Self {
        Self {
            fast_period,
            slow_period,
            fast_ema: 0.0,
            slow_ema: 0.0,
            initialized: false,
            last_cross_up: false,
            cooldown_steps: cooldown,
            steps_since_action: 0,
        }
    }
    
    fn update_ema(&mut self, price: f64) {
        if !self.initialized {
            self.fast_ema = price;
            self.slow_ema = price;
            self.initialized = true;
            return;
        }
        
        let k_fast = 2.0 / (self.fast_period as f64 + 1.0);
        let k_slow = 2.0 / (self.slow_period as f64 + 1.0);
        
        self.fast_ema = price * k_fast + self.fast_ema * (1.0 - k_fast);
        self.slow_ema = price * k_slow + self.slow_ema * (1.0 - k_slow);
    }
}

impl Strategy for EmaCrossStrategy {
    fn name(&self) -> &str {
        "EmaCrossBaseline"
    }
    
    fn act(&mut self, obs: &Observation, _ctx: &mut StrategyContext) -> Action {
        self.update_ema(obs.mid_price);
        
        if self.steps_since_action < self.cooldown_steps {
            self.steps_since_action += 1;
            return Action { r#type: ActionType::Hold as i32 };
        }

        let cross_up = self.fast_ema > self.slow_ema;
        let cross_down = self.fast_ema < self.slow_ema;
        
        let mut action = ActionType::Hold;
        
        // Simple Logic:
        // Cross Up -> Long (Open or Flip)
        // Cross Down -> Short (Open or Flip)
        // Important: This logic assumes we want to always be in the market if crossed.
        // For baseline, we can implement: if flat and cross up -> Long.
        
        let position_size = obs.position_qty.abs();
        let in_position = position_size > 1e-6; // Approximate zero
        
        if cross_up && !self.last_cross_up {
             // Crossover Up caught
             if !in_position {
                 action = ActionType::OpenLong;
             } else if obs.position_qty < 0.0 {
                 action = ActionType::CloseAll; // Then next tick open long? Or flip support?
                 // Simpler: Just close for now, next tick open.
             }
        } else if cross_down && self.last_cross_up {
             // Crossover Down caught
             if !in_position {
                 action = ActionType::OpenShort;
             } else if obs.position_qty > 0.0 {
                 action = ActionType::CloseAll;
             }
        }
        
        self.last_cross_up = cross_up;
        
        if action != ActionType::Hold {
            self.steps_since_action = 0;
        } else {
             self.steps_since_action += 1;
        }

        Action { r#type: action as i32 }
    }
    
    fn reset(&mut self) {
        self.fast_ema = 0.0;
        self.slow_ema = 0.0;
        self.initialized = false;
        self.steps_since_action = 0;
    }
}
