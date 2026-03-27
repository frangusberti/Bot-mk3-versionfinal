from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
from enum import Enum

class DataFreq(Enum):
    FR_1M = "1m"
    FR_5M = "5m"
    FR_15M = "15m"
    FR_1H = "1h"
    FR_4H = "4h"
    FR_1D = "1d"

@dataclass
class MarketEvent:
    timestamp: int  # Unix timestamp in milliseconds
    symbol: str
    price: float
    volume: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    order_id: Optional[str] = None
    is_buyer_maker: bool = False

@dataclass
class FeatureConfig:
    enabled_features: List[str]
    window_sizes: List[int]
    use_volatility: bool = True
    use_momentum: bool = True
