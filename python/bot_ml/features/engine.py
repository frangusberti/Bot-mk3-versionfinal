import pandas as pd
from typing import List
from bot_ml.core import FeatureConfig
from .indicators import TechnicalIndicators
from .normalization import FeatureScaler

class FeatureEngine:
    def __init__(self, config: FeatureConfig):
        self.config = config
        self.scaler = FeatureScaler()
        self.indicators = TechnicalIndicators()

    def generate_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Generate features from raw OHLCV data.
        """
        df_features = df.copy()
        
        # Ensure numeric
        numeric_cols = ['open', 'high', 'low', 'close', 'volume']
        df_features[numeric_cols] = df_features[numeric_cols].apply(pd.to_numeric)

        # Generate enabled features
        if 'rsi' in self.config.enabled_features:
            for window in self.config.window_sizes:
                df_features[f'rsi_{window}'] = self.indicators.rsi(df_features['close'], period=window)

        if 'bbands' in self.config.enabled_features:
            for window in self.config.window_sizes:
                u, l = self.indicators.bollinger_bands(df_features['close'], period=window)
                df_features[f'bb_upper_{window}'] = u
                df_features[f'bb_lower_{window}'] = l
                # Derived feature: %B or Bandwidth could be added here
                
        if 'macd' in self.config.enabled_features:
             # MACD usually has fixed params (12, 26, 9), but we could parameterize
             m, s = self.indicators.macd(df_features['close'])
             df_features['macd'] = m
             df_features['macd_signal'] = s
             df_features['macd_hist'] = m - s

        # Drop NaNs created by rolling windows
        df_features.dropna(inplace=True)
        
        return df_features

    def scale_features(self, df: pd.DataFrame, method: str = 'minmax') -> pd.DataFrame:
        """
        Scale features using the FeatureScaler.
        """
        # Select columns to scale (exclude timestamp, maybe raw prices if needed unscaled)
        # For now, scale all numeric feature columns except timestamp
        
        cols_to_scale = [c for c in df.columns if c not in ['timestamp', 'close_time', 'symbol', 'date']]
        return self.scaler.fit_transform(df, cols_to_scale, method=method)
