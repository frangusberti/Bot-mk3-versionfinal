
#[derive(Debug, Clone)]
pub struct PendingMtm {
    pub initial_mid: f64,
    pub side: f64, // 1.0 for Buy, -1.0 for Sell
    pub remaining_ms: i64,
}

#[derive(Debug, Clone)]
pub struct RewardState {
    pub prev_equity: f64,
    pub peak_equity: f64,
    pub initial_equity: f64,
    pub pending_mtm: Vec<PendingMtm>,
    pub last_thesis_penalty: f64,
}

impl RewardState {
    pub fn new(initial_equity: f64) -> Self {
        Self {
            prev_equity: initial_equity,
            peak_equity: initial_equity,
            initial_equity,
            pending_mtm: Vec::new(),
            last_thesis_penalty: 0.0,
        }
    }
}

/// vNext simplified reward config — 4 economic terms only.
/// All shaping bonuses/penalties removed. Failure modes are handled
/// by hard gates in the action dispatch layer (rl.rs), not here.
pub struct RewardConfig {
    /// Weight for fee cost amplification
    pub fee_cost_weight: f64,
    /// Weight for adverse selection penalty (deferred MTM, penalty-only)
    pub as_penalty_weight: f64,
    /// Horizon in ms for adverse selection evaluation
    pub as_horizon_ms: u32,
    /// Weight for quadratic inventory risk
    pub inventory_risk_weight: f64,
    /// Weight for rewarding realized profit via REDUCE/CLOSE actions (surgical intervention)
    pub realized_pnl_bonus_weight: f64,
    /// Penalty for choosing an action that is illegal for the current state (e.g. ADD while FLAT)
    pub invalid_action_penalty: f64,
    /// Weight for thesis decay penalty (microprice drift against position)
    pub thesis_decay_weight: f64,
    /// Weight for trailing MFE penalty (giving back gains)
    pub trailing_mfe_penalty_weight: f64,
    /// Flag to use the consolidated strict economic variant (A/B testing)
    pub reward_consolidated_variant: bool,
    /// Weight for voluntary taker exit penalty
    pub exit_taker_penalty_weight: f64,
    /// Weight for maker exit bonus
    pub exit_maker_bonus_weight: f64,

    // ── Legacy fields (kept at 0.0 for backward compat, not used by vNext) ──
    pub overtrading_penalty: f64,
    pub exposure_penalty: f64,
    pub toxic_fill_penalty: f64,
    pub tib_bonus: f64,
    pub maker_fill_bonus: f64,
    pub taker_fill_penalty: f64,
    pub idle_posting_penalty: f64,
    pub mtm_penalty_window_ms: u32,
    pub mtm_penalty_multiplier: f64,
    pub reprice_penalty_bps: f64,
    pub reward_distance_to_mid_penalty: f64,
    pub reward_skew_penalty_weight: f64,
    pub reward_adverse_selection_bonus_multiplier: f64,
    pub reward_realized_pnl_multiplier: f64,
    pub reward_cancel_all_penalty: f64,
    pub reward_inventory_change_penalty: f64,
    pub reward_two_sided_bonus: f64,
    pub reward_taker_action_penalty: f64,
    pub reward_quote_presence_bonus: f64,
}

