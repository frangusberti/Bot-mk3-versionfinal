
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# 1. Update EpisodeHandle Struct - Add legacy fields back
struct_replace = """    pub exit_blocked_1_to_4_count: u32,
    pub max_blocked_upnl_bps: f64,
    pub opportunity_lost_count: u32,
    pub win_count: u32,
    pub loss_count: u32,
    pub sum_win_hold_ms: u64,
    pub sum_loss_hold_ms: u64,
    pub total_win_hold_ms: u64,
    pub total_loss_hold_ms: u64,"""

content = re.sub(r"    pub realized_pnl_total: f64,.*?\n    pub loss_count: u32,", f"    pub realized_pnl_total: f64,\n{struct_replace}", orig, flags=re.DOTALL)

# 2. Update StepInfo Mapping - Remove fees_paid, fix mark_price
# We use the block we had before
new_step_info = """            info: Some(StepInfo {
                ts: episode.last_tick_ts,
                reason: final_reason.to_string(),
                mid_price: episode.last_mid_price,
                mark_price: episode.last_mid_price,
                position_qty: episode.exec_engine.portfolio.state.positions.get(&episode.symbol).map(|p| p.qty).unwrap_or(0.0),
                unrealized_pnl: episode.exec_engine.portfolio.state.positions.get(&episode.symbol).map(|p| p.unrealized_pnl).unwrap_or(0.0),
                equity: episode.exec_engine.portfolio.state.equity_usdt,
                trades_executed: trades_this_step,
                maker_fills,
                immediate_fill_count: episode.immediate_fill_count,
                resting_fill_count: episode.resting_fill_count,
                liquidity_flag_unknown_count: episode.liquidity_flag_unknown_count,
                stale_expiries: 0,
                cancel_count: episode.cancel_count_in_step,
                active_order_count: episode.exec_engine.portfolio.state.active_orders.len() as u32,
                reprice_count: episode.reprice_count_in_step,
                fills: fills,
                action_mask: episode.compute_action_mask().to_vec(),
                invalid_open_marketable_count: 0,
                invalid_close_flat_count: episode.hard_invalid_count_in_step,
                invalid_reprice_empty_count: 0,
                invalid_pos_side_mismatch_count: 0,
                masked_action_chosen_count: 0,
                veto_long_flow_count: episode.entry_veto_count_in_step,
                veto_long_bb_count: 0,
                veto_long_dead_regime_count: 0,
                exit_intent_active: if episode.exit_intent_ts.is_some() { 1 } else { 0 },
                exit_fallback_triggered: if episode.exit_fallback_triggered_in_step { 1 } else { 0 },
                time_since_exit_intent_ms: if let Some(ts) = episode.exit_intent_ts { (episode.last_tick_ts - ts).max(0) as u32 } else { 0 },
                exit_fallback_reason: episode.exit_fallback_reason_in_step,
                action_counts: episode.action_counts.clone(),
                realized_pnl_total: episode.realized_pnl_total,
                avg_win_hold_ms: if episode.win_count > 0 { episode.sum_win_hold_ms as f64 / episode.win_count as f64 } else { 0.0 },
                avg_loss_hold_ms: if episode.loss_count > 0 { episode.sum_loss_hold_ms as f64 / episode.loss_count as f64 } else { 0.0 },
                exit_distribution: episode.exit_distribution.clone(),
                entry_veto_count: episode.entry_veto_count,
                exit_blocked_count: episode.exit_blocked_count,
                exit_blocked_avg_pnl_bps: if episode.exit_blocked_count > 0 { episode.exit_blocked_pnl_sum / episode.exit_blocked_count as f64 } else { 0.0 },
                exit_blocked_1_to_4_count: episode.exit_blocked_1_to_4_count,
                opportunity_lost_count: episode.opportunity_lost_count,
                thesis_decay_penalty: episode.reward_state.last_thesis_penalty,
                is_invalid,
                gate_close_blocked: episode.gate_close_blocked_in_step,
                gate_offset_blocked: episode.gate_offset_blocked_in_step,
                gate_imbalance_blocked: episode.gate_imbalance_blocked_in_step,
                accepted_as_marketable_count: episode.accepted_as_marketable_count,
                accepted_as_passive_count: episode.accepted_as_passive_count,
                exit_maker_fills: episode.exit_maker_fills_in_step,
                voluntary_exit_taker_fills: episode.voluntary_exit_taker_fills_in_step,
                soft_veto_count_in_step: episode.entry_veto_count_in_step,
                hard_invalid_count_in_step: episode.hard_invalid_count_in_step,
                toxic_fills: 0,
            }),"""

content = re.sub(r"info: Some\(StepInfo \{.*?\n\s+\}\),", new_step_info, content, flags=re.DOTALL)

# 3. Update Initializer
init_replace = """            exit_blocked_count: 0,
            exit_blocked_pnl_sum: 0.0,
            exit_blocked_1_to_4_count: 0,
            max_blocked_upnl_bps: 0.0,
            opportunity_lost_count: 0,
            realized_pnl_total: 0.0,
            win_count: 0,
            loss_count: 0,
            sum_win_hold_ms: 0,
            sum_loss_hold_ms: 0,
            total_win_hold_ms: 0,
            total_loss_hold_ms: 0,"""

content = re.sub(r"exit_blocked_count: 0,.*?\n\s+loss_count: 0,", init_replace, content, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))
