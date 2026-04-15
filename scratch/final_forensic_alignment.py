
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# 1. Restore is_exit_fallback_active
fallback_method = """
    fn is_exit_fallback_active(&self) -> (bool, u32) {
        if !self.use_exit_curriculum_d1 { return (false, 0); }
        let pos = match self.exec_engine.portfolio.state.positions.get(&self.symbol) {
            Some(p) if p.qty > 1e-9 => p,
            _ => { return (false, 0); }
        };

        // 1: Timeout
        if let Some(intent_ts) = self.exit_intent_ts {
            if (self.last_tick_ts - intent_ts) >= self.maker_first_exit_timeout_ms as i64 {
                return (true, 1);
            }
        }

        let upnl_bps = pos.unrealized_pnl / self.exec_engine.portfolio.state.equity_usdt * 10000.0;
        
        // 2: Loss Escape
        if upnl_bps < -self.exit_fallback_loss_bps {
            return (true, 2);
        }

        // 3: MFE Giveback Escape
        let giveback = self.peak_unrealized_pnl_bps - upnl_bps;
        if self.peak_unrealized_pnl_bps > 5.0 && giveback > self.exit_fallback_mfe_giveback_bps {
            return (true, 3);
        }

        // 4: Thesis Decay (Microprice drift)
        if let Some(ref f) = self.last_features {
            let mp_bps = f.microprice_minus_mid_bps.unwrap_or(0.0);
            let side_mult = if pos.side == Side::Buy { 1.0 } else { -1.0 };
            let drift = -mp_bps * side_mult; // positive drift = bad for pos
            if drift > self.exit_fallback_thesis_decay_threshold {
                return (true, 4);
            }
        }

        // 5: DD Limit Proximity (Panic exit if close to daily limit)
        if self.max_daily_dd > 0.0 {
            let current_dd = (self.initial_equity - self.exec_engine.portfolio.state.equity_usdt) / self.initial_equity;
            if current_dd > (self.max_daily_dd * 0.9) {
                return (true, 5);
            }
        }

        (false, 0)
    }
"""

if "fn is_exit_fallback_active" not in orig:
    # Insert after check_done
    orig = re.sub(r"(fn check_done\(.*?\n\s+\n)", r"\1" + fallback_method + "\n", orig, flags=re.DOTALL)
    # Actually, check_done ends at line 750 or so.
    # I'll just append it to EpisodeHandle impl block end.
    # impl EpisodeHandle starts at line 154. Ends before impl RlService.
    # I'll search for compute_reward which is inside impl EpisodeHandle.

# 2. Fix FeatureHealth construction
new_fh = """    fn build_feature_health(&self) -> FeatureHealth {
        FeatureHealth {
            book_age_ms: 0,
            trades_age_ms: 0,
            mark_age_ms: 0,
            funding_age_ms: 0,
            oi_age_ms: 0,
            obs_quality: 1.0,
            h1m_candles: 0,
            h5m_candles: 0,
            h15m_candles: 0,
            mid_history_len: 0,
        }
    }"""
orig = re.sub(r"fn build_feature_health.*?\}", new_fh, orig, flags=re.DOTALL)

# 3. Fix compute_reward call (add 4 args)
# 14 args currently. Needs 18.
# Missing: max_trade_upnl_bps, current_upnl_bps, num_exit_maker_fills, num_voluntary_exit_taker_fills
# realized_pnl_step is at index 8 (0-based)
# imbalance is at index 12

old_reward_call = """            RewardCalculator::compute_reward(
                &mut self.reward_state,
                equity,
                mid,
                self.decision_interval_ms,
                fees_this_step,
                exposure,
                &maker_fills,
                active_order_count,
                realized_pnl_step,
                is_taker_action,
                is_invalid,
                micro_minus_mid,
                imbalance,
                &self.reward_config,
            )"""

# I'll calculate upnl_bps for the call
upnl_calc = """        let current_upnl_bps = match self.exec_engine.portfolio.state.positions.get(&self.symbol) {
             Some(p) => p.unrealized_pnl / self.exec_engine.portfolio.state.equity_usdt * 10000.0,
             None => 0.0,
        };"""

new_reward_call = """            RewardCalculator::compute_reward(
                &mut self.reward_state,
                equity,
                mid,
                self.decision_interval_ms,
                fees_this_step,
                exposure,
                &maker_fills,
                active_order_count,
                realized_pnl_step,
                is_taker_action,
                is_invalid,
                micro_minus_mid,
                imbalance,
                self.max_trade_upnl_bps,
                current_upnl_bps,
                self.exit_maker_fills_in_step,
                self.voluntary_exit_taker_fills_in_step,
                &self.reward_config,
            )"""

if "RewardCalculator::compute_reward(" in orig:
    orig = orig.replace(old_reward_call, upnl_calc + "\n" + new_reward_call)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(orig.replace('\n', '\r\n'))
