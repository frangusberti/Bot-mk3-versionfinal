from .base import BasePolicy

class HoldPolicy(BasePolicy):
    def infer(self, symbol, obs, portfolio, risk, config):
        return "HOLD", 1.0, "always_hold", 0.0, 0.0
