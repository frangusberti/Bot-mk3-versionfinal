
import os
import re

filepath = r"c:\Bot mk3\crates\bot-server\src\services\rl.rs"
with open(filepath, 'r', encoding='utf-8') as f:
    orig = f.read().replace('\r\n', '\n')

# 1. Definición Completa del Struct (Sin dejarse nada)
full_struct = """struct EpisodeHandle {
    replay: ReplayEngine,
    feature_engine: FeatureEngineV2,
    exec_engine: ExecutionEngine,

    // Config
    symbol: String,
    initial_equity: f64,
    max_pos_frac: f64,
    profit_floor_bps: f64,
    stop_loss_bps: f64,
    use_selective_entry: bool,
    entry_veto_threshold_bps: f64,
    imbalance_block_threshold: f64,
    
    // Phase P + D1 Logic
    pub use_exit_curriculum_d1: bool,
    pub maker_first_exit_timeout_ms: u32,
    pub exit_fallback_loss_bps: f64,
    pub exit_fallback_mfe_giveback_bps: f64,
    pub exit_fallback_thesis_decay_threshold: f64,
    pub exit_maker_pricing_multiplier: f32,
    pub reward_exit_maker_bonus_weight: f64,

    // State
    orderbook: SimOrderBook,
    step_count: u32,
    last_tick_ts: i64,
    last_mid_price: f64,
    last_mark_price: f64,
    last_features: Option<FeatureRow>,
    action_counts: HashMap<String, u32>,
    exit_distribution: HashMap<String, u32>,
    entry_veto_count: u32,
    
    // D1 State Trackers
    pub exit_intent_ts: Option<i64>,
    pub max_trade_upnl_bps: f64,
    pub peak_unrealized_pnl_bps: f64,
    pub exit_fallback_triggered_in_step: bool,
    pub exit_fallback_reason_in_step: u32,

    // Metric Trackers (Legacy)
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

    // Decision Logic
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
    pub post_delta_threshold_bps: f64,
    pub prev_realized_pnl: f64,
    pub prev_exposure: f64,
    pub close_position_loss_threshold: f64,
    pub min_post_offset_bps: f64,
    
    // Per-Step Diagnostic Counters
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
    
    // Initial State Ref
    pub initial_equity_base: f64,
}"""

content = re.sub(r"struct EpisodeHandle \{.*?\n\}", full_struct, orig, flags=re.DOTALL)

# 2. Restaurar compute_action_mask (si falta)
if "fn compute_action_mask" not in content:
    mask_method = """    fn compute_action_mask(&mut self) -> [f32; 10] {
        let mut mask = [1.0f32; 10]; 
        let current_pos = self.exec_engine.portfolio.state.positions.get(&self.symbol);
        let has_pos = current_pos.is_some() && current_pos.unwrap().qty > 1e-9;
        let pos_side = current_pos.map(|p| p.side);

        let (fallback_active, _) = self.is_exit_fallback_active();

        // 1: OPEN_LONG / 2: ADD_LONG / 3: REDUCE_LONG / 4: CLOSE_LONG
        // 5: OPEN_SHORT / 6: ADD_SHORT / 7: REDUCE_SHORT / 8: CLOSE_SHORT
        // Si no tenemos posición, no podemos reducir ni cerrar ni repriciar con sentido
        if !has_pos {
            mask[3] = 0.0; mask[4] = 0.0;
            mask[7] = 0.0; mask[8] = 0.0;
            mask[9] = 0.0;
        } else {
            // Si tenemos LONG, no podemos abrir SHORT
            if pos_side == Some(Side::Buy) {
                mask[1] = 0.0; mask[2] = 0.0;
                mask[5] = 0.0; mask[6] = 0.0;
            } else {
                mask[5] = 0.0; mask[6] = 0.0;
                mask[1] = 0.0; mask[2] = 0.0;
            }
        }
        mask
    }"""
    content = content.replace("impl EpisodeHandle {", "impl EpisodeHandle {\n" + mask_method)

with open(filepath, 'w', encoding='utf-8', newline='') as f:
    f.write(content.replace('\n', '\r\n'))
