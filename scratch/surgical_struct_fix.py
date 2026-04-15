
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# 1. Fix nested is_exit_fallback_active
# We need to close get_synthetic_passive_price before starting is_exit_fallback_active
# Currently line 803 is the end of the first half of get_synthetic_passive_price? 
# No, let's just replace the whole messed up area.

messed_up_pattern = r"fn get_synthetic_passive_price.*?fn is_exit_fallback_active.*?\(false, 0\)\n\s+\}"
# This is too risky. I'll search for the exact boundary.

# Better: delete the nested function and restore it at the end of the impl block.
orig = orig.replace("fn is_exit_fallback_active(&self) -> (bool, u32) {", "} fn is_exit_fallback_active(&self) -> (bool, u32) {")
# But wait, that might leave an extra closing brace later.

# 2. Fix submit_passive_order in Reprice
orig = orig.replace("self.submit_passive_order(pos_side, target_qty - pos_qty)", "self.submit_passive_order(pos_side, target_qty - pos_qty, false)")

# 3. Fix compute_reward call - ensure it has 18 args and the config is correct.
# Wait, I'll just rewrite the methods get_synthetic_passive_price and is_exit_fallback_active from scratch.

fixed_methods = """
    fn get_synthetic_passive_price(&self, side: Side, is_exit: bool) -> Option<f64> {
        let mid = self.last_mid_price;
        if mid <= 0.0 { return None; }

        let f = match self.last_features.as_ref() {
            Some(f) => f,
            None => {
                log::warn!("RL_SYNTHETIC_PRICE: Missing last_features, cannot calculate price");
                return None;
            }
        };
        
        // Extract features
        let spread = f.spread_bps.unwrap_or(1.0).max(0.05);
        let vol = f.rv_5s.unwrap_or(0.2).max(0.0);
        let imbalance = f.trade_imbalance_5s.unwrap_or(0.0);

        // Adaptive Offset
        let mut offset_bps = (spread * 0.5).max(0.2) + (vol * 1.5);
        if is_exit {
            let mult = if self.exit_maker_pricing_multiplier > 0.0 { self.exit_maker_pricing_multiplier as f64 } else { 1.0 };
            offset_bps *= mult;
            offset_bps = offset_bps.max(0.01);
        }

        // Adverse selection shift
        let side_mult = if side == Side::Buy { 1.0 } else { -1.0 };
        if (imbalance * side_mult) < 0.0 {
            offset_bps += imbalance.abs() * vol * 2.0;
        }

        let price = match side {
            Side::Buy => mid * (1.0 - offset_bps / 10000.0),
            Side::Sell => mid * (1.0 + offset_bps / 10000.0),
        };

        if self.step_count % 100 == 0 {
            log::info!("RL_SYNTHETIC_PRICE: side={:?}, offset={:.2}bps, price={:.2}, mid={:.2}, vol={:.2}, imb={:.2}", 
                side, offset_bps, price, mid, vol, imbalance);
        }
            
        Some(price)
    }

    fn is_exit_fallback_active(&self) -> (bool, u32) {
        if !self.use_exit_curriculum_d1 { return (false, 0); }
        let pos = match self.exec_engine.portfolio.state.positions.get(&self.symbol) {
            Some(p) if p.qty > 1e-9 => p,
            _ => { return (false, 0); }
        };

        if let Some(intent_ts) = self.exit_intent_ts {
            if (self.last_tick_ts - intent_ts) >= self.maker_first_exit_timeout_ms as i64 {
                return (true, 1);
            }
        }

        let upnl_bps = pos.unrealized_pnl / self.exec_engine.portfolio.state.equity_usdt * 10000.0;
        if upnl_bps < -self.exit_fallback_loss_bps {
            return (true, 2);
        }

        let giveback = self.peak_unrealized_pnl_bps - upnl_bps;
        if self.peak_unrealized_pnl_bps > 5.0 && giveback > self.exit_fallback_mfe_giveback_bps {
            return (true, 3);
        }

        if let Some(ref f) = self.last_features {
            let mp_bps = f.microprice_minus_mid_bps.unwrap_or(0.0);
            let side_mult = if pos.side == Side::Buy { 1.0 } else { -1.0 };
            let drift = -mp_bps * side_mult;
            if drift > self.exit_fallback_thesis_decay_threshold {
                return (true, 4);
            }
        }

        if self.max_daily_dd > 0.0 {
            let current_dd = (self.initial_equity - self.exec_engine.portfolio.state.equity_usdt) / self.initial_equity;
            if current_dd > (self.max_daily_dd * 0.9) {
                return (true, 5);
            }
        }

        (false, 0)
    }
"""

# Replace the whole corrupted block from starting of get_synthetic_passive_price to end of is_exit_fallback_active
orig = re.sub(r"fn get_synthetic_passive_price.*?\(false, 0\)\s+\}", fixed_methods, orig, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(orig.replace('\n', '\r\n'))
