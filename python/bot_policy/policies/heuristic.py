import time
from .base import BasePolicy

# ──────────────────────────────────────────────────────────────
#  Feature Index Constants — must match FeatureRow::to_obs_vec()
#  See: bot-data/src/features_v2/schema.rs
# ──────────────────────────────────────────────────────────────
IDX_MID_PRICE       = 0
IDX_SPREAD_ABS      = 1
IDX_SPREAD_BPS      = 2
IDX_RET_1S          = 3
IDX_RET_5S          = 4
IDX_RET_30S         = 5
IDX_RV_30S          = 6
IDX_RV_5M           = 7
IDX_TAKER_BUY_1S    = 8
IDX_TAKER_SELL_1S   = 9
IDX_TAKER_BUY_5S    = 10
IDX_TAKER_SELL_5S   = 11
IDX_TAPE_TRADES_1S  = 12
IDX_TAPE_INTENSITY_Z = 13
IDX_OBI_TOP1        = 14
IDX_OBI_TOP3        = 15
IDX_MICROPRICE      = 16
IDX_MICROPRICE_MID  = 17
IDX_OBI_DELTA_5S    = 18
IDX_LIQ_BUY_30S    = 19
IDX_LIQ_SELL_30S   = 20
IDX_LIQ_NET_30S    = 21
IDX_LIQ_COUNT_30S  = 22
IDX_MARK_MID_BPS   = 23
IDX_FUNDING_RATE   = 24
IDX_EMA200_DIST    = 25
IDX_RSI_14         = 26
IDX_BB_WIDTH       = 27
IDX_BB_POS         = 28
IDX_POSITION_FLAG  = 29  # -1 (short), 0 (flat), 1 (long)
IDX_LATENT_PNL     = 30
IDX_MAX_PNL        = 31
IDX_DRAWDOWN       = 32
IDX_TIME_SIN       = 33
IDX_TIME_COS       = 34

OBS_DIM = 148


class HeuristicPolicy(BasePolicy):
    """
    Rule-based heuristic policy adapted for the 74-feature V2 obs vector.
    
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

    def infer(self, symbol, obs, portfolio, risk, config):
        print(f"--- Inference Request: {symbol} (obs_dim={len(obs)}) ---")
        
        # Validate obs dimension
        if len(obs) < OBS_DIM:
            print(f"WARNING: Expected {OBS_DIM} features, got {len(obs)}. Padding.")
            obs = list(obs) + [0.0] * (OBS_DIM - len(obs))
        
        # ── Extract Features ──
        ret_1s     = obs[IDX_RET_1S]
        ret_5s     = obs[IDX_RET_5S]
        rv_30s     = obs[IDX_RV_30S]
        spread_bps = obs[IDX_SPREAD_BPS]
        pos_flag   = obs[IDX_POSITION_FLAG]   # -1, 0, 1
        obi_top1   = obs[IDX_OBI_TOP1]
        
        is_long  = pos_flag > 0.5
        is_short = pos_flag < -0.5
        is_flat  = not is_long and not is_short
        
        print(f"Features: ret_5s={ret_5s:.6f} rv_30s={rv_30s:.6f} spread_bps={spread_bps:.2f} pos={pos_flag:.0f} obi={obi_top1:.3f}")
        
        now_ms = int(time.time() * 1000)
        cooldown_ms = config.get("cooldown_ms", 2000)
        base_thr = config.get("threshold", 0.00005)
        # Dynamic threshold: minimum base_thr, but scales with realized volatility
        thr = max(base_thr, rv_30s * 0.25)

        # 1. Safety Filters: high spread or extreme volatility
        if spread_bps > 15.0 or rv_30s > 0.004:
            if is_flat:
                return "HOLD", 1.0, "high_risk_flat", 0.0, 0.0
            else:
                return "REDUCE_25", 0.8, "high_risk_reduce", 0.0, 0.0

        # 2. Cooldown check
        last_ts = self.cooldowns.get(symbol, 0)
        if (now_ms - last_ts) < cooldown_ms:
            return "HOLD", 0.9, "cooldown_active", 0.0, 0.0

        # 3. Decision Logic (trend-following with microstructure confirmation)
        action = "HOLD"
        reason = "no_signal"
        
        if is_flat:
            if ret_5s > thr and obi_top1 > -0.1:  # Trend up + book not heavily ask-biased
                action = "OPEN_LONG"
                reason = "trend_up"
            elif ret_5s < -thr and obi_top1 < 0.1:  # Trend down + book not heavily bid-biased
                action = "OPEN_SHORT"
                reason = "trend_down"
        elif is_long:
            if ret_5s < 0:  # Trend reversal
                action = "CLOSE_ALL"
                reason = "reversal_long"
            elif ret_5s < thr * 0.2 and ret_1s < 0:  # Momentum fading
                action = "CLOSE_ALL"
                reason = "profit_flat_long"
        elif is_short:
            if ret_5s > 0:  # Trend reversal
                action = "CLOSE_ALL"
                reason = "reversal_short"
            elif ret_5s > -thr * 0.2 and ret_1s > 0:  # Momentum fading
                action = "CLOSE_ALL"
                reason = "profit_flat_short"

        confidence = 1.0
        if action in ("OPEN_LONG", "OPEN_SHORT"):
            margin = (abs(ret_5s) - thr) / thr if thr > 0 else 0
            confidence = min(0.99, max(0.5, 0.5 + (margin * 0.25)))
        elif "CLOSE" in action or "REDUCE" in action:
            confidence = 0.85

        if action != "HOLD":
            self.cooldowns[symbol] = now_ms
            
        return action, confidence, reason, 0.0, 0.0
