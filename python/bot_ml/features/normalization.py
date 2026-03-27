import pandas as pd
import numpy as np
from typing import Dict, Any

class FeatureScaler:
    def __init__(self):
        self.scalers: Dict[str, Any] = {}

    def fit_transform(self, df: pd.DataFrame, columns: list, method: str = 'minmax') -> pd.DataFrame:
        df_scaled = df.copy()
        
        for col in columns:
            if method == 'minmax':
                min_val = df[col].min()
                max_val = df[col].max()
                self.scalers[col] = {'min': min_val, 'max': max_val, 'method': 'minmax'}
                
                if max_val - min_val == 0:
                    df_scaled[col] = 0
                else:
                    df_scaled[col] = (df[col] - min_val) / (max_val - min_val)
                    
            elif method == 'zscore':
                mean_val = df[col].mean()
                std_val = df[col].std()
                self.scalers[col] = {'mean': mean_val, 'std': std_val, 'method': 'zscore'}
                
                if std_val == 0:
                    df_scaled[col] = 0
                else:
                    df_scaled[col] = (df[col] - mean_val) / std_val
                    
        return df_scaled

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        df_scaled = df.copy()
        
        for col, params in self.scalers.items():
            if col not in df.columns:
                continue
                
            if params['method'] == 'minmax':
                min_val = params['min']
                max_val = params['max']
                if max_val - min_val == 0:
                    df_scaled[col] = 0
                else:
                    df_scaled[col] = (df[col] - min_val) / (max_val - min_val)
                    
            elif params['method'] == 'zscore':
                mean_val = params['mean']
                std_val = params['std']
                if std_val == 0:
                    df_scaled[col] = 0
                else:
                    df_scaled[col] = (df[col] - mean_val) / std_val
                    
        return df_scaled
