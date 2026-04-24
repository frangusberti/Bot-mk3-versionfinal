"""
Regime-aware policy router.

Detects market regime from obs vector (schema v8) and routes each
inference to the policy trained for that regime.

Regimes:
  HIGH_VOL     — rv_30s > rv_high_vol threshold
  TRENDING_UP  — ret_30s > ret_trending threshold
  TRENDING_DOWN — ret_30s < -ret_trending threshold
  SIDEWAYS     — everything else

If no PPO model is configured for a regime, falls back to HeuristicPolicy.
"""
import logging
from policies.heuristic import HeuristicPolicy
from policies.sb3_ppo import SB3PPOPolicy

logger = logging.getLogger("policy_server")

# Schema v8 obs indices
IDX_RET_30S = 8
IDX_RV_30S  = 10
IDX_CTX_REGIME_TREND = 92
IDX_CTX_REGIME_RANGE = 93
IDX_CTX_REGIME_SHOCK = 94
IDX_CTX_REGIME_DEAD = 95
IDX_TREND_ALIGNMENT = 99

REGIMES = ["HIGH_VOL", "TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS"]


class RegimeRouter:
    def __init__(self, config: dict):
        thresholds          = config.get("regime_thresholds", {})
        self.rv_high_vol    = thresholds.get("rv_high_vol",  0.003)
        self.ret_trending   = thresholds.get("ret_trending", 0.0003)

        regime_models = config.get("regime_models", {})
        self.policies: dict = {}

        for regime in REGIMES + ["default"]:
            path = regime_models.get(regime, "")
            if path:
                logger.info(f"RegimeRouter: loading PPO for {regime} from {path}")
                self.policies[regime] = SB3PPOPolicy(path)
            else:
                logger.info(f"RegimeRouter: no model for '{regime}', using heuristic fallback")
                self.policies[regime] = HeuristicPolicy()

        self._counts: dict = {r: 0 for r in REGIMES + ["default"]}

    # ------------------------------------------------------------------
    def detect_regime(self, obs: list) -> str:
        if len(obs) <= IDX_RV_30S:
            return "default"
        if len(obs) > IDX_CTX_REGIME_DEAD:
            ctx_trend = obs[IDX_CTX_REGIME_TREND]
            ctx_range = obs[IDX_CTX_REGIME_RANGE]
            ctx_shock = obs[IDX_CTX_REGIME_SHOCK]
            ctx_dead = obs[IDX_CTX_REGIME_DEAD]
            alignment = obs[IDX_TREND_ALIGNMENT] if len(obs) > IDX_TREND_ALIGNMENT else 0.0
            ctx_scores = [ctx_trend, ctx_range, ctx_shock, ctx_dead]
            winner = max(ctx_scores)
            runner_up = sorted(ctx_scores, reverse=True)[1]
            if winner - runner_up < 0.05:
                return "SIDEWAYS"

            if ctx_shock > max(ctx_trend, ctx_range, ctx_dead):
                return "HIGH_VOL"
            if ctx_trend > max(ctx_range, ctx_shock, ctx_dead) and alignment > 0.08:
                return "TRENDING_UP"
            if ctx_trend > max(ctx_range, ctx_shock, ctx_dead) and alignment < -0.08:
                return "TRENDING_DOWN"
            return "SIDEWAYS"
        rv_30s  = obs[IDX_RV_30S]
        ret_30s = obs[IDX_RET_30S] if len(obs) > IDX_RET_30S else 0.0

        if rv_30s > self.rv_high_vol:
            return "HIGH_VOL"
        if ret_30s > self.ret_trending:
            return "TRENDING_UP"
        if ret_30s < -self.ret_trending:
            return "TRENDING_DOWN"
        return "SIDEWAYS"

    # ------------------------------------------------------------------
    def infer(self, symbol, obs, portfolio, risk, config):
        regime = self.detect_regime(obs)
        policy = self.policies.get(regime) or self.policies.get("default") or HeuristicPolicy()
        self._counts[regime] = self._counts.get(regime, 0) + 1

        action, conf, reason, log_prob, value = policy.infer(
            symbol, obs, portfolio, risk, config
        )
        return action, conf, f"{regime}:{reason}", log_prob, value

    # ------------------------------------------------------------------
    def get_regime_stats(self) -> dict:
        return dict(self._counts)

    def reload_model(self, regime: str, path: str):
        """Hot-swap the model for a specific regime without restarting."""
        if regime not in REGIMES + ["default"]:
            raise ValueError(f"Unknown regime: {regime}")
        if path:
            self.policies[regime] = SB3PPOPolicy(path)
            logger.info(f"RegimeRouter: reloaded {regime} from {path}")
        else:
            self.policies[regime] = HeuristicPolicy()
            logger.info(f"RegimeRouter: {regime} reset to heuristic")
