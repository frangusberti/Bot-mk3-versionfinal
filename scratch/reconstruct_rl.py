
import os

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig_content = f.read().replace('\r\n', '\n')

# 1. Update EpisodeHandle Struct
struct_start = "struct EpisodeHandle {"
struct_end = "}"
# We'll find the first struct and replace its fields

new_fields = """    // Config
    symbol: String,
    initial_equity: f64,
    max_pos_frac: f64,
    profit_floor_bps: f64,
    stop_loss_bps: f64,
    use_selective_entry: bool,
    entry_veto_threshold_bps: f64,
    imbalance_block_threshold: f64,
    
    // Phase P + D1 Logic
    pub use_exit_curriculum_d1: bool,
    pub maker_first_exit_timeout_ms: u32,
    pub exit_fallback_loss_bps: f64,
    pub exit_fallback_mfe_giveback_bps: f64,
    pub exit_fallback_thesis_decay_threshold: f64,
    pub exit_maker_pricing_multiplier: f32,

    // Reward config
    reward_config: RewardConfig,
    reward_exit_maker_bonus_weight: f64,

    // State
    orderbook: SimOrderBook,
    step_count: u32,
    last_tick_ts: i64,
    last_mid_price: f64,
    last_features: Option<FeatureRow>,
    action_counts: HashMap<String, u32>,
    exit_distribution: HashMap<String, u32>,
    entry_veto_count: u32,
    
    // D1 State Trackers
    exit_intent_ts: Option<i64>,
    max_trade_upnl_bps: f64,
    peak_unrealized_pnl_bps: f64,
    exit_fallback_triggered_in_step: bool,
    exit_fallback_reason_in_step: u32,
    
    // Diagnostic counters
    exit_maker_fills_in_step: u32,
    voluntary_exit_taker_fills_in_step: u32,
    gate_close_blocked_in_step: u32,
    gate_offset_blocked_in_step: u32,
    gate_imbalance_blocked_in_step: u32,
    entry_veto_count_in_step: u32,
    hard_invalid_count_in_step: u32,
    cancel_count_in_step: u32,
    accepted_as_marketable_count: u32,
    accepted_as_passive_count: u32,
    resting_fill_count: u32,
    immediate_fill_count: u32,
    liquidity_flag_unknown_count: u32,
    exit_blocked_count: u32,
    exit_blocked_pnl_sum: f64,
    realized_pnl_total: f64,
    total_win_hold_ms: u64,
    total_loss_hold_ms: u64,
    win_count: u32,
    loss_count: u32,"""

# We'll use a regex-like replace for the struct content
import re
content = re.sub(r"struct EpisodeHandle \{.*?\n\}", f"struct EpisodeHandle {{\n{new_fields}\n}}", orig_content, flags=re.DOTALL)

# 2. Re-implement is_exit_fallback_active and curriculum methods
# We'll insert them before compute_action_mask

