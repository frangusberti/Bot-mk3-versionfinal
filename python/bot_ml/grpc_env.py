"""
GrpcTradingEnv — Gymnasium wrapper over gRPC RLService.

Translates the Rust Gym-like environment (Reset/Step) into standard
gymnasium.Env so it can be consumed by Stable-Baselines3 PPO.
"""
import gymnasium as gym
import numpy as np
import grpc
import sys, os

# Add parent dir so we can import bot_pb2
sys.path.insert(0, os.path.dirname(__file__))
import bot_pb2
import bot_pb2_grpc


class GrpcTradingEnv(gym.Env):
    """Gymnasium environment that bridges to the Rust RLService via gRPC."""

    metadata = {"render_modes": []}

    def __init__(
        self,
        server_addr: str = "localhost:50051",
        dataset_id: str = "synthetic_test",
        symbol: str = "BTCUSDT",
        seed: int = 42,
        initial_equity: float = 10000.0,
        max_leverage: float = 5.0,
        max_pos_frac: float = 0.20,
        decision_interval_ms: int = 1000,
        maker_fee: float = 2.0,
        taker_fee: float = 5.0,
        slip_bps: float = 1.0,
        hard_disaster_dd: float = 0.06,
        max_daily_dd: float = 0.03,
        feature_profile: str = "Rich",
        fill_model: int = 0, # 0=Conservative, 1=SemiOptimistic, 2=Optimistic
        reward_tib_bonus_bps: float = 0.0,
        reward_maker_fill_bonus: float = 0.0,
        reward_taker_fill_penalty: float = 0.0,
        reward_toxic_fill_penalty: float = 0.0,
        reward_idle_posting_penalty: float = 0.0,
        reward_mtm_penalty_window_ms: int = 0,
        reward_mtm_penalty_multiplier: float = 0.0,
        reward_reprice_penalty_bps: float = 0.0,
        reward_distance_to_mid_penalty: float = 0.0,
        reward_skew_penalty_weight: float = 0.0,
        reward_adverse_selection_bonus_multiplier: float = 0.0,
        reward_realized_pnl_multiplier: float = 0.0,
        reward_cancel_all_penalty: float = 0.0,
        reward_inventory_change_penalty: float = 0.0,
        reward_two_sided_bonus: float = 0.0,
        reward_taker_action_penalty: float = 0.0,
        reward_quote_presence_bonus: float = 0.0,
        post_delta_threshold_bps: float = 0.0,
        random_start_offset: bool = False,
        min_episode_events: int = 500,
        override_action_dim: int = 10,
        profit_floor_bps: float = 5.0,
        stop_loss_bps: float = 30.0,
        # vNext: Hard gate configs
        close_position_loss_threshold: float = 0.0,
        min_post_offset_bps: float = 0.0,
        imbalance_block_threshold: float = 0.0,
        # vNext: Simplified reward configs
        reward_fee_cost_weight: float = 0.0,
        reward_as_penalty_weight: float = 0.0,
        reward_inventory_risk_weight: float = 0.0,
        reward_as_horizon_ms: int = 0,
        use_selective_entry: bool = False,
        entry_veto_threshold_bps: float = 1.0,
        reward_thesis_decay_weight: float = 0.0,
        reward_trailing_mfe_penalty_weight: float = 0.0,
        use_winner_unlock: bool = False,
        reward_consolidated_variant: bool = False,
        micro_strict: bool = True,
        use_selective_entry_long_v2: bool = False,
        long_veto_imbalance_threshold: float = 0.0,
        long_veto_bb_pos_5m_threshold: float = 0.0,
        long_veto_regime_dead_threshold: float = 0.0,
        reward_exit_taker_penalty_weight: float = 0.0,
        reward_exit_maker_bonus_weight: float = 0.0,
        use_selective_entry_short_v1: bool = False,
        short_veto_imbalance_threshold: float = 0.20,
        short_veto_bb_pos_5m_threshold: float = 0.65,
        short_veto_regime_dead_threshold: float = 0.60,
        **kwargs
    ):
        super().__init__()
        self.micro_strict = micro_strict

        self.server_addr = server_addr
        self.dataset_id = dataset_id
        self.symbol = symbol
        self.seed_val = seed

        # gRPC channel
        self.channel = grpc.insecure_channel(server_addr)
        self.stub = bot_pb2_grpc.RLServiceStub(self.channel)

        # Get env info from server
        try:
            info_resp = self.stub.GetEnvInfo(bot_pb2.EnvInfoRequest())
            obs_dim = info_resp.obs_dim
            action_dim = info_resp.action_dim
            self.feature_signature = info_resp.feature_signature
            self.feature_profile = info_resp.feature_profile
        except grpc.RpcError:
            obs_dim = 166  # FeatureRow::OBS_DIM (Schema v7)
            action_dim = 10
            self.feature_signature = "unknown"
            self.feature_profile = "unknown"

        if override_action_dim is not None:
            action_dim = override_action_dim

        self.observation_space = gym.spaces.Box(
            low=-np.inf, high=np.inf, shape=(obs_dim,), dtype=np.float32
        )
        self.action_space = gym.spaces.Discrete(action_dim)

        # RLConfig
        self.rl_config = bot_pb2.RLConfig(
            decision_interval_ms=decision_interval_ms,
            initial_equity=initial_equity,
            max_leverage=max_leverage,
            max_pos_frac=max_pos_frac,
            maker_fee=maker_fee,
            taker_fee=taker_fee,
            slip_bps=slip_bps,
            hard_disaster_drawdown=hard_disaster_dd,
            max_daily_drawdown=max_daily_dd,
            feature_profile=feature_profile,
            fill_model=fill_model,
            reward_tib_bonus_bps=reward_tib_bonus_bps,
            reward_maker_fill_bonus=reward_maker_fill_bonus,
            reward_taker_fill_penalty=reward_taker_fill_penalty,
            reward_toxic_fill_penalty=reward_toxic_fill_penalty,
            reward_idle_posting_penalty=reward_idle_posting_penalty,
            reward_mtm_penalty_window_ms=reward_mtm_penalty_window_ms,
            reward_mtm_penalty_multiplier=reward_mtm_penalty_multiplier,
            reward_reprice_penalty_bps=reward_reprice_penalty_bps,
            reward_distance_to_mid_penalty=reward_distance_to_mid_penalty,
            reward_skew_penalty_weight=reward_skew_penalty_weight,
            reward_adverse_selection_bonus_multiplier=reward_adverse_selection_bonus_multiplier,
            reward_realized_pnl_multiplier=reward_realized_pnl_multiplier,
            reward_cancel_all_penalty=reward_cancel_all_penalty,
            reward_inventory_change_penalty=reward_inventory_change_penalty,
            reward_two_sided_bonus=reward_two_sided_bonus,
            reward_taker_action_penalty=reward_taker_action_penalty,
            reward_quote_presence_bonus=reward_quote_presence_bonus,
            post_delta_threshold_bps=post_delta_threshold_bps,
            random_start_offset=random_start_offset,
            min_episode_events=min_episode_events,
            # vNext
            close_position_loss_threshold=close_position_loss_threshold,
            min_post_offset_bps=min_post_offset_bps,
            imbalance_block_threshold=imbalance_block_threshold,
            reward_inventory_risk_weight=reward_inventory_risk_weight,
            reward_as_horizon_ms=reward_as_horizon_ms,
            use_selective_entry=use_selective_entry,
            entry_veto_threshold_bps=entry_veto_threshold_bps,
            profit_floor_bps=profit_floor_bps,
            stop_loss_bps=stop_loss_bps,
            reward_thesis_decay_weight=reward_thesis_decay_weight,
            reward_trailing_mfe_penalty_weight=reward_trailing_mfe_penalty_weight,
            use_winner_unlock=use_winner_unlock,
            reward_consolidated_variant=reward_consolidated_variant,
            micro_strict=kwargs.get("micro_strict", True),
            use_selective_entry_long_v2=use_selective_entry_long_v2,
            long_veto_imbalance_threshold=long_veto_imbalance_threshold,
            long_veto_bb_pos_5m_threshold=long_veto_bb_pos_5m_threshold,
            long_veto_regime_dead_threshold=long_veto_regime_dead_threshold,
            reward_exit_taker_penalty_weight=reward_exit_taker_penalty_weight,
            reward_exit_maker_bonus_weight=reward_exit_maker_bonus_weight,
            use_selective_entry_short_v1=use_selective_entry_short_v1,
            short_veto_imbalance_threshold=short_veto_imbalance_threshold,
            short_veto_bb_pos_5m_threshold=short_veto_bb_pos_5m_threshold,
            short_veto_regime_dead_threshold=short_veto_regime_dead_threshold,
            use_exit_curriculum_d1=kwargs.get("use_exit_curriculum_d1", False),
            maker_first_exit_timeout_ms=kwargs.get("maker_first_exit_timeout_ms", 30000),
            exit_fallback_loss_bps=kwargs.get("exit_fallback_loss_bps", 10.0),
            exit_fallback_mfe_giveback_bps=kwargs.get("exit_fallback_mfe_giveback_bps", 5.0),
            exit_fallback_thesis_decay_threshold=kwargs.get("exit_fallback_thesis_decay_threshold", 0.45),
            exit_maker_pricing_multiplier=kwargs.get("exit_maker_pricing_multiplier", 1.0),
        )

        self.entry_veto_threshold_bps = kwargs.get("entry_veto_threshold_bps", 0.2)
        self.reward_realized_pnl_bonus_weight = kwargs.get("reward_realized_pnl_bonus_weight", 0.0)
        self.reward_thesis_decay_weight = kwargs.get("reward_thesis_decay_weight", 0.0001)
        self.micro_strict = kwargs.get("micro_strict", False)
        self.reward_consolidated_variant = kwargs.get("reward_consolidated_variant", False)

        self.episode_id = None
        # Action masking: flat state default (HOLD + OPEN_LONG + OPEN_SHORT valid)
        self._action_mask = np.array([1,1,0,0,0,1,0,0,0,0], dtype=np.float32)

    def reset(self, *, seed=None, options=None):
        """Reset the environment and return initial observation."""
        if seed is not None:
            self.seed_val = seed
            
        print(f"[DEBUG_ENV] reset() called: fill_model={self.rl_config.fill_model}, bonus={self.rl_config.reward_maker_fill_bonus}")

        req = bot_pb2.ResetRequest(
            dataset_id=self.dataset_id,
            symbol=self.symbol,
            seed=self.seed_val,
            config=self.rl_config,
        )

        resp = self.stub.ResetEpisode(req)
        self.episode_id = resp.episode_id

        obs = np.array(resp.obs.vec, dtype=np.float32)
        info = {
            "episode_id": resp.episode_id,
            "equity": resp.state.equity if resp.state else 0.0,
            "ts": resp.obs.ts,
            "position_qty": resp.state.position_qty if resp.state else 0.0,
            "entry_price": resp.state.entry_price if resp.state else 0.0,
            "realized_pnl": resp.state.realized_pnl if resp.state else 0.0,
            "fees_paid": resp.state.fees_paid if resp.state else 0.0,
        }
        if resp.info:
            info["thesis_decay_penalty"] = getattr(resp.info, "thesis_decay_penalty", 0.0)
            info["is_invalid"] = getattr(resp.info, "is_invalid", False)
            info["mid_price"] = getattr(resp.info, "mid_price", 0.0)
        if resp.feature_health:
            info["feature_health"] = {
                "obs_quality": resp.feature_health.obs_quality,
                "book_age_ms": resp.feature_health.book_age_ms,
            }
        # Update action mask from reset response (or use flat-state default)
        if resp.info and len(resp.info.action_mask) == self.action_space.n:
            self._action_mask = np.array(list(resp.info.action_mask), dtype=np.float32)
        else:
            self._action_mask = np.array([1,1,0,0,0,1,0,0,0,0], dtype=np.float32)
        return obs, info

    def step(self, action: int):
        """Execute one step in the environment."""
        action_msg = bot_pb2.Action(type=action)
        req = bot_pb2.StepRequest(
            episode_id=self.episode_id,
            action=action_msg,
        )

        resp = self.stub.Step(req)

        obs = np.array(resp.obs.vec, dtype=np.float32)
        reward = resp.reward
        terminated = resp.done
        truncated = False

        info = {}
        if resp.feature_health:
            info["feature_health"] = {
                "obs_quality": resp.feature_health.obs_quality,
                "book_age_ms": resp.feature_health.book_age_ms,
            }
        if resp.info:
            info["ts"] = resp.info.ts
            info["reason"] = resp.info.reason
            info["mid_price"] = resp.info.mid_price
            info["trades_executed"] = resp.info.trades_executed
            info["maker_fills"] = resp.info.maker_fills
            info["toxic_fills"] = resp.info.toxic_fills
            info["stale_expiries"] = resp.info.stale_expiries
            info["cancel_count"] = resp.info.cancel_count
            info["active_order_count"] = resp.info.active_order_count
            # vNext gate telemetry
            info["gate_close_blocked"] = getattr(resp.info, "gate_close_blocked", 0)
            info["gate_offset_blocked"] = getattr(resp.info, "gate_offset_blocked", 0)
            info["gate_imbalance_blocked"] = getattr(resp.info, "gate_imbalance_blocked", 0)
            info["exit_blocked_1_to_4_count"] = getattr(resp.info, "exit_blocked_1_to_4_count", 0)
            info["opportunity_lost_count"] = getattr(resp.info, "opportunity_lost_count", 0)
            info["thesis_decay_penalty"] = getattr(resp.info, "thesis_decay_penalty", 0.0)
            info["is_invalid"] = getattr(resp.info, "is_invalid", False)
            info["soft_veto_count"] = getattr(resp.info, "soft_veto_count_in_step", 0)
            info["veto_long_flow_count"] = getattr(resp.info, "veto_long_flow_count", 0)
            info["veto_long_bb_count"] = getattr(resp.info, "veto_long_bb_count", 0)
            info["veto_long_dead_regime_count"] = getattr(resp.info, "veto_long_dead_regime_count", 0)
            info["hard_invalid_count"] = getattr(resp.info, "hard_invalid_count_in_step", 0)
            info["accepted_as_marketable_count"] = getattr(resp.info, "accepted_as_marketable_count", 0)
            info["accepted_as_passive_count"] = getattr(resp.info, "accepted_as_passive_count", 0)
            info["resting_fill_count"] = getattr(resp.info, "resting_fill_count", 0)
            info["exit_maker_fills"] = getattr(resp.info, "exit_maker_fills", 0)
            info["voluntary_exit_taker_fills"] = getattr(resp.info, "voluntary_exit_taker_fills", 0)
            info["action_counts"] = dict(resp.info.action_counts)
        if resp.state:
            info["position_qty"] = resp.state.position_qty
            info["entry_price"] = resp.state.entry_price
            info["unrealized_pnl"] = resp.state.unrealized_pnl
            info["realized_pnl"] = resp.state.realized_pnl
            info["fees_paid"] = resp.state.fees_paid
            info["equity"] = resp.state.equity
            info["immediate_fill_count"] = getattr(resp.info, "immediate_fill_count", 0)
            info["liquidity_flag_unknown_count"] = getattr(resp.info, "liquidity_flag_unknown_count", 0)
            
            # Action Masking & Granular Breakdown
            raw_mask = list(getattr(resp.info, "action_mask", [1.0]*10))
            info["action_mask"] = raw_mask
            self._action_mask = np.array(raw_mask, dtype=np.float32)
            info["invalid_open_marketable"] = getattr(resp.info, "invalid_open_marketable_count", 0)
            info["invalid_close_flat"] = getattr(resp.info, "invalid_close_flat_count", 0)
            info["invalid_reprice_empty"] = getattr(resp.info, "invalid_reprice_empty_count", 0)
            info["invalid_pos_side_mismatch"] = getattr(resp.info, "invalid_pos_side_mismatch_count", 0)
            info["masked_action_count"] = getattr(resp.info, "masked_action_chosen_count", 0)
            
            # Phase 4 Lifecycle Telemetry
            info["action_counts"] = dict(resp.info.action_counts) if hasattr(resp.info, "action_counts") else {}
            info["realized_pnl_total"] = getattr(resp.info, "realized_pnl_total", 0.0)
            info["avg_win_hold_ms"] = getattr(resp.info, "avg_win_hold_ms", 0.0)
            info["avg_loss_hold_ms"] = getattr(resp.info, "avg_loss_hold_ms", 0.0)
            info["exit_distribution"] = dict(resp.info.exit_distribution) if hasattr(resp.info, "exit_distribution") else {}
            
            # D1 Telemetry
            info["exit_intent_active"] = getattr(resp.info, "exit_intent_active", 0)
            info["exit_fallback_triggered"] = getattr(resp.info, "exit_fallback_triggered", 0)
            info["time_since_exit_intent_ms"] = getattr(resp.info, "time_since_exit_intent_ms", 0)
            info["exit_fallback_reason"] = getattr(resp.info, "exit_fallback_reason", 0)
            
            fills_list = []
            for f in getattr(resp.info, "fills", []):
                fills_list.append({
                    "trace_id": f.trace_id,
                    "symbol": f.symbol,
                    "side": f.side,
                    "price": f.price,
                    "qty": f.qty,
                    "fee": getattr(f, "fee", 0.0),
                    "liquidity": getattr(f, "liquidity", "unknown"),
                    "ts_event": f.ts_event,
                    "ts_recv_local": getattr(f, "ts_recv_local", 0),
                    "is_toxic": getattr(f, "is_toxic", False)
                })
            info["fills"] = fills_list
        if resp.state:
            info["equity"] = resp.state.equity
            info["position_qty"] = resp.state.position_qty
            info["position_side"] = resp.state.position_side
            info["realized_pnl"] = resp.state.realized_pnl
            info["fees_paid"] = resp.state.fees_paid

        return obs, reward, terminated, truncated, info

    def action_masks(self) -> np.ndarray:
        """Return boolean mask of valid actions for MaskablePPO."""
        return self._action_mask > 0.5

    def close(self):
        """Clean up gRPC channel and end episode on server."""
        if getattr(self, "episode_id", None) and getattr(self, "stub", None):
            try:
                self.stub.EndEpisode(bot_pb2.EndEpisodeRequest(episode_id=self.episode_id))
            except Exception:
                pass
        if self.channel:
            self.channel.close()
