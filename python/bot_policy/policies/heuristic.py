import time
from .base import BasePolicy

# Feature Index Constants: must match FeatureRow::to_obs_vec()
# See: crates/bot-data/src/features_v2/schema.rs (schema_version 7)
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

OBS_DIM = 166


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
        
        # ── Extract Features ──
        ret_1s     = obs[IDX_RET_1S]
        ret_5s     = obs[IDX_RET_5S]
        ret_10s    = obs[IDX_RET_10S]
        ret_30s    = obs[IDX_RET_30S]
        rv_30s     = obs[IDX_RV_30S]
        spread_bps = obs[IDX_SPREAD_BPS]
        pos_flag   = obs[IDX_POSITION_FLAG]   # -1, 0, 1 when available
        obi_top1   = obs[IDX_OBI_TOP1]
        
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
        
        print(f"Features: ret_5s={ret_5s:.6f} ret_30s={ret_30s:.6f} rv_30s={rv_30s:.6f} spread_bps={spread_bps:.2f} pos={pos_flag:.0f} obi={obi_top1:.3f}")
        
        now_ms = int(time.time() * 1000)
        cooldown_ms = config.get("cooldown_ms", 60000)
        min_hold_ms = config.get("min_hold_ms", 300000)
        exit_confirm_ms = config.get("exit_confirm_ms", 60000)
        base_thr = config.get("threshold", 0.00025)
        entry_thr = max(base_thr, rv_30s * 0.35)
        exit_thr = max(base_thr * 0.75, rv_30s * 0.20)
        min_profit_equity_frac = config.get("min_profit_equity_frac", 0.00075)
        stop_loss_equity_frac = config.get("stop_loss_equity_frac", 0.00120)
        upnl_frac = portfolio.get("upnl_frac", 0.0)

        if is_flat:
            self.position_opened_at.pop(symbol, None)
            self.exit_signal_since.pop(symbol, None)
        elif symbol not in self.position_opened_at:
            # Existing positions after a policy restart are treated as freshly observed
            # so the patient exit rules do not flatten them immediately.
            self.position_opened_at[symbol] = now_ms

        hold_age_ms = now_ms - self.position_opened_at.get(symbol, now_ms)

        # 1. Safety Filters: high spread or extreme volatility
        if spread_bps > 15.0 or rv_30s > 0.004:
            if is_long:
                return "CLOSE_LONG", 0.9, "emergency_high_risk_long", 0.0, 0.0
            if is_short:
                return "CLOSE_SHORT", 0.9, "emergency_high_risk_short", 0.0, 0.0
            return "HOLD", 1.0, "high_risk_flat", 0.0, 0.0

        # 2. Cooldown check
        last_ts = self.cooldowns.get(symbol, 0)
        if (now_ms - last_ts) < cooldown_ms:
            return "HOLD", 0.9, "cooldown_active", 0.0, 0.0

        # 3. Decision Logic (patient trend-following with microstructure confirmation)
        action = "HOLD"
        reason = "no_signal"
        
        if is_flat:
            long_setup = ret_30s > entry_thr and ret_10s > 0 and ret_5s > 0 and obi_top1 > -0.05
            short_setup = ret_30s < -entry_thr and ret_10s < 0 and ret_5s < 0 and obi_top1 < 0.05
            if long_setup:
                action = "OPEN_LONG"
                reason = "patient_trend_up"
                self.position_opened_at[symbol] = now_ms
                self.exit_signal_since.pop(symbol, None)
            elif short_setup:
                action = "OPEN_SHORT"
                reason = "patient_trend_down"
                self.position_opened_at[symbol] = now_ms
                self.exit_signal_since.pop(symbol, None)
        elif is_long:
            stop_hit = upnl_frac <= -stop_loss_equity_frac
            reversal = ret_30s < -exit_thr and ret_10s < 0
            profit_fade = upnl_frac >= min_profit_equity_frac and ret_30s < exit_thr and ret_10s <= 0 and ret_1s <= 0
            if stop_hit:
                action = "CLOSE_LONG"
                reason = "stop_long"
            elif hold_age_ms < min_hold_ms:
                reason = "min_hold_long"
            elif reversal or profit_fade:
                first_seen = self.exit_signal_since.setdefault(symbol, now_ms)
                if now_ms - first_seen >= exit_confirm_ms:
                    action = "CLOSE_LONG"
                    reason = "confirmed_reversal_long" if reversal else "confirmed_profit_fade_long"
                else:
                    reason = "exit_confirming_long"
            else:
                self.exit_signal_since.pop(symbol, None)
        elif is_short:
            stop_hit = upnl_frac <= -stop_loss_equity_frac
            reversal = ret_30s > exit_thr and ret_10s > 0
            profit_fade = upnl_frac >= min_profit_equity_frac and ret_30s > -exit_thr and ret_10s >= 0 and ret_1s >= 0
            if stop_hit:
                action = "CLOSE_SHORT"
                reason = "stop_short"
            elif hold_age_ms < min_hold_ms:
                reason = "min_hold_short"
            elif reversal or profit_fade:
                first_seen = self.exit_signal_since.setdefault(symbol, now_ms)
                if now_ms - first_seen >= exit_confirm_ms:
                    action = "CLOSE_SHORT"
                    reason = "confirmed_reversal_short" if reversal else "confirmed_profit_fade_short"
                else:
                    reason = "exit_confirming_short"
            else:
                self.exit_signal_since.pop(symbol, None)

        confidence = 1.0
        expected_edge_bps = 0.0
        if action in ("OPEN_LONG", "OPEN_SHORT"):
            margin = (abs(ret_30s) - entry_thr) / entry_thr if entry_thr > 0 else 0
            confidence = min(0.99, max(0.55, 0.5 + (margin * 0.25)))
            book_alignment = obi_top1 if action == "OPEN_LONG" else -obi_top1
            book_bonus_bps = max(0.0, book_alignment) * 1.0
            expected_edge_bps = min(30.0, max(6.0, abs(ret_30s) * 30000.0 + book_bonus_bps))
        elif "CLOSE" in action or "REDUCE" in action:
            confidence = 0.85
            self.exit_signal_since.pop(symbol, None)

        if action != "HOLD":
            self.cooldowns[symbol] = now_ms
            
        return action, confidence, reason, 0.0, expected_edge_bps
