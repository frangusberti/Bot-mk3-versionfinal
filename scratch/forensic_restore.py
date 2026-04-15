
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# 1. Update Struct Fields
# Search for end of struct (just before impl EpisodeHandle)
struct_replace = """    // Phase P + D1 Logic
    pub use_exit_curriculum_d1: bool,
    pub maker_first_exit_timeout_ms: u32,
    pub exit_fallback_loss_bps: f64,
    pub exit_fallback_mfe_giveback_bps: f64,
    pub exit_fallback_thesis_decay_threshold: f64,
    pub exit_maker_pricing_multiplier: f32,

    // Reward config additions
    pub reward_exit_maker_bonus_weight: f64,

    // D1 State Trackers
    pub exit_intent_ts: Option<i64>,
    pub max_trade_upnl_bps: f64,
    pub peak_unrealized_pnl_bps: f64,
    pub exit_fallback_triggered_in_step: bool,
    pub exit_fallback_reason_in_step: u32,
    
    // Diagnostic markers
    pub exit_maker_fills_in_step: u32,
    pub voluntary_exit_taker_fills_in_step: u32,
    pub action_counts: HashMap<String, u32>,
    pub exit_distribution: HashMap<String, u32>,
    pub entry_veto_count: u32,
    pub entry_veto_count_in_step: u32,
    pub hard_invalid_count_in_step: u32,
    pub gate_close_blocked_in_step: u32,
    pub gate_offset_blocked_in_step: u32,
    pub gate_imbalance_blocked_in_step: u32,
    pub exit_blocked_count: u32,
    pub exit_blocked_pnl_sum: f64,
}"""

content = orig[:orig.find('struct EpisodeHandle {')]
content += "struct EpisodeHandle {\n"
content += "    replay: ReplayEngine,\n"
content += "    feature_engine: FeatureEngineV2,\n"
content += "    exec_engine: ExecutionEngine,\n"
content += "    symbol: String,\n"
content += "    initial_equity: f64,\n"
content += "    max_pos_frac: f64,\n"
content += "    profit_floor_bps: f64,\n"
content += "    stop_loss_bps: f64,\n"
content += "    use_selective_entry: bool,\n"
content += "    entry_veto_threshold_bps: f64,\n"
content += "    imbalance_block_threshold: f64,\n"
content += "    orderbook: SimOrderBook,\n"
content += "    step_count: u32,\n"
content += "    last_tick_ts: i64,\n"
content += "    last_mid_price: f64,\n"
content += "    last_features: Option<FeatureRow>,\n"
content += "    reward_config: RewardConfig,\n"
content += "    reward_state: RewardState,\n"
content += "    max_daily_dd: f64, // Added\n"
content += "    initial_equity_base: f64, // Added\n"
content += struct_replace

# We need to preserve everything after the struct
original_after_struct = orig[orig.find('impl EpisodeHandle {'):]
content += "\n\n" + original_after_struct

# 2. Add is_exit_fallback_active and attempt_curriculum_exit
# We'll put them before compute_action_mask
fallback_logic = """    fn is_exit_fallback_active(&self) -> (bool, u32) {
        if !self.use_exit_curriculum_d1 { return (false, 0); }

        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let upnl_bps = match current_pos {
            Some(p) if p.qty > 1e-9 => p.unrealized_pnl / self.initial_equity * 10000.0,
            _ => return (false, 0),
        };

        // 1. TIMEOUT
        if let Some(intent_ts) = self.exit_intent_ts {
            if self.last_tick_ts - intent_ts > self.maker_first_exit_timeout_ms as i64 { return (true, 1); }
        }

        // 2. LOSS ESCAPE
        if upnl_bps < -self.exit_fallback_loss_bps { return (true, 2); }

        // 3. MFE GIVEBACK
        // For diagnostic, we use peak since intent
        // (Assuming peak_unrealized_pnl_bps is being updated)
        if upnl_bps < self.peak_unrealized_pnl_bps - self.exit_fallback_mfe_giveback_bps { return (true, 3); }

        // 4. THESIS DECAY
        if self.exit_intent_ts.is_some() && upnl_bps < self.exit_fallback_thesis_decay_threshold {
             // For Forensic, if we are in intent and uPnL is near zero/below threshold, we bail
             return (true, 4);
        }

        // 5. DD PROXIMITY
        let cur_equity = self.exec_engine.portfolio.state.equity_usdt;
        let dd = (self.initial_equity - cur_equity) / self.initial_equity;
        if self.max_daily_dd > 0.0 && dd > (self.max_daily_dd * 0.9) { return (true, 5); }

        (false, 0)
    }

    fn attempt_curriculum_exit(&mut self, side: Side, qty: f64) -> u32 {
        if self.exit_intent_ts.is_none() {
            self.exit_intent_ts = Some(self.last_tick_ts);
        }
        let (fallback_active, reason) = self.is_exit_fallback_active();
        if fallback_active {
            self.exit_fallback_triggered_in_step = true;
            self.exit_fallback_reason_in_step = reason;
            self.attempt_market_exit(side, qty, self.profit_floor_bps, self.stop_loss_bps)
        } else {
            self.exit_fallback_triggered_in_step = false;
            self.exit_fallback_reason_in_step = 0;
            self.submit_passive_order(side, qty, true)
        }
    }
"""

content = content.replace("    fn compute_action_mask(&mut self) -> [f32; 10] {", fallback_logic + "\n    fn compute_action_mask(&mut self) -> [f32; 10] {")

