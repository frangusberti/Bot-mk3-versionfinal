import time
from .base import BasePolicy

# Feature Index Constants: must match FeatureRow::to_obs_vec()
# See: crates/bot-data/src/features_v2/schema.rs (schema_version 8)
IDX_MID_PRICE       = 0
IDX_SPREAD_ABS      = 1
IDX_SPREAD_BPS      = 2
IDX_SPREAD_BASELINE = 3
IDX_RET_1S          = 4
IDX_RET_3S          = 5
IDX_RET_5S          = 6
IDX_RET_10S         = 7
IDX_RET_30S         = 8
IDX_RV_5S           = 9
IDX_RV_30S          = 10
IDX_RV_5M           = 11
IDX_TAKER_BUY_1S    = 17
IDX_TAKER_SELL_1S   = 18
IDX_TAKER_BUY_5S    = 19
IDX_TAKER_SELL_5S   = 20
IDX_TAPE_TRADES_1S  = 21
IDX_TAPE_INTENSITY_Z = 22
IDX_TRADE_IMBALANCE_1S = 23
IDX_TRADE_IMBALANCE_5S = 24
IDX_TRADE_IMBALANCE_15S = 25
IDX_TAPE_INTENSITY_5S_Z = 26
IDX_OBI_TOP1        = 27
IDX_OBI_TOP3        = 28
IDX_OBI_TOP10       = 29
IDX_MICROPRICE      = 30
IDX_MICROPRICE_MID  = 31
IDX_OBI_DELTA_5S    = 32
IDX_POSITION_FLAG   = 57  # -1 (short), 0 (flat), 1 (long)
IDX_LATENT_PNL      = 58
IDX_MAX_PNL         = 59
IDX_DRAWDOWN        = 60
IDX_TIME_SIN        = 61
IDX_TIME_COS        = 62
IDX_RET_5M          = 83
IDX_RET_15M         = 84
IDX_RET_1H          = 85
IDX_RV_15M          = 86
IDX_RV_1H           = 87
IDX_SLOPE_MID_1H    = 88
IDX_RANGE_POS_5M    = 89
IDX_RANGE_POS_15M   = 90
IDX_RANGE_POS_1H    = 91
IDX_CTX_REGIME_TREND = 92
IDX_CTX_REGIME_RANGE = 93
IDX_CTX_REGIME_SHOCK = 94
IDX_CTX_REGIME_DEAD  = 95
IDX_TREND_BIAS_5M   = 96
IDX_TREND_BIAS_15M  = 97
IDX_TREND_BIAS_1H   = 98
IDX_TREND_ALIGNMENT = 99

VALUE_DIM = 100
OBS_DIM = 200


