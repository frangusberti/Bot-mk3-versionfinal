
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# Actualización total del bloque let mut episode = EpisodeHandle { ... }
# Para ser SEGUROS, reemplazamos el bloque entero que construí antes con uno que tiene TODOS los campos.

full_init = """        let mut episode = EpisodeHandle {
            replay, feature_engine, exec_engine,
            symbol: req.symbol.clone(),
            initial_equity, max_pos_frac: 0.1,
            profit_floor_bps: if cfg.profit_floor_bps > 0.0 { cfg.profit_floor_bps } else { 0.5 },
            stop_loss_bps: if cfg.stop_loss_bps > 0.0 { cfg.stop_loss_bps } else { 30.0 },
            use_selective_entry: cfg.use_selective_entry,
            entry_veto_threshold_bps: if cfg.entry_veto_threshold_bps > 0.0 { cfg.entry_veto_threshold_bps } else { 1.0 },
            imbalance_block_threshold: cfg.imbalance_block_threshold,
            orderbook: SimOrderBook::new(),
            step_count: 0, last_tick_ts: 0, last_mid_price: 0.0, last_mark_price: 0.0, last_features: None,
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
            exit_blocked_count: 0, exit_blocked_pnl_sum: 0.0, exit_blocked_1_to_4_count: 0,
            max_blocked_upnl_bps: 0.0, opportunity_lost_count: 0, realized_pnl_total: 0.0,
            win_count: 0, loss_count: 0, sum_win_hold_ms: 0, sum_loss_hold_ms: 0,
            total_win_hold_ms: 0, total_loss_hold_ms: 0,
            decision_interval_ms: decision_interval_ms.try_into().unwrap_or(100),
            use_vnext_reward: cfg.reward_as_penalty_weight > 0.0 || cfg.reward_fee_cost_weight > 0.0 || cfg.reward_thesis_decay_weight > 0.0,
            hard_disaster_dd: hard_dd, max_hold_ms: if cfg.max_hold_ms > 0 { cfg.max_hold_ms as u64 } else { 0 },
            end_ts: end_ts_val, peak_equity: initial_equity, done: false,
            last_obs: vec![0.0; OBS_DIM], cancel_count_in_step: 0, reprice_count_in_step: 0,
            post_delta_threshold_bps: cfg.post_delta_threshold_bps,
            prev_realized_pnl: 0.0, prev_exposure: 0.0,
            close_position_loss_threshold: cfg.close_position_loss_threshold,
            min_post_offset_bps: cfg.min_post_offset_bps,
            entry_veto_count: 0, entry_veto_count_in_step: 0, exit_maker_fills_in_step: 0, voluntary_exit_taker_fills_in_step: 0,
            gate_close_blocked_in_step: 0, gate_offset_blocked_in_step: 0, gate_imbalance_blocked_in_step: 0,
            hard_invalid_count_in_step: 0, accepted_as_marketable_count: 0, accepted_as_passive_count: 0,
            resting_fill_count: 0, immediate_fill_count: 0, liquidity_flag_unknown_count: 0,
            action_counts: HashMap::new(), exit_distribution: HashMap::new(),
        };"""

content = re.sub(r"let mut episode = EpisodeHandle \{.*?\};", full_init, orig, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))
