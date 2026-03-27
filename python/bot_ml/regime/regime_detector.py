import numpy as np
import logging
from enum import Enum
from typing import List, Dict, Union

logger = logging.getLogger(__name__)

class Regime(Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOL = "HIGH_VOL"
    LOW_VOL = "LOW_VOL"
    UNKNOWN = "UNKNOWN"

class RegimeDetector:
    """
    A lightweight market regime classifier for BOTMK3.
    Uses moving average convergence/divergence for trend and rolling volatility for volatility regimes.
    """
    def __init__(
        self, 
        trend_threshold: float = 0.0005, 
        vol_threshold: float = 0.002, 
        window_fast: int = 20, 
        window_slow: int = 100
    ):
        self.trend_threshold = trend_threshold
        self.vol_threshold = vol_threshold
        self.window_fast = window_fast
        self.window_slow = window_slow

    def detect(self, prices: Union[List[float], np.ndarray]) -> Dict:
        """
        Classifies the current market regime based on a price series.
        
        Returns:
            Dict: {
                "regime": Regime,
                "metrics": {
                    "volatility": float,
                    "trend_strength": float,
                    "fast_ma": float,
                    "slow_ma": float
                }
            }
        """
        if len(prices) < self.window_slow:
            return {
                "regime": Regime.UNKNOWN,
                "metrics": {}
            }
        
        p = np.array(prices)
        fast_prices = p[-self.window_fast:]
        slow_prices = p[-self.window_slow:]
        
        ma_fast = np.mean(fast_prices)
        ma_slow = np.mean(slow_prices)
        
        # Volatility: Coefficient of Variation (std / mean)
        # Using fast window for reactivity
        vol = np.std(fast_prices) / ma_fast
        
        # Trend: Percent difference between fast and slow MA
        trend_strength = (ma_fast - ma_slow) / ma_slow
        
        metrics = {
            "volatility": float(vol),
            "trend_strength": float(trend_strength),
            "fast_ma": float(ma_fast),
            "slow_ma": float(ma_slow)
        }
        
        # Classification Logic
        # Priority 1: High Volatility
        if vol > self.vol_threshold:
            regime = Regime.HIGH_VOL
        # Priority 2: Strong Trend
        elif trend_strength > self.trend_threshold:
            regime = Regime.TREND_UP
        elif trend_strength < -self.trend_threshold:
            regime = Regime.TREND_DOWN
        # Priority 3: Low Vol/Sideways
        elif vol < self.vol_threshold * 0.3:
            regime = Regime.LOW_VOL
        else:
            regime = Regime.SIDEWAYS
            
        return {
            "regime": regime,
            "metrics": metrics
        }

if __name__ == "__main__":
    # Smoke test
    detector = RegimeDetector()
    
    # 1. Sideways / Low Vol
    base_price = 100.0
    prices = [base_price + np.random.normal(0, 0.01) for _ in range(200)]
    result = detector.detect(prices)
    print(f"Sideways Test: {result['regime']} | Vol: {result['metrics'].get('volatility', 0):.6f}")
    
    # 2. Up Trend
    prices = [base_price + i*0.02 + np.random.normal(0, 0.01) for i in range(200)]
    result = detector.detect(prices)
    print(f"Trend Up Test: {result['regime']} | Trend: {result['metrics'].get('trend_strength', 0):.6f}")
    
    # 3. High Vol
    prices = [base_price + np.random.normal(0, 1.0) for _ in range(200)]
    result = detector.detect(prices)
    print(f"High Vol Test: {result['regime']} | Vol: {result['metrics'].get('volatility', 0):.6f}")