impl Default for RewardConfig {
    fn default() -> Self {
        Self {
            // vNext active params
            fee_cost_weight: 0.1,
            as_penalty_weight: 0.5,
            as_horizon_ms: 3000,
            inventory_risk_weight: 0.0005,
            realized_pnl_bonus_weight: 2.0,
            invalid_action_penalty: 0.1, // Fixed -0.1 penalty per bad action
            thesis_decay_weight: 0.0,    // Default to off
            trailing_mfe_penalty_weight: 0.0,
            reward_consolidated_variant: false, // Default to legacy/A variant
            exit_taker_penalty_weight: 0.0,
            exit_maker_bonus_weight: 0.0,

            // Legacy — all zeroed
            overtrading_penalty: 0.0,
            exposure_penalty: 0.0,
            toxic_fill_penalty: 0.0,
            tib_bonus: 0.0,
            maker_fill_bonus: 0.0,
            taker_fill_penalty: 0.0,
            idle_posting_penalty: 0.0,
            mtm_penalty_window_ms: 0,
            mtm_penalty_multiplier: 0.0,
            reprice_penalty_bps: 0.0,
            reward_distance_to_mid_penalty: 0.0,
            reward_skew_penalty_weight: 0.0,
            reward_adverse_selection_bonus_multiplier: 0.0,
            reward_realized_pnl_multiplier: 0.0,
            reward_cancel_all_penalty: 0.0,
            reward_inventory_change_penalty: 0.0,
            reward_two_sided_bonus: 0.0,
            reward_taker_action_penalty: 0.0,
            reward_quote_presence_bonus: 0.0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct MakerFillDetail {
    pub side: f64, // 1.0 for Buy, -1.0 for Sell
}

pub struct RewardCalculator;

impl RewardCalculator {
    /// vNext: Simplified 4-term reward.
    ///
    /// R(t) = Δ_log_equity
    ///      - fee_cost_weight × fee_cost_bps
    ///      - as_penalty   (deferred MTM, penalty-only, no favorable bonus)
    ///      - inventory_risk_weight × skew²
    pub fn compute_reward(
        state: &mut RewardState,
        current_equity: f64,
        current_mid: f64,
        elapsed_ms: u32,
        fees_this_step: f64,
        exposure: f64,
        maker_fills: &[MakerFillDetail],
        active_order_count: u32,
        realized_pnl_step: f64,
        is_taker_action: bool,
        is_invalid_action: bool,
        micro_minus_mid_bps: f64,
        trade_imbalance_5s: f64,
        max_trade_upnl_bps: f64,
        current_upnl_bps: f64,
        num_exit_maker_fills: u32,
        num_voluntary_exit_taker_fills: u32,
        config: &RewardConfig,
    ) -> f64 {
        // Validation
        if !current_equity.is_finite() || !state.prev_equity.is_finite()
            || current_equity <= 0.0 || state.prev_equity <= 0.0
        {
            return -1.0;
        }

        // ── Term 1: Log-Return ──
        let log_return = (current_equity / state.prev_equity).ln();

        // ── Term 2: Fee Cost Signal ──
        let fee_cost_bps = if current_equity > 0.0 && fees_this_step.is_finite() {
            fees_this_step / current_equity * 10000.0
        } else {
            0.0
        };
        let fee_penalty = config.fee_cost_weight * fee_cost_bps;

        // ── Term 3: Adverse Selection (Deferred MTM, penalty-only) ──
        let mut as_signal = 0.0;

        // Register new fills for deferred evaluation
        if config.as_horizon_ms > 0 && !maker_fills.is_empty() {
            for fill in maker_fills {
                state.pending_mtm.push(PendingMtm {
                    initial_mid: current_mid,
                    side: fill.side,
                    remaining_ms: config.as_horizon_ms as i64,
                });
            }
        }

        // Evaluate expired entries
        let mut i = 0;
        while i < state.pending_mtm.len() {
            state.pending_mtm[i].remaining_ms -= elapsed_ms as i64;
            if state.pending_mtm[i].remaining_ms <= 0 {
                let mtm = state.pending_mtm.remove(i);
                let price_delta = (current_mid - mtm.initial_mid) * mtm.side;
                if mtm.initial_mid > 0.0 {
                    let move_bps = price_delta / mtm.initial_mid;
                    if move_bps < 0.0 {
                        // Penalty for adverse selection — NO favorable bonus
                        as_signal -= config.as_penalty_weight * move_bps.abs();
                    }
                    // Favorable moves: NO bonus. Already captured in equity return.
                }
            } else {
                i += 1;
            }
        }

        // ── Term 4: Inventory Risk (Quadratic) ──
        let skew = if current_equity > 0.0 { exposure / current_equity } else { 0.0 };
        let inventory_penalty = config.inventory_risk_weight * skew * skew;

        // ── Term 5: Exploration Bonus (Micro-Proxy) ──
        let exploration_bonus = if config.reward_quote_presence_bonus > 0.0 {
            config.reward_quote_presence_bonus * (active_order_count.min(2) as f64)
        } else {
            0.0
        };
        
        // ── Term 6: Realized PnL Bonus (Surgical Intervention) ──
        // Only rewards the agent if it CHOSE to exit (taker) and it was profitable.
        let realized_bonus = if is_taker_action && realized_pnl_step > 0.0 {
            config.realized_pnl_bonus_weight * (realized_pnl_step / current_equity)
        } else {
            0.0
        };

        let invalid_penalty = if is_invalid_action { config.invalid_action_penalty } else { 0.0 };

        // ── Term 8: Thesis Decay Penalty (Soft Shaping) ──
        // Penalize the agent if microprice indicates pressure AGAINST our position.
        // micro_minus_mid_bps is (Micro - Mid) / Mid * 10000.
        // If LONG (side=1) and micro < mid (negative diff), we are in "decay".
        let side = if exposure > 0.0 { 1.0 } else if exposure < 0.0 { -1.0 } else { 0.0 };
        let thesis_decay = if side != 0.0 {
            let decay_bps = (side * micro_minus_mid_bps).min(0.0).abs();
            decay_bps * (1.0 + trade_imbalance_5s.abs())
        } else {
            0.0
        };
        let thesis_penalty = config.thesis_decay_weight * thesis_decay;
        state.last_thesis_penalty = thesis_penalty;

        // ── Term 9: Trailing MFE Penalty ──
        // Penalize the agent for giving back profit. 
        // If max_trade_upnl_bps = 10 and current_upnl_bps = 5, we have 5bps of decay.
        let mfe_decay = (max_trade_upnl_bps - current_upnl_bps).max(0.0);
        let mfe_penalty = config.trailing_mfe_penalty_weight * mfe_decay;
        
        // ── Term 10: Exit Shaping (Consolidated) ──
        // Penalize voluntary taker exits and reward maker exits.
        // These are unit-less weights applied per fill event.
        let exit_shaping = (config.exit_maker_bonus_weight * num_exit_maker_fills as f64)
                         - (config.exit_taker_penalty_weight * num_voluntary_exit_taker_fills as f64);

        // ── Update state ──
        if current_equity > state.peak_equity {
            state.peak_equity = current_equity;
        }
        state.prev_equity = current_equity;

        // ── Combine: 7 terms (4 economic + 1 exploration + 1 surgical exit + 1 invalid penalty) ──
        let mut reward = log_return
            - fee_penalty
            + as_signal      // as_signal is already negative for adverse
            - inventory_penalty
            + exploration_bonus
            + realized_bonus
            - invalid_penalty
            - thesis_penalty
            - mfe_penalty
            + exit_shaping;

        if config.reward_consolidated_variant {
            reward = log_return
                + as_signal
                - inventory_penalty
                - thesis_penalty
                - invalid_penalty;
        }

        if !reward.is_finite() {
            return -1.0;
        }

        reward
    }

    /// Legacy compute_reward for backward compatibility.
    /// Delegates to the old 18-term formula. Used if vNext params are all zero.
    #[allow(clippy::too_many_arguments)]
    pub fn compute_reward_legacy(
        state: &mut RewardState,
        current_equity: f64,
        current_mid: f64,
        elapsed_ms: u32,
        num_trades: u32,
        num_toxic_fills: u32,
        exposure: f64,
        tib_count: u32,
        maker_fills: &[MakerFillDetail],
        num_taker_fills: u32,
        active_order_count: u32,
        num_reprices: u32,
        distance_to_mid_bps: f64,
        realized_pnl: f64,
        is_cancel_all: bool,
        is_two_sided: bool,
        is_taker_action: bool,
        prev_exposure: f64,
        micro_minus_mid_bps: f64,
        trade_imbalance_5s: f64,
        config: &RewardConfig,
    ) -> f64 {
        // Validation
        if !current_equity.is_finite() || !state.prev_equity.is_finite()
            || current_equity <= 0.0 || state.prev_equity <= 0.0
        {
            return -1.0;
        }

        let log_return = (current_equity / state.prev_equity).ln();
        let trade_penalty = config.overtrading_penalty * (num_trades as f64);
        let toxic_penalty = config.toxic_fill_penalty * (num_toxic_fills as f64);
        let effective_leverage = exposure.abs() / current_equity;
        let exposure_penalty = config.exposure_penalty * effective_leverage;
        let tib_reward = config.tib_bonus * (tib_count as f64);
        let maker_reward = config.maker_fill_bonus * (maker_fills.len() as f64);
        let taker_penalty = config.taker_fill_penalty * (num_taker_fills as f64);
        let idle_penalty = if maker_fills.is_empty() {
            config.idle_posting_penalty * (active_order_count as f64)
        } else {
            0.0
        };

        // MtM legacy
        let mut mtm_signal = 0.0;
        if config.mtm_penalty_window_ms > 0 && !maker_fills.is_empty() {
            for fill in maker_fills {
                state.pending_mtm.push(PendingMtm {
                    initial_mid: current_mid,
                    side: fill.side,
                    remaining_ms: config.mtm_penalty_window_ms as i64,
                });
            }
        }
        let mut i = 0;
        while i < state.pending_mtm.len() {
            state.pending_mtm[i].remaining_ms -= elapsed_ms as i64;
            if state.pending_mtm[i].remaining_ms <= 0 {
                let mtm = state.pending_mtm.remove(i);
                let price_delta = (current_mid - mtm.initial_mid) * mtm.side;
                if mtm.initial_mid > 0.0 {
                    let move_bps = price_delta / mtm.initial_mid;
                    if move_bps < 0.0 {
                        mtm_signal -= config.mtm_penalty_multiplier * move_bps.abs();
                    } else {
                        mtm_signal += config.reward_adverse_selection_bonus_multiplier * move_bps;
                    }
                }
            } else {
                i += 1;
            }
        }

        let reprice_penalty = config.reprice_penalty_bps * (num_reprices as f64);
        let cancel_penalty = if is_cancel_all { config.reward_cancel_all_penalty } else { 0.0 };
        let distance_penalty = config.reward_distance_to_mid_penalty * distance_to_mid_bps;
        let rpnl_reward = realized_pnl * config.reward_realized_pnl_multiplier;
        let skew = exposure / current_equity;
        let skew_penalty = config.reward_skew_penalty_weight * skew * skew.abs();
        let inventory_change_penalty = config.reward_inventory_change_penalty * (exposure - prev_exposure).abs();
        let two_sided_bonus = if is_two_sided { config.reward_two_sided_bonus } else { 0.0 };
        let take_action_penalty = if is_taker_action { config.reward_taker_action_penalty } else { 0.0 };
        let quote_presence_bonus = if active_order_count > 0 && distance_to_mid_bps < 15.0 && !is_taker_action && !is_cancel_all {
            config.reward_quote_presence_bonus * (active_order_count as f64)
        } else {
            0.0
        };

        if current_equity > state.peak_equity { state.peak_equity = current_equity; }
        state.prev_equity = current_equity;

        let reward = log_return
            - trade_penalty
            - toxic_penalty
            - exposure_penalty
            + tib_reward
            + maker_reward
            - taker_penalty
            - idle_penalty
            + mtm_signal
            - reprice_penalty
            - cancel_penalty
            - distance_penalty
            + rpnl_reward
            - skew_penalty
            - inventory_change_penalty
            + two_sided_bonus
            - take_action_penalty
            + quote_presence_bonus;

        // --- Term 19: Thesis Decay (Integrated into Legacy) ---
        let side = if exposure > 0.0 { 1.0 } else if exposure < 0.0 { -1.0 } else { 0.0 };
        let thesis_decay = if side != 0.0 {
            let decay_bps = (side * micro_minus_mid_bps).min(0.0).abs();
            decay_bps * (1.0 + trade_imbalance_5s.abs())
        } else {
            0.0
        };
        let thesis_penalty = config.thesis_decay_weight * thesis_decay;
        state.last_thesis_penalty = thesis_penalty;
        
        let reward = reward - thesis_penalty;

        if !reward.is_finite() {
            return -1.0;
        }

        reward
    }
}