curriculum_methods = """    fn is_exit_fallback_active(&self) -> (bool, u32) {
        if !self.use_exit_curriculum_d1 { return (true, 0); }

        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let upnl_bps = match current_pos {
            Some(p) if p.qty > 1e-9 => p.unrealized_pnl / self.exec_engine.portfolio.state.equity_usdt * 10000.0,
            _ => return (true, 0),
        };

        // 1. TIMEOUT (Reason 1)
        if let Some(intent_ts) = self.exit_intent_ts {
            let duration_ms = self.last_tick_ts - intent_ts;
            if duration_ms > self.maker_first_exit_timeout_ms as i64 {
                return (true, 1);
            }
        }

        // 2. LOSS ESCAPE (Reason 2)
        if upnl_bps < -self.exit_fallback_loss_bps { return (true, 2); }

        // 3. MFE GIVEBACK (Reason 3)
        let giveback = self.max_trade_upnl_bps - upnl_bps;
        if giveback > self.exit_fallback_mfe_giveback_bps { return (true, 3); }

        // 4. THESIS DECAY (Reason 4)
        let thesis_penalty = (self.reward_config.thesis_decay_weight * self.step_count as f64).min(0.5);
        if thesis_penalty > self.exit_fallback_thesis_decay_threshold { return (true, 4); }

        // 5. DD PROXIMITY (Reason 5)
        let equity = self.exec_engine.portfolio.state.equity_usdt;
        let dd_ratio = (self.initial_equity - equity) / self.initial_equity;
        if self.max_daily_dd > 0.0 && dd_ratio > self.max_daily_dd * 0.90 {
            return (true, 5);
        }

        (false, 0)
    }

    fn attempt_curriculum_exit(&mut self, side: Side, qty: f64) -> u32 {
        if self.exit_intent_ts.is_none() {
            self.exit_intent_ts = Some(self.last_tick_ts);
            log::info!("[D1_CURRICULUM] Exit intent recorded at ts={}", self.last_tick_ts);
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

content = content.replace("    fn compute_action_mask(&mut self) -> [f32; 10] {", curriculum_methods + "\n    fn compute_action_mask(&mut self) -> [f32; 10] {")

# 3. Update apply_action with D1 redirections and Phase P Reprice
# This is a bit complex, we'll replace the match block

new_apply_action = """    fn apply_action(&mut self, action: u32) -> (u32, bool) {
        let action_type = unsafe { std::mem::transmute(action as i32) };
        let mid = self.last_mid_price;
        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let has_pos = current_pos.is_some() && current_pos.unwrap().qty > 1e-9;
        let pos_side = current_pos.map(|p| p.side);
        let pos_qty = current_pos.map(|p| p.qty).unwrap_or(0.0);
        let target_qty = (self.initial_equity * self.max_pos_frac) / mid;

        match action_type {
            ActionType::Hold => {
                *self.action_counts.entry("HOLD".to_string()).or_insert(0) += 1;
                (0, false)
            }
            ActionType::OpenLong => {
                *self.action_counts.entry("OPEN_LONG".to_string()).or_insert(0) += 1;
                if has_pos { return (0, true); }
                (self.submit_passive_order(Side::Buy, target_qty, false), false)
            }
            ActionType::AddLong => {
                *self.action_counts.entry("ADD_LONG".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Some(Side::Buy) { return (0, true); }
                let delta = (target_qty - pos_qty).max(0.0);
                (self.submit_passive_order(Side::Buy, delta, false), false)
            }
            ActionType::ReduceLong => {
                *self.action_counts.entry("REDUCE_LONG".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Some(Side::Buy) { return (0, true); }
                (self.attempt_curriculum_exit(Side::Sell, pos_qty * 0.5), false)
            }
            ActionType::CloseLong => {
                *self.action_counts.entry("CLOSE_LONG".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Some(Side::Buy) { return (0, true); }
                (self.attempt_curriculum_exit(Side::Sell, pos_qty), false)
            }
            ActionType::OpenShort => {
                *self.action_counts.entry("OPEN_SHORT".to_string()).or_insert(0) += 1;
                if has_pos { return (0, true); }
                (self.submit_passive_order(Side::Sell, target_qty, false), false)
            }
            ActionType::AddShort => {
                *self.action_counts.entry("ADD_SHORT".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Some(Side::Sell) { return (0, true); }
                let delta = (target_qty - pos_qty).max(0.0);
                (self.submit_passive_order(Side::Sell, delta, false), false)
            }
            ActionType::ReduceShort => {
                *self.action_counts.entry("REDUCE_SHORT".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Some(Side::Sell) { return (0, true); }
                (self.attempt_curriculum_exit(Side::Buy, pos_qty * 0.5), false)
            }
            ActionType::CloseShort => {
                *self.action_counts.entry("CLOSE_SHORT".to_string()).or_insert(0) += 1;
                if !has_pos || pos_side != Some(Side::Sell) { return (0, true); }
                (self.attempt_curriculum_exit(Side::Buy, pos_qty), false)
            }
            ActionType::Reprice => {
                *self.action_counts.entry("REPR_REQ".to_string()).or_insert(0) += 1;
                if let Some(pos) = self.exec_engine.portfolio.state.positions.get(&self.symbol) {
                    let p_side = pos.side;
                    let p_qty = pos.qty;
                    let cancelled = self.cancel_all_orders();
                    self.cancel_count_in_step += cancelled;
                    let delta = target_qty - p_qty;
                    if delta.abs() > 1e-9 {
                        if delta > 0.0 {
                            self.submit_passive_order(p_side, delta, false);
                        } else {
                            let opp = if p_side == Side::Buy { Side::Sell } else { Side::Buy };
                            self.submit_passive_order(opp, delta.abs(), true);
                        }
                    }
                    (0, false)
                } else {
                    self.hard_invalid_count_in_step += 1;
                    (0, true)
                }
            }
        }
    }"""

content = re.sub(r"fn apply_action.*?\(0, false\)\s+\}\s+\}\s+\}", new_apply_action, content, flags=re.DOTALL)

# 4. Update submit_passive_order and get_synthetic_passive_price
content = content.replace("fn submit_passive_order(&mut self, side: Side, qty: f64) -> u32 {", "fn submit_passive_order(&mut self, side: Side, qty: f64, is_exit: bool) -> u32 {")
content = content.replace("fn get_synthetic_passive_price(&mut self, side: Side) -> Option<f64> {", "fn get_synthetic_passive_price(&mut self, side: Side, is_exit: bool) -> Option<f64> {")

# Phase P Price logic
cur_price_logic = """        let mut offset_bps = (spread * 0.5).max(0.2) + (vol * 1.5);
        
        let side_mult = if side == Side::Buy { 1.0 } else { -1.0 };"""

new_price_logic = """        let mut offset_bps = (spread * 0.5).max(0.2) + (vol * 1.5);
        
        if is_exit {
            let mult = if self.exit_maker_pricing_multiplier > 0.0 { self.exit_maker_pricing_multiplier as f64 } else { 1.0 };
            offset_bps *= mult;
            offset_bps = offset_bps.max(0.01);
        }

        let side_mult = if side == Side::Buy { 1.0 } else { -1.0 };"""

content = content.replace(cur_price_logic, new_price_logic)

# Final write
with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))

print("Full Reconstruction Applied")
