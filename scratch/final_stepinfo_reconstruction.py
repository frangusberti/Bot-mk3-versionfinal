
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# Definición del bloque StepInfo COMPLETO (49 campos)
complete_step_info = """            info: Some(StepInfo {
                ts: episode.last_tick_ts,
                reason: final_reason.to_string(),
                mid_price: episode.last_mid_price,
                mark_price: episode.last_mid_price,
                trades_executed: trades_this_step,
                maker_fills: maker_fills,
                toxic_fills: toxic_fills,
                stale_expiries: stale_expiries,
                cancel_count: cancel_count,
                active_order_count: active_order_count,
                reprice_count: episode.reprice_count_in_step,
                fills: fills,
                gate_close_blocked: episode.gate_close_blocked_in_step,
                gate_offset_blocked: episode.gate_offset_blocked_in_step,
                gate_imbalance_blocked: episode.gate_imbalance_blocked_in_step,
                action_counts: episode.action_counts.clone(),
                realized_pnl_total: episode.realized_pnl_total,
                avg_win_hold_ms: if episode.win_count > 0 { episode.total_win_hold_ms as f64 / episode.win_count as f64 } else { 0.0 },
                avg_loss_hold_ms: if episode.loss_count > 0 { episode.total_loss_hold_ms as f64 / episode.loss_count as f64 } else { 0.0 },
                exit_distribution: episode.exit_distribution.clone(),
                entry_veto_count: episode.entry_veto_count,
                exit_blocked_count: episode.exit_blocked_count,
                exit_blocked_avg_pnl_bps: if episode.exit_blocked_count > 0 { episode.exit_blocked_pnl_sum / episode.exit_blocked_count as f64 } else { 0.0 },
                exit_blocked_1_to_4_count: episode.exit_blocked_1_to_4_count,
                opportunity_lost_count: episode.opportunity_lost_count,
                thesis_decay_penalty: episode.reward_state.last_thesis_penalty,
                is_invalid,
                soft_veto_count_in_step: episode.entry_veto_count_in_step,
                hard_invalid_count_in_step: episode.hard_invalid_count_in_step,
                exit_maker_fills: episode.exit_maker_fills_in_step,
                voluntary_exit_taker_fills: episode.voluntary_exit_taker_fills_in_step,
                accepted_as_marketable_count: episode.accepted_as_marketable_count,
                accepted_as_passive_count: episode.accepted_as_passive_count,
                resting_fill_count: episode.resting_fill_count,
                immediate_fill_count: episode.immediate_fill_count,
                liquidity_flag_unknown_count: episode.liquidity_flag_unknown_count,
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
            }),"""

orig = re.sub(r"info: Some\(StepInfo \{.*?\n\s+\}\),", complete_step_info, orig, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(orig.replace('\n', '\r\n'))
