
import os

filepath = r"c:\\Bot mk3\\crates\\bot-server\\src\\services\\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read()

# Normalize for matching
content = content.replace('\r\n', '\n')

# 1. struct fields
search_struct = "pub exit_maker_pricing_multiplier: f32,"
if search_struct in content:
    content = content.replace(search_struct, search_struct + "\n    exit_fallback_reason_in_step: u32,")

# 2. is_exit_fallback_active rewrite (Using the fixed one with the DD proximity I saw in the clean file)
old_fn = """    fn is_exit_fallback_active(&self) -> bool {
        if !self.use_exit_curriculum_d1 { return true; }

        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let (has_pos, pos_qty, upnl) = match current_pos {
            Some(p) if p.qty > 1e-9 => (true, p.qty, p.unrealized_pnl),
            _ => return true,
        };

        let equity = self.exec_engine.portfolio.state.equity_usdt;
        let upnl_bps = upnl / equity * 10000.0;
        
        // 1. Loss escape
        if upnl_bps < -self.exit_fallback_loss_bps { return true; }

        // 2. MFE giveback (trailing from peak)
        let giveback = self.max_trade_upnl_bps - upnl_bps;
        if giveback > self.exit_fallback_mfe_giveback_bps { return true; }

        // 3. Thesis decay (step-based)
        let thesis_penalty = (self.reward_config.thesis_decay_weight * self.step_count as f64).min(0.5);
        if thesis_penalty > self.exit_fallback_thesis_decay_threshold { return true; }

        // 4. Timeout since first intent
        if let Some(intent_ts) = self.exit_intent_ts {
            let duration_ms = self.last_tick_ts - intent_ts;
            if duration_ms > self.maker_first_exit_timeout_ms as i64 {
                return true;
            }
        }

        // 5. DD Limit proximity (Panic if we are at 80% of daily limit)
        let dd_ratio = (self.initial_equity - equity) / self.initial_equity;
        if self.max_daily_dd > 0.0 && dd_ratio > self.max_daily_dd * 0.8 {
            return true;
        }

        false
    }"""

new_fn = """    fn is_exit_fallback_active(&self) -> (bool, u32) {
        if !self.use_exit_curriculum_d1 { return (true, 0); }

        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let (has_pos, _pos_qty, upnl) = match current_pos {
            Some(p) if p.qty > 1e-9 => (true, p.qty, p.unrealized_pnl),
            _ => return (true, 0),
        };

        let equity = self.exec_engine.portfolio.state.equity_usdt;
        let upnl_bps = upnl / equity * 10000.0;
        
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
        let dd_ratio = (self.initial_equity - equity) / self.initial_equity;
        if self.max_daily_dd > 0.0 && dd_ratio > self.max_daily_dd * 0.90 {
            return (true, 5);
        }

        (false, 0)
    }"""

if old_fn in content:
    content = content.replace(old_fn, new_fn)
else:
    print("WARNING: Fallback fn not found exactly")

# 3. compute_action_mask call
search_mask = "let fallback_active = self.is_exit_fallback_active();"
if search_mask in content:
    content = content.replace(search_mask, "let (fallback_active, _) = self.is_exit_fallback_active();")

# 4. attempt_curriculum_exit rewrite
old_curriculum = """        if self.is_exit_fallback_active() {
            // Fallback triggered: allow Taker
            self.exit_fallback_triggered_in_step = true;
            self.attempt_market_exit(side, qty, self.profit_floor_bps, self.stop_loss_bps)
        } else {
            // No fallback: Redirection to Maker
            self.exit_fallback_triggered_in_step = false;
            self.submit_passive_order(side, qty, true)
        }"""

new_curriculum = """        let (fallback_active, reason) = self.is_exit_fallback_active();
        if fallback_active {
            // Fallback triggered: allow Taker
            self.exit_fallback_triggered_in_step = true;
            self.exit_fallback_reason_in_step = reason;
            self.attempt_market_exit(side, qty, self.profit_floor_bps, self.stop_loss_bps)
        } else {
            // No fallback: Redirection to Maker
            self.exit_fallback_triggered_in_step = false;
            self.exit_fallback_reason_in_step = 0;
            self.submit_passive_order(side, qty, true)
        }"""

if old_curriculum in content:
    content = content.replace(old_curriculum, new_curriculum)

# 5. compute_step_info
search_step_info = "exit_intent_active: if episode.exit_intent_ts.is_some() { 1 } else { 0 },"
if search_step_info in content:
    content = content.replace(search_step_info, search_step_info + "\n                exit_fallback_reason: episode.exit_fallback_reason_in_step,")

# 6. Struct initialization in new()
search_init = "exit_intent_ts: None,"
if search_init in content:
    content = content.replace(search_init, search_init + "\n            exit_fallback_reason_in_step: 0,")

# Final write with CRLF
with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))

print("Patch applied successfully")