class HeuristicPolicy(BasePolicy):
    """
    Rule-based heuristic policy adapted for the 166-feature V2 obs vector.
    
    Uses:
    - ret_5s for trend detection
    - rv_30s for volatility gating
    - spread_bps for liquidity gating
    - position_flag for position state
    - obi_top1 for microstructure confirmation
    """
    
    def __init__(self):
        self.cooldowns = {}  # symbol -> last_action_ts
        self.last_actions = {}  # symbol -> last_action_type
        self.position_opened_at = {}  # symbol -> first seen/open ts
        self.exit_signal_since = {}  # symbol -> first ts for persistent exit pressure

    def infer(self, symbol, obs, portfolio, risk, config):
        print(f"--- Inference Request: {symbol} (obs_dim={len(obs)}) ---")
        
        # Validate obs dimension
        if len(obs) < OBS_DIM:
            print(f"WARNING: Expected {OBS_DIM} features, got {len(obs)}. Padding.")
            obs = list(obs) + [0.0] * (OBS_DIM - len(obs))

        def mask(idx):
            m_idx = VALUE_DIM + idx
            return obs[m_idx] if m_idx < len(obs) else 0.0

        def read(idx, default=0.0):
            return obs[idx] if idx < len(obs) and mask(idx) > 0.5 else default
        
        # ── Extract Features ──
        ret_1s     = read(IDX_RET_1S)
        ret_5s     = read(IDX_RET_5S)
        ret_10s    = read(IDX_RET_10S)
        ret_30s    = read(IDX_RET_30S)
        rv_30s     = read(IDX_RV_30S)
        spread_bps = read(IDX_SPREAD_BPS)
        pos_flag   = read(IDX_POSITION_FLAG)   # -1, 0, 1 when available
        obi_top1   = read(IDX_OBI_TOP1)
        ret_5m     = read(IDX_RET_5M)
        ret_15m    = read(IDX_RET_15M)
        ret_1h     = read(IDX_RET_1H)
        rv_15m     = read(IDX_RV_15M)
        rv_1h      = read(IDX_RV_1H)
        slope_1h   = read(IDX_SLOPE_MID_1H)
        range_pos_5m = read(IDX_RANGE_POS_5M, 0.5)
        range_pos_15m = read(IDX_RANGE_POS_15M, 0.5)
        range_pos_1h = read(IDX_RANGE_POS_1H, 0.5)
        ctx_trend  = read(IDX_CTX_REGIME_TREND, 0.25)
        ctx_range  = read(IDX_CTX_REGIME_RANGE, 0.25)
        ctx_shock  = read(IDX_CTX_REGIME_SHOCK, 0.25)
        ctx_dead   = read(IDX_CTX_REGIME_DEAD, 0.25)
        bias_5m    = read(IDX_TREND_BIAS_5M)
        bias_15m   = read(IDX_TREND_BIAS_15M)
        bias_1h    = read(IDX_TREND_BIAS_1H)
        alignment  = read(IDX_TREND_ALIGNMENT)
        
        # Prefer the backend portfolio state over the feature vector for action legality.
        is_long = portfolio.get("is_long", 0.0) > 0.5
        is_short = portfolio.get("is_short", 0.0) > 0.5
        is_flat = portfolio.get("is_flat", 1.0) > 0.5
        if is_long:
            pos_flag = 1.0
        elif is_short:
            pos_flag = -1.0
        elif is_flat:
            pos_flag = 0.0
        
        print(
            "Features: "
            f"ret_5s={ret_5s:.6f} ret_30s={ret_30s:.6f} "
            f"ret_15m={ret_15m:.6f} ret_1h={ret_1h:.6f} "
            f"ctx_trend={ctx_trend:.3f} ctx_shock={ctx_shock:.3f} "
            f"align={alignment:.3f} spread_bps={spread_bps:.2f} pos={pos_flag:.0f} obi={obi_top1:.3f}"
        )
        
        now_ms = int(time.time() * 1000)
        cooldown_ms = config.get("cooldown_ms", 60000)
        min_hold_ms = config.get("min_hold_ms", 300000)
        exit_confirm_ms = config.get("exit_confirm_ms", 60000)
        base_thr = config.get("threshold", 0.00025)
        rv_scale = config.get("rv_scale", 0.35)
        entry_thr = max(base_thr, rv_30s * rv_scale)
        exit_thr = max(base_thr * 0.75, rv_30s * (rv_scale * 0.57))
        min_profit_equity_frac = config.get("min_profit_equity_frac", 0.00075)
        stop_loss_equity_frac = config.get("stop_loss_equity_frac", 0.00120)
        upnl_frac = portfolio.get("upnl_frac", 0.0)
        higher_tf_ready = mask(IDX_TREND_BIAS_15M) > 0.5
        one_hour_ready = mask(IDX_TREND_BIAS_1H) > 0.5
        dominant_ctx = max(ctx_trend, ctx_range, ctx_shock, ctx_dead)
        trend_dominant = ctx_trend >= dominant_ctx - 1e-9
        market_untradable = ctx_shock > 0.72 or ctx_dead > 0.86
        long_context = (
            trend_dominant
            and alignment > 0.10
            and bias_15m > 0.06
            and (bias_1h > -0.02 if one_hour_ready else True)
            and ctx_shock < 0.68
            and ctx_dead < 0.82
        )
        short_context = (
            trend_dominant
            and alignment < -0.10
            and bias_15m < -0.06
            and (bias_1h < 0.02 if one_hour_ready else True)
            and ctx_shock < 0.68
            and ctx_dead < 0.82
        )
        pullback_long_ok = 0.18 <= range_pos_5m <= 0.88 and range_pos_15m <= 0.94
        pullback_short_ok = 0.12 <= (1.0 - range_pos_5m) <= 0.82 and range_pos_15m >= 0.06

        if is_flat:
            self.position_opened_at.pop(symbol, None)
            self.exit_signal_since.pop(symbol, None)
        elif symbol not in self.position_opened_at:
            # Existing positions after a policy restart are treated as freshly observed
            # so the patient exit rules do not flatten them immediately.
            self.position_opened_at[symbol] = now_ms

        hold_age_ms = now_ms - self.position_opened_at.get(symbol, now_ms)

        # 1. Safety Filters: high spread or extreme volatility
        if spread_bps > 15.0 or rv_30s > 0.004 or rv_15m > 0.006 or rv_1h > 0.010:
            if is_long:
                return "CLOSE_LONG", 0.9, "emergency_high_risk_long", 0.0, 0.0
            if is_short:
                return "CLOSE_SHORT", 0.9, "emergency_high_risk_short", 0.0, 0.0
            return "HOLD", 1.0, "high_risk_flat", 0.0, 0.0
        if is_flat and market_untradable:
            return "HOLD", 1.0, "context_untradable", 0.0, 0.0

        # 2. Cooldown check
        last_ts = self.cooldowns.get(symbol, 0)
        if (now_ms - last_ts) < cooldown_ms:
            return "HOLD", 0.9, "cooldown_active", 0.0, 0.0

        # 3. Decision Logic (patient trend-following with microstructure confirmation)
        action = "HOLD"
        reason = "no_signal"
        
        if is_flat:
            long_setup = (
                long_context
                and pullback_long_ok
                and ret_30s > (entry_thr * 0.55)
                and ret_10s > -(entry_thr * 0.10)
                and ret_5s > -(entry_thr * 0.25)
                and obi_top1 > -0.12
                and ret_5m > -0.0008
                and (slope_1h >= -0.01 if one_hour_ready else True)
            )
            short_setup = (
                short_context
                and pullback_short_ok
                and ret_30s < -(entry_thr * 0.55)
                and ret_10s < (entry_thr * 0.10)
                and ret_5s < (entry_thr * 0.25)
                and obi_top1 < 0.12
                and ret_5m < 0.0008
                and (slope_1h <= 0.01 if one_hour_ready else True)
            )
            if long_setup:
                action = "OPEN_LONG"
                reason = "mtf_context_long"
                self.position_opened_at[symbol] = now_ms
                self.exit_signal_since.pop(symbol, None)
            elif short_setup:
                action = "OPEN_SHORT"
                reason = "mtf_context_short"
                self.position_opened_at[symbol] = now_ms
                self.exit_signal_since.pop(symbol, None)
            elif higher_tf_ready and not trend_dominant:
                reason = "waiting_context"
        elif is_long:
            stop_hit = upnl_frac <= -stop_loss_equity_frac
            context_break = alignment < -0.08 or bias_15m < -0.04 or ctx_shock > 0.70
            reversal = ret_30s < -exit_thr and ret_10s < 0 and bias_5m < -0.04
            profit_fade = (
                upnl_frac >= min_profit_equity_frac
                and (ret_30s < exit_thr and ret_10s <= 0 and ret_1s <= 0)
                and (range_pos_5m > 0.84 or context_break)
            )
            if stop_hit:
                action = "CLOSE_LONG"
                reason = "stop_long"
            elif market_untradable:
                action = "CLOSE_LONG"
                reason = "context_break_long"
            elif hold_age_ms < min_hold_ms:
                reason = "min_hold_long"
            elif reversal or profit_fade or context_break:
                first_seen = self.exit_signal_since.setdefault(symbol, now_ms)
                if now_ms - first_seen >= exit_confirm_ms:
                    action = "CLOSE_LONG"
                    if context_break and not reversal and not profit_fade:
                        reason = "confirmed_context_break_long"
                    else:
                        reason = "confirmed_reversal_long" if reversal else "confirmed_profit_fade_long"
                else:
                    reason = "exit_confirming_long"
            else:
                self.exit_signal_since.pop(symbol, None)
        elif is_short:
            stop_hit = upnl_frac <= -stop_loss_equity_frac
            context_break = alignment > 0.08 or bias_15m > 0.04 or ctx_shock > 0.70
            reversal = ret_30s > exit_thr and ret_10s > 0 and bias_5m > 0.04
            profit_fade = (
                upnl_frac >= min_profit_equity_frac
                and (ret_30s > -exit_thr and ret_10s >= 0 and ret_1s >= 0)
                and (range_pos_5m < 0.16 or context_break)
            )
            if stop_hit:
                action = "CLOSE_SHORT"
                reason = "stop_short"
            elif market_untradable:
                action = "CLOSE_SHORT"
                reason = "context_break_short"
            elif hold_age_ms < min_hold_ms:
                reason = "min_hold_short"
            elif reversal or profit_fade or context_break:
                first_seen = self.exit_signal_since.setdefault(symbol, now_ms)
                if now_ms - first_seen >= exit_confirm_ms:
                    action = "CLOSE_SHORT"
                    if context_break and not reversal and not profit_fade:
                        reason = "confirmed_context_break_short"
                    else:
                        reason = "confirmed_reversal_short" if reversal else "confirmed_profit_fade_short"
                else:
                    reason = "exit_confirming_short"
            else:
                self.exit_signal_since.pop(symbol, None)

        confidence = 1.0
        expected_edge_bps = 0.0
        if action in ("OPEN_LONG", "OPEN_SHORT"):
            margin = (abs(ret_30s) - entry_thr) / entry_thr if entry_thr > 0 else 0
            context_strength = max(ctx_trend - max(ctx_range, ctx_shock, ctx_dead), 0.0)
            alignment_strength = abs(alignment)
            confidence = min(0.99, max(0.58, 0.52 + (margin * 0.18) + (context_strength * 0.18) + (alignment_strength * 0.12)))
            book_alignment = obi_top1 if action == "OPEN_LONG" else -obi_top1
            book_bonus_bps = max(0.0, book_alignment) * 1.0
            higher_tf_bonus = max(abs(bias_15m), abs(bias_1h)) * 6.0
            expected_edge_bps = min(35.0, max(6.0, abs(ret_30s) * 30000.0 + book_bonus_bps + higher_tf_bonus))
        elif "CLOSE" in action or "REDUCE" in action:
            confidence = 0.85
            self.exit_signal_since.pop(symbol, None)

        if action != "HOLD":
            self.cooldowns[symbol] = now_ms
            
        return action, confidence, reason, 0.0, expected_edge_bps
