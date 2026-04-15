
    fn build_feature_health(&self) -> FeatureHealth {
        FeatureHealth {
            is_warm: self.feature_engine.is_warm(),
            h1m_candles: 0,
            h5m_candles: 0,
            h15m_candles: 0,
            total_events: self.step_count,
        }
    }
