
import os

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

content = "".join(lines)

# 1. replace is_exit_fallback_active
old_fn_block = """    fn is_exit_fallback_active(&self) -> bool {
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

new_fn_block = """    fn is_exit_fallback_active(&self) -> (bool, u32) {
        if !self.use_exit_curriculum_d1 { return (true, 0); }

        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let (has_pos, pos_qty, upnl) = match current_pos {
            Some(p) if p.qty > 1e-9 => (true, p.qty, p.unrealized_pnl),
            _ => return (true, 0),
        };

        let equity = self.exec_engine.portfolio.state.equity_usdt;
        let upnl_bps = upnl / equity * 10000.0;
        
        // 1. Loss escape (Emergency Exit)
        if upnl_bps < -self.exit_fallback_loss_bps { return (true, 2); }

        // 2. MFE giveback (Trailing Exit)
        let giveback = self.max_trade_upnl_bps - upnl_bps;
        if giveback > self.exit_fallback_mfe_giveback_bps { return (true, 3); }

        // 3. Thesis decay (Time vs PnL decay)
        let thesis_penalty = (self.reward_config.thesis_decay_weight * self.step_count as f64).min(0.5);
        if thesis_penalty > self.exit_fallback_thesis_decay_threshold { return (true, 4); }

        // 4. Timeout (Maker Wait Exhausted)
        if let Some(intent_ts) = self.exit_intent_ts {
            let duration_ms = self.last_tick_ts - intent_ts;
            if duration_ms > self.maker_first_exit_timeout_ms as i64 {
                return (true, 1);
            }
        }

        // 5. DD Limit proximity
        let dd_ratio = (self.initial_equity - equity) / self.initial_equity;
        if self.max_daily_dd > 0.0 && dd_ratio > self.max_daily_dd * 0.90 {
            return (true, 5);
        }

        (false, 0)
    }"""

# Normalización total
old_fn_block = old_fn_block.replace('\r', '')
new_fn_block = new_fn_block.replace('\r', '')
content_clean = content.replace('\r', '')

if old_fn_block in content_clean:
    content_clean = content_clean.replace(old_fn_block, new_fn_block)
    # Volver a poner los \r\n para Windows para no romper el estilo del archivo
    content = content_clean.replace('\n', '\r\n')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print("SUCCESS: replaced fallback function")
else:
    print("FATAL: could not find old_fn_block even after fix")
