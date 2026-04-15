
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    content = f.read().replace('\r\n', '\n')

# Actualización de la inicialización del struct en reset / new_with_config
# Los campos que añadimos:
# use_exit_curriculum_d1, maker_first_exit_timeout_ms, exit_fallback_loss_bps, etc.

new_init_fields = """            use_exit_curriculum_d1: cfg.use_exit_curriculum_d1,
            maker_first_exit_timeout_ms: if cfg.maker_first_exit_timeout_ms > 0 { cfg.maker_first_exit_timeout_ms } else { 3000 },
            exit_fallback_loss_bps: if cfg.exit_fallback_loss_bps > 0.0 { cfg.exit_fallback_loss_bps } else { 10.0 },
            exit_fallback_mfe_giveback_bps: if cfg.exit_fallback_mfe_giveback_bps > 0.0 { cfg.exit_fallback_mfe_giveback_bps } else { 5.0 },
            exit_fallback_thesis_decay_threshold: if cfg.exit_fallback_thesis_decay_threshold > 0.0 { cfg.exit_fallback_thesis_decay_threshold } else { 0.45 },
            exit_maker_pricing_multiplier: if cfg.exit_maker_pricing_multiplier > 0.0 { cfg.exit_maker_pricing_multiplier } else { 1.0 },

            reward_config: RewardConfig {
                fee_cost_weight: cfg.reward_fee_cost_weight,
                as_penalty_weight: cfg.reward_as_penalty_weight,
                inventory_risk_weight: cfg.reward_inventory_risk_weight,
                trailing_mfe_penalty_weight: cfg.reward_trailing_mfe_penalty_weight,
                thesis_decay_weight: cfg.reward_thesis_decay_weight,
                use_winner_unlock: cfg.use_winner_unlock,
            },
            reward_exit_maker_bonus_weight: cfg.reward_exit_maker_bonus_weight,

            orderbook: SimOrderBook::new(),
            step_count: 0,
            last_tick_ts: 0,
            last_mid_price: 0.0,
            last_features: None,
            action_counts: HashMap::new(),
            exit_distribution: HashMap::new(),
            entry_veto_count: 0,
            
            exit_intent_ts: None,
            max_trade_upnl_bps: 0.0,
            peak_unrealized_pnl_bps: 0.0,
            exit_fallback_triggered_in_step: false,
            exit_fallback_reason_in_step: 0,

            exit_maker_fills_in_step: 0,
            voluntary_exit_taker_fills_in_step: 0,
            gate_close_blocked_in_step: 0,
            gate_offset_blocked_in_step: 0,
            gate_imbalance_blocked_in_step: 0,
            entry_veto_count_in_step: 0,
            hard_invalid_count_in_step: 0,
            cancel_count_in_step: 0,
            accepted_as_marketable_count: 0,
            accepted_as_passive_count: 0,
            resting_fill_count: 0,
            immediate_fill_count: 0,
            liquidity_flag_unknown_count: 0,
            exit_blocked_count: 0,
            exit_blocked_pnl_sum: 0.0,
            realized_pnl_total: 0.0,
            total_win_hold_ms: 0,
            total_loss_hold_ms: 0,
            win_count: 0,
            loss_count: 0,"""

# Buscamos la asignación de campos en el constructor y la reemplazamos
# Normalmente empieza después de: EpisodeHandle {
content = re.sub(r"EpisodeHandle \{.*?\n\s+\}", f"EpisodeHandle {{\n{new_init_fields}\n        }}", content, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))

print("Constructor updated")
