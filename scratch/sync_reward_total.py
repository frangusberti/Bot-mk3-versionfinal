
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# 1. Añadir current_trade_start_ts al struct
if "current_trade_start_ts: Option<i64>," not in orig:
    orig = orig.replace("pub initial_equity_base: f64,", "pub initial_equity_base: f64,\n    pub current_trade_start_ts: Option<i64>,")

# 2. Re-escribir el mapeo de RewardConfig
# Buscamos el bloque reward_config: RewardConfig { ... } en la inicialización

new_reward_init = """            reward_config: RewardConfig {
                fee_cost_weight: cfg.reward_fee_cost_weight,
                as_penalty_weight: cfg.reward_as_penalty_weight,
                as_horizon_ms: if cfg.reward_as_horizon_ms > 0 { cfg.reward_as_horizon_ms } else { 3000 },
                inventory_risk_weight: cfg.reward_inventory_risk_weight,
                realized_pnl_bonus_weight: if cfg.reward_realized_pnl_bonus_weight > 0.0 { cfg.reward_realized_pnl_bonus_weight } else { 0.0 },
                invalid_action_penalty: 0.1,
                thesis_decay_weight: cfg.reward_thesis_decay_weight,
                trailing_mfe_penalty_weight: cfg.reward_trailing_mfe_penalty_weight,
                reward_consolidated_variant: false,
                exit_taker_penalty_weight: 0.0,
                exit_maker_bonus_weight: cfg.reward_exit_maker_bonus_weight,
                overtrading_penalty: cfg.reward_overtrading_penalty,
                exposure_penalty: cfg.reward_exposure_penalty,
                toxic_fill_penalty: cfg.reward_toxic_fill_penalty,
                tib_bonus: cfg.reward_tib_bonus_bps / 10000.0,
                maker_fill_bonus: cfg.reward_maker_fill_bonus,
                taker_fill_penalty: cfg.reward_taker_fill_penalty,
                idle_posting_penalty: cfg.reward_idle_posting_penalty,
                mtm_penalty_window_ms: cfg.reward_mtm_penalty_window_ms,
                mtm_penalty_multiplier: cfg.reward_mtm_penalty_multiplier,
                reprice_penalty_bps: cfg.reward_reprice_penalty_bps,
                reward_distance_to_mid_penalty: cfg.reward_distance_to_mid_penalty,
                reward_skew_penalty_weight: cfg.reward_skew_penalty_weight,
                reward_adverse_selection_bonus_multiplier: cfg.reward_adverse_selection_bonus_multiplier,
                reward_realized_pnl_multiplier: cfg.reward_realized_pnl_multiplier,
                reward_cancel_all_penalty: cfg.reward_cancel_all_penalty,
                reward_inventory_change_penalty: cfg.reward_inventory_change_penalty,
                reward_two_sided_bonus: cfg.reward_two_sided_bonus,
                reward_taker_action_penalty: cfg.reward_taker_action_penalty,
                reward_quote_presence_bonus: cfg.reward_quote_presence_bonus,
            },
            current_trade_start_ts: None,"""

# Reemplazo de bloque reward_config
orig = re.sub(r"reward_config: RewardConfig \{.*?\n\s+\},", new_reward_init, orig, flags=re.DOTALL)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(orig.replace('\n', '\r\n'))