# 3. Fix price logic (Phase P)
old_price = "let mut offset_bps = (spread * 0.5).max(0.2) + (vol * 1.5);"
new_price = """        let mut offset_bps = (spread * 0.5).max(0.2) + (vol * 1.5);
        if is_exit {
            let mult = if self.exit_maker_pricing_multiplier > 0.0 { self.exit_maker_pricing_multiplier as f64 } else { 1.0 };
            offset_bps *= mult;
            offset_bps = offset_bps.max(0.01);
        }"""
content = content.replace(old_price, new_price)

# 4. Fix submit_passive_order signature
content = content.replace("fn submit_passive_order(&mut self, side: Side, qty: f64) -> u32 {", "fn submit_passive_order(&mut self, side: Side, qty: f64, is_exit: bool) -> u32 {")
content = content.replace("fn get_synthetic_passive_price(&mut self, side: Side) -> Option<f64> {", "fn get_synthetic_passive_price(&mut self, side: Side, is_exit: bool) -> Option<f64> {")

# Fix calls to submit/get price
# (This is tricky, we'll do literal replacements where possible)
content = content.replace("self.submit_passive_order(Side::Buy, target_qty)", "self.submit_passive_order(Side::Buy, target_qty, false)")
content = content.replace("self.submit_passive_order(p_side, delta)", "self.submit_passive_order(p_side, delta, false)")
content = content.replace("self.get_synthetic_passive_price(side)", "self.get_synthetic_passive_price(side, false)")

# 5. Telemetry
old_resp = "exit_intent_active: if episode.exit_intent_ts.is_some() { 1 } else { 0 },"
new_resp = """                exit_intent_active: if episode.exit_intent_ts.is_some() { 1 } else { 0 },
                exit_fallback_triggered: if episode.exit_fallback_triggered_in_step { 1 } else { 0 },
                exit_fallback_reason: episode.exit_fallback_reason_in_step,
                time_since_exit_intent_ms: if let Some(ts) = episode.exit_intent_ts { (episode.last_tick_ts - ts).max(0) as u32 } else { 0 },"""
content = content.replace(old_resp, new_resp)

# 6. Struct Init in loop
old_init = "let mut episode = EpisodeHandle {"
new_init = """        let mut episode = EpisodeHandle {
            replay, feature_engine, exec_engine,
            symbol: req.symbol.clone(),
            initial_equity, max_pos_frac: 0.1,
            profit_floor_bps: if cfg.profit_floor_bps > 0.0 { cfg.profit_floor_bps } else { 0.5 },
            stop_loss_bps: if cfg.stop_loss_bps > 0.0 { cfg.stop_loss_bps } else { 30.0 },
            use_selective_entry: cfg.use_selective_entry,
            entry_veto_threshold_bps: if cfg.entry_veto_threshold_bps > 0.0 { cfg.entry_veto_threshold_bps } else { 1.0 },
            imbalance_block_threshold: cfg.imbalance_block_threshold,
            orderbook: SimOrderBook::new(),
            step_count: 0, last_tick_ts: 0, last_mid_price: 0.0, last_features: None,
            reward_config: RewardConfig {
                fee_cost_weight: cfg.reward_fee_cost_weight,
                as_penalty_weight: cfg.reward_as_penalty_weight,
                inventory_risk_weight: cfg.reward_inventory_risk_weight,
                trailing_mfe_penalty_weight: cfg.reward_trailing_mfe_penalty_weight,
                thesis_decay_weight: cfg.reward_thesis_decay_weight,
                use_winner_unlock: cfg.use_winner_unlock,
            },
            reward_state: RewardState::new(initial_equity),
            max_daily_dd, initial_equity_base: initial_equity,
            use_exit_curriculum_d1: cfg.use_exit_curriculum_d1,
            maker_first_exit_timeout_ms: if cfg.maker_first_exit_timeout_ms > 0 { cfg.maker_first_exit_timeout_ms } else { 3000 },
            exit_fallback_loss_bps: if cfg.exit_fallback_loss_bps > 0.0 { cfg.exit_fallback_loss_bps } else { 10.0 },
            exit_fallback_mfe_giveback_bps: if cfg.exit_fallback_mfe_giveback_bps > 0.0 { cfg.exit_fallback_mfe_giveback_bps } else { 5.0 },
            exit_fallback_thesis_decay_threshold: if cfg.exit_fallback_thesis_decay_threshold > 0.0 { cfg.exit_fallback_thesis_decay_threshold } else { 0.45 },
            exit_maker_pricing_multiplier: if cfg.exit_maker_pricing_multiplier > 0.0 { cfg.exit_maker_pricing_multiplier } else { 1.0 },
            reward_exit_maker_bonus_weight: cfg.reward_exit_maker_bonus_weight,
            exit_intent_ts: None, max_trade_upnl_bps: 0.0, peak_unrealized_pnl_bps: 0.0,
            exit_fallback_triggered_in_step: false, exit_fallback_reason_in_step: 0,
            exit_maker_fills_in_step: 0, voluntary_exit_taker_fills_in_step: 0,
            action_counts: HashMap::new(), exit_distribution: HashMap::new(), entry_veto_count: 0,
            entry_veto_count_in_step: 0, hard_invalid_count_in_step: 0,
            gate_close_blocked_in_step: 0, gate_offset_blocked_in_step: 0, gate_imbalance_blocked_in_step: 0,
            exit_blocked_count: 0, exit_blocked_pnl_sum: 0.0, realized_pnl_total: 0.0,"""

# We'll replace the old init from old_init all the way to '};'
# (Be careful with the closing brace)
content = re.sub(r"let mut episode = EpisodeHandle \{.*?\};", new_init + "\n        };", content, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))
