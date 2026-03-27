/// Compute Group H: Time-of-day cyclic features.
/// Uses sin/cos encoding of hour-of-day for 24h periodicity.
/// Compute time features from a UTC millisecond timestamp.
pub fn compute_time_features(t_emit_ms: i64) -> TimeFeatures {
    // Extract hour-of-day in UTC
    let secs = t_emit_ms / 1000;
    let hour_of_day = ((secs % 86400) as f64) / 86400.0; // Fraction of day [0, 1)

    let two_pi = std::f64::consts::TAU;
    TimeFeatures {
        time_sin: (two_pi * hour_of_day).sin(),
        time_cos: (two_pi * hour_of_day).cos(),
    }
}

#[derive(Debug, Clone)]
pub struct TimeFeatures {
    pub time_sin: f64,
    pub time_cos: f64,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_time_features_midnight() {
        // Midnight UTC = 0 fraction
        let f = compute_time_features(0);
        assert!((f.time_sin - 0.0).abs() < 1e-10);
        assert!((f.time_cos - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_time_features_noon() {
        // Noon UTC = 12 * 3600 * 1000 ms
        let noon_ms = 12 * 3600 * 1000;
        let f = compute_time_features(noon_ms);
        assert!((f.time_sin - 0.0).abs() < 1e-10); // sin(π) ≈ 0
        assert!((f.time_cos - (-1.0)).abs() < 1e-10); // cos(π) ≈ -1
    }
}
