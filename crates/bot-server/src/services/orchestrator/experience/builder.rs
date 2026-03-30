use bot_data::experience::schema::ExperienceRow;
use bot_data::experience::reward::{RewardCalculator, RewardState};

pub struct ExperienceBuilder {
    pub episode_id: String,
    pub step_index: i32,
    pub reward_state: RewardState,
    
    // Previous Step State
    pub prev_obs: Option<Vec<f32>>,
    pub prev_action: i32,
    pub prev_log_prob: f32,
    pub prev_value: f32,
    
    pub prev_equity: f64,
    pub prev_pos_qty: f64,
    pub prev_pos_side: String,
    pub prev_entry_price: f64,
    pub prev_realized_fees: f64,
    pub prev_realized_pnl: f64,
    pub prev_realized_funding: f64,
    
    // Shaping State
    pub orders_in_step: u32,
    pub toxic_fills_in_step: u32,
    pub reward_config: bot_data::experience::reward::RewardConfig,
}

impl ExperienceBuilder {
    pub fn new(initial_equity: f64) -> Self {
        Self {
            episode_id: format!("EXP_{}", chrono::Utc::now().format("%Y%m%d_%H%M%S")),
            step_index: 0,
            reward_state: RewardState::new(initial_equity),
            prev_obs: None,
            prev_action: 0,
            prev_log_prob: 0.0,
            prev_value: 0.0,
            prev_equity: initial_equity,
            prev_pos_qty: 0.0,
            prev_pos_side: "Flat".to_string(),
            prev_entry_price: 0.0,
            prev_realized_fees: 0.0,
            prev_realized_pnl: 0.0,
            prev_realized_funding: 0.0,
            orders_in_step: 0,
            toxic_fills_in_step: 0,
            reward_config: bot_data::experience::reward::RewardConfig::default(),
        }
    }

    /// Ends the current step by calculating reward and returning an ExperienceRow.
    /// Should be called WHEN a new decision is about to be made.
    pub fn finalize_step(
        &mut self, 
        symbol: String, 
        current_ts: i64, 
        current_mid: f64,
        elapsed_ms: u32,
        current_equity: f64, 
        current_fees: f64, 
        current_funding: f64,
        exposure: f64,
        tib_count: u32
    ) -> Option<ExperienceRow> {
        let prev_obs = self.prev_obs.take()?;
        
        let reward = RewardCalculator::compute_reward_legacy(
            &mut self.reward_state, 
            current_equity,
            current_mid,
            elapsed_ms,
            self.orders_in_step,
            self.toxic_fills_in_step,
            exposure,
            tib_count,
            &[], // orchestrator doesn't track maker_fills yet
            0, // orchestrator doesn't track taker_fills yet
            0, // orchestrator doesn't track active_order_count yet
            0, // orchestrator doesn't track reprices yet
            0.0, // orchestrator doesn't track distance yet
            0.0, // realized_pnl not explicitly tracked in builder yet
            self.prev_action == 5, // CANCEL_ALL check
            false, // is_two_sided not tracked here
            false, // is_taker_action not tracked here
            self.prev_pos_qty * current_mid, // prev_exposure
            0.0, // micro_minus_mid fallthrough
            0.0, // imbalance fallthrough
            &self.reward_config
        );
        
        // Calculate fee delta
        let fees_step = (current_fees - self.prev_realized_fees) + (current_funding - self.prev_realized_funding);
        
        // Validation Log
        log::info!(
            "REWARD CALC: Eq_old={:.2} Eq_new={:.2} Reward={:.6} Trades={} Lev={:.2} FeesStep={:.4}", 
            self.prev_equity, current_equity, reward, self.orders_in_step, exposure.abs() / current_equity, fees_step
        );

        let row = ExperienceRow {
            episode_id: self.episode_id.clone(),
            symbol,
            decision_ts: current_ts,
            step_index: self.step_index,
            obs: prev_obs,
            action: self.prev_action,
            reward: reward as f32,
            equity_before: self.prev_equity,
            equity_after: current_equity,
            pos_qty_before: self.prev_pos_qty,
            pos_side_before: self.prev_pos_side.clone(),
            fees_step, 
            done: false,
            done_reason: "".to_string(),
            info_json: "{}".to_string(),
            log_prob: self.prev_log_prob,
            value_estimate: self.prev_value,
        };
        
        self.step_index += 1;
        self.prev_equity = current_equity;
        self.orders_in_step = 0; // Reset shaping counter
        self.toxic_fills_in_step = 0; // Reset toxic counter
        
        Some(row)
    }

    /// Stores the state for the next step.
    #[allow(clippy::too_many_arguments)]
    pub fn start_step(&mut self, obs: Vec<f32>, action: i32, log_prob: f32, value: f32, pos_qty: f64, pos_side: String, entry_price: f64, current_fees: f64, current_funding: f64) {
        self.prev_obs = Some(obs);
        self.prev_action = action;
        self.prev_log_prob = log_prob;
        self.prev_value = value;
        self.prev_pos_qty = pos_qty;
        self.prev_pos_side = pos_side;
        self.prev_entry_price = entry_price;
        self.prev_realized_fees = current_fees;
        self.prev_realized_funding = current_funding;
    }
}
