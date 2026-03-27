from abc import ABC, abstractmethod

class BasePolicy(ABC):
    @abstractmethod
    def infer(self, symbol, obs, portfolio, risk, config):
        """
        Calculates action based on input.
        Returns (action_str, confidence, reason, log_prob, value)
        """
        pass
