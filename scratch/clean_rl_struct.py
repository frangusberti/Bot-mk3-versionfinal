
import os

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_struct = """struct EpisodeHandle {
    replay: ReplayEngine,
    feature_engine: FeatureEngineV2,
    exec_engine: ExecutionEngine,
    symbol: String,
    initial_equity: f64,
    max_pos_frac: f64,
    profit_floor_bps: f64,
    stop_loss_bps: f64,
    use_selective_entry: bool,
    entry_veto_threshold_bps: f64,
    imbalance_block_threshold: f64,
    pub use_exit_curriculum_d1: bool,
    pub maker_first_exit_timeout_ms: u32,
    pub exit_fallback_loss_bps: f64,
    pub exit_fallback_mfe_giveback_bps: f64,
    pub exit_fallback_thesis_decay_threshold: f64,
    pub exit_maker_pricing_multiplier: f32,
    pub reward_exit_maker_bonus_weight: f64,
    orderbook: SimOrderBook,
    step_count: u32,
    last_tick_ts: i64,
    last_mid_price: f64,
    last_mark_price: f64,
    last_features: Option<FeatureRow>,
    action_counts: HashMap<String, u32>,
    exit_distribution: HashMap<String, u32>,
    entry_veto_count: u32,
    pub exit_intent_ts: Option<i64>,
    pub max_trade_upnl_bps: f64,
    pub peak_unrealized_pnl_bps: f64,
    pub exit_fallback_triggered_in_step: bool,
    pub exit_fallback_reason_in_step: u32,
    pub exit_blocked_count: u32,
    pub exit_blocked_pnl_sum: f64,
    pub exit_blocked_1_to_4_count: u32,
    pub max_blocked_upnl_bps: f64,
    pub opportunity_lost_count: u32,
    pub realized_pnl_total: f64,
    pub win_count: u32,
    pub loss_count: u32,
    pub sum_win_hold_ms: u64,
    pub sum_loss_hold_ms: u64,
    pub total_win_hold_ms: u64,
    pub total_loss_hold_ms: u64,
    pub decision_interval_ms: u32,
    pub use_vnext_reward: bool,
    pub reward_config: RewardConfig,
    pub reward_state: RewardState,
    pub hard_disaster_dd: f64,
    pub max_daily_dd: f64,
    pub max_hold_ms: u64,
    pub end_ts: i64,
    pub peak_equity: f64,
    pub done: bool,
    pub last_obs: Vec<f32>,
    pub cancel_count_in_step: u32,
    pub reprice_count_in_step: u32,
    pub current_trade_start_ts: Option<i64>,
    pub post_delta_threshold_bps: f64,
    pub prev_realized_pnl: f64,
    pub prev_exposure: f64,
    pub close_position_loss_threshold: f64,
    pub min_post_offset_bps: f64,
    pub entry_veto_count_in_step: u32,
    pub exit_maker_fills_in_step: u32,
    pub voluntary_exit_taker_fills_in_step: u32,
    pub gate_close_blocked_in_step: u32,
    pub gate_offset_blocked_in_step: u32,
    pub gate_imbalance_blocked_in_step: u32,
    pub hard_invalid_count_in_step: u32,
    pub accepted_as_marketable_count: u32,
    pub accepted_as_passive_count: u32,
    pub resting_fill_count: u32,
    pub immediate_fill_count: u32,
    pub liquidity_flag_unknown_count: u32,
    pub initial_equity_base: f64,
}
"""

out_lines = []
skip = False
for line in lines:
    if "struct EpisodeHandle {" in line:
        out_lines.append(new_struct)
        skip = True
    elif skip and "impl EpisodeHandle {" in line:
        out_lines.append(line)
        skip = False
    elif not skip:
        out_lines.append(line)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.writelines(out_lines)
