use log::info;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

// ─── Enums & Config ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
pub enum LeverageMode {
    #[default]
    Manual,
    Auto,
    Fixed,
}

impl LeverageMode {
    /// Convert from proto i32 enum value.
    pub fn from_proto(v: i32) -> Self {
        match v {
            1 => LeverageMode::Manual,
            2 => LeverageMode::Auto,
            3 => LeverageMode::Fixed,
            _ => LeverageMode::Manual, // UNSPECIFIED (0) → Manual
        }
    }

    /// Convert to proto i32 enum value.
    #[allow(dead_code)]
    pub fn to_proto(self) -> i32 {
        match self {
            LeverageMode::Manual => 1,
            LeverageMode::Auto => 2,
            LeverageMode::Fixed => 3,
        }
    }

    #[allow(dead_code)]
    pub fn as_str(&self) -> &'static str {
        match self {
            LeverageMode::Manual => "MANUAL",
            LeverageMode::Auto => "AUTO",
            LeverageMode::Fixed => "FIXED",
        }
    }
}

/// Per-symbol leverage configuration (persisted to JSON).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LeverageConfig {
    pub mode: LeverageMode,
    pub manual_value: f64,            // Used by MANUAL mode (>=1.0)
    pub fixed_value: f64,             // Used by FIXED mode (>=1.0)
    pub auto_min: f64,                // AUTO: minimum leverage
    pub auto_max: f64,                // AUTO: maximum leverage
    pub auto_cooldown_secs: u64,      // AUTO: seconds between changes
    pub auto_max_change_per_min: f64, // AUTO: max leverage delta per minute
    // Configurable reference values for AUTO normalization
    pub auto_vol_ref: f64,    // Reference volatility for normalization
    pub auto_spread_ref: f64, // Reference spread for normalization
    // Live apply settings
    pub live_apply_enabled: bool,
    pub live_apply_on_start: bool,
    pub live_readback_enabled: bool,
    pub live_readback_interval_secs: u64,
}

impl Default for LeverageConfig {
    fn default() -> Self {
        Self {
            mode: LeverageMode::Manual,
            manual_value: 5.0,
            fixed_value: 5.0,
            auto_min: 3.0,
            auto_max: 10.0,
            auto_cooldown_secs: 60,
            auto_max_change_per_min: 1.0,
            auto_vol_ref: 0.002,
            auto_spread_ref: 0.001,
            live_apply_enabled: false,
            live_apply_on_start: false,
            live_readback_enabled: true,
            live_readback_interval_secs: 120,
        }
    }
}

impl LeverageConfig {
    fn normalize_lev(v: f64) -> f64 {
        v.clamp(1.0, 125.0).round()
    }

    fn normalize_int_fields(&mut self) {
        self.manual_value = Self::normalize_lev(self.manual_value);
        self.fixed_value = Self::normalize_lev(self.fixed_value);
        self.auto_min = Self::normalize_lev(self.auto_min).clamp(1.0, 50.0);
        self.auto_max = Self::normalize_lev(self.auto_max);
        if self.auto_min > self.auto_max {
            std::mem::swap(&mut self.auto_min, &mut self.auto_max);
        }
    }

    /// Validate config. Returns Err with reason if invalid.
    pub fn validate(&self) -> Result<(), String> {
        if self.manual_value < 1.0 {
            return Err("manual_value must be >= 1.0".into());
        }
        if self.fixed_value < 1.0 {
            return Err("fixed_value must be >= 1.0".into());
        }
        if self.auto_min < 1.0 {
            return Err("auto_min must be >= 1.0".into());
        }
        if self.auto_max < 1.0 {
            return Err("auto_max must be >= 1.0".into());
        }
        if self.auto_min > self.auto_max {
            return Err(format!(
                "auto_min ({}) > auto_max ({})",
                self.auto_min, self.auto_max
            ));
        }
        if self.auto_max > 125.0 {
            return Err("auto_max must be <= 125 (Binance limit)".into());
        }
        if self.auto_max_change_per_min <= 0.0 {
            return Err("auto_max_change_per_min must be > 0".into());
        }
        if self.auto_vol_ref <= 0.0 {
            return Err("auto_vol_ref must be > 0".into());
        }
        if self.auto_spread_ref <= 0.0 {
            return Err("auto_spread_ref must be > 0".into());
        }
        Ok(())
    }
}

// ─── Runtime State ───────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct LeverageState {
    pub current_leverage: f64,
    pub last_change_ts: i64, // milliseconds
    pub last_risk_score: f64,
    pub last_reason: String,
    pub apply_state: String, // "OFF" | "APPLIED_OK" | "APPLIED_FAIL"
    pub apply_error: String, // last error if fail
}

impl LeverageState {
    pub fn new(initial: f64) -> Self {
        Self {
            current_leverage: initial,
            last_change_ts: 0,
            last_risk_score: 0.0,
            last_reason: "init".into(),
            apply_state: "OFF".into(),
            apply_error: String::new(),
        }
    }
}

// ─── Deterministic AUTO Policy ───────────────────────────────────────────────

pub struct LeveragePolicy;

impl LeveragePolicy {
    /// Compute target leverage from market features using config refs.
    /// Returns (new_leverage, reason_string).
    /// Deterministic: same inputs → same output.
    pub fn compute(
        config: &LeverageConfig,
        state: &LeverageState,
        realized_vol: f64,
        relative_spread: f64,
        ts_ms: i64,
    ) -> (f64, String) {
        // 1. Normalize inputs using configurable references
        let vol_norm = (realized_vol / config.auto_vol_ref).clamp(0.0, 1.0);
        let spread_norm = (relative_spread / config.auto_spread_ref).clamp(0.0, 1.0);

        // 2. Risk score [0,1] — higher = more risky = lower leverage
        let risk_score = 0.7 * vol_norm + 0.3 * spread_norm;

        // 3. Target leverage via linear interpolation
        //    risk=0 → max_leverage, risk=1 → min_leverage
        let target_raw = config.auto_max + (config.auto_min - config.auto_max) * risk_score;

        // 4. Cooldown check
        let dt_ms = ts_ms - state.last_change_ts;
        let cooldown_ms = (config.auto_cooldown_secs as i64) * 1000;

        if dt_ms < cooldown_ms && state.last_change_ts > 0 {
            return (
                state.current_leverage,
                format!(
                    "cooldown ({:.0}s/{:.0}s) risk={:.3}",
                    dt_ms as f64 / 1000.0,
                    config.auto_cooldown_secs as f64,
                    risk_score
                ),
            );
        }

        // 5. Rate limiting: cap delta per minute
        let dt_minutes = (dt_ms as f64 / 60_000.0).max(0.001); // avoid div-by-zero
        let max_delta = config.auto_max_change_per_min * dt_minutes;
        let delta = target_raw - state.current_leverage;
        let clamped_delta = delta.clamp(-max_delta, max_delta);
        let new_lev = (state.current_leverage + clamped_delta)
            .clamp(config.auto_min, config.auto_max)
            .round();

        let reason = format!(
            "risk={:.3} vol_n={:.3} spr_n={:.3} target={:.2} delta={:.2}",
            risk_score, vol_norm, spread_norm, target_raw, clamped_delta
        );

        (new_lev, reason)
    }
}

// ─── Leverage Manager ────────────────────────────────────────────────────────

/// Default symbols for multi-symbol setup.
const DEFAULT_SYMBOLS: &[&str] = &["BTCUSDT", "ETHUSDT", "DOGEUSDT", "XRPUSDT"];

/// Centralized manager for all per-symbol leverage configs and states.
pub struct LeverageManager {
    configs: HashMap<String, LeverageConfig>,
    states: HashMap<String, LeverageState>,
}

impl LeverageManager {
    pub fn new() -> Self {
        Self {
            configs: HashMap::new(),
            states: HashMap::new(),
        }
    }

    /// Get or create config for a symbol (defaults if missing).
    pub fn get_config(&self, symbol: &str) -> LeverageConfig {
        self.configs.get(symbol).cloned().unwrap_or_default()
    }

    /// Set config for a symbol (validates first).
    pub fn set_config(&mut self, symbol: &str, config: LeverageConfig) -> Result<(), String> {
        let mut config = config;
        config.normalize_int_fields();
        config.validate()?;
        // Initialize state if not present
        if !self.states.contains_key(symbol) {
            let initial = Self::initial_leverage(&config);
            self.states
                .insert(symbol.to_string(), LeverageState::new(initial));
        }
        self.configs.insert(symbol.to_string(), config);
        Ok(())
    }

    /// Compute initial leverage for a config (AUTO uses midpoint).
    fn initial_leverage(config: &LeverageConfig) -> f64 {
        match config.mode {
            LeverageMode::Manual => config.manual_value.round(),
            LeverageMode::Fixed => config.fixed_value.round(),
            LeverageMode::Auto => ((config.auto_min + config.auto_max) / 2.0).round(),
        }
    }

    /// Get the effective leverage for a symbol right now (no recompute).
    pub fn get_effective_leverage(&self, symbol: &str) -> f64 {
        let config = self.get_config(symbol);
        match config.mode {
            LeverageMode::Manual => config.manual_value.round(),
            LeverageMode::Fixed => config.fixed_value.round(),
            LeverageMode::Auto => self
                .states
                .get(symbol)
                .map(|s| s.current_leverage.round())
                .unwrap_or(Self::initial_leverage(&config)),
        }
    }

    /// Update AUTO leverage for a symbol using latest market features.
    /// Returns new effective leverage.
    /// Must only be called on decision ticks (FeatureVector emission).
    pub fn update_auto(
        &mut self,
        symbol: &str,
        realized_vol: f64,
        relative_spread: f64,
        ts_ms: i64,
    ) -> f64 {
        let config = self.get_config(symbol);
        if config.mode != LeverageMode::Auto {
            return self.get_effective_leverage(symbol);
        }

        let state = self
            .states
            .entry(symbol.to_string())
            .or_insert_with(|| LeverageState::new(Self::initial_leverage(&config)));

        let (new_lev, reason) =
            LeveragePolicy::compute(&config, state, realized_vol, relative_spread, ts_ms);

        let changed = (new_lev - state.current_leverage).abs() > 0.001;
        if changed {
            info!(
                "[LEV][{}] mode=AUTO risk={:.3} last={:.1} new={:.1} reason=\"{}\"",
                symbol, state.last_risk_score, state.current_leverage, new_lev, reason
            );
            state.last_change_ts = ts_ms;
        }

        state.current_leverage = new_lev;
        // Recompute risk score with config refs
        let vol_norm = (realized_vol / config.auto_vol_ref).clamp(0.0, 1.0);
        let spread_norm = (relative_spread / config.auto_spread_ref).clamp(0.0, 1.0);
        state.last_risk_score = 0.7 * vol_norm + 0.3 * spread_norm;
        state.last_reason = reason;

        new_lev
    }

    /// Set the live apply status for a symbol.
    pub fn set_apply_status(&mut self, symbol: &str, state: &str, error: &str) {
        if let Some(s) = self.states.get_mut(symbol) {
            s.apply_state = state.to_string();
            s.apply_error = error.to_string();
        }
    }

    /// Get runtime state for a symbol (for GUI/status display).
    #[allow(dead_code)]
    pub fn get_state(&self, symbol: &str) -> Option<&LeverageState> {
        self.states.get(symbol)
    }

    /// Save all configs to a JSON file.
    pub fn save_to_disk(&self, path: &str) -> Result<(), String> {
        let json = serde_json::to_string_pretty(&self.configs)
            .map_err(|e| format!("serialize error: {}", e))?;

        // Ensure parent directory exists
        if let Some(parent) = std::path::Path::new(path).parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("mkdir error: {}", e))?;
        }

        std::fs::write(path, json).map_err(|e| format!("write error: {}", e))?;

        info!("[LEV] Config saved to {}", path);
        Ok(())
    }

    /// Load configs from a JSON file. Missing file => defaults for all 4 symbols.
    pub fn load_from_disk(&mut self, path: &str) -> Result<(), String> {
        let data = match std::fs::read_to_string(path) {
            Ok(d) => d,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                info!(
                    "[LEV] No config file at {}, creating defaults for {} symbols",
                    path,
                    DEFAULT_SYMBOLS.len()
                );
                for sym in DEFAULT_SYMBOLS {
                    let cfg = LeverageConfig::default();
                    let initial = Self::initial_leverage(&cfg);
                    self.states
                        .insert(sym.to_string(), LeverageState::new(initial));
                    self.configs.insert(sym.to_string(), cfg);
                }
                // Persist the defaults
                let _ = self.save_to_disk(path);
                return Ok(());
            }
            Err(e) => return Err(format!("read error: {}", e)),
        };

        let loaded: HashMap<String, LeverageConfig> =
            serde_json::from_str(&data).map_err(|e| format!("parse error: {}", e))?;

        for (symbol, config) in loaded {
            config
                .validate()
                .map_err(|e| format!("{}: {}", symbol, e))?;
            let initial = Self::initial_leverage(&config);
            self.states
                .insert(symbol.clone(), LeverageState::new(initial));
            self.configs.insert(symbol, config);
        }

        info!(
            "[LEV] Loaded {} symbol configs from {}",
            self.configs.len(),
            path
        );
        Ok(())
    }

    /// Get all configured symbols.
    #[allow(dead_code)]
    pub fn symbols(&self) -> Vec<String> {
        self.configs.keys().cloned().collect()
    }
}

// ─── Tests ───────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn auto_config() -> LeverageConfig {
        LeverageConfig {
            mode: LeverageMode::Auto,
            auto_min: 3.0,
            auto_max: 10.0,
            auto_cooldown_secs: 60,
            auto_max_change_per_min: 1.0,
            auto_vol_ref: 0.002,
            auto_spread_ref: 0.001,
            ..Default::default()
        }
    }

    #[test]
    fn test_determinism() {
        let cfg = auto_config();
        let state = LeverageState::new(6.5);

        let (a, _) = LeveragePolicy::compute(&cfg, &state, 0.001, 0.0005, 120_000);
        let (b, _) = LeveragePolicy::compute(&cfg, &state, 0.001, 0.0005, 120_000);
        assert_eq!(a, b, "Same inputs must produce same output");
    }

    #[test]
    fn test_bounds_never_exceeded() {
        let cfg = auto_config();
        let state = LeverageState::new(6.5);

        // Extreme high risk
        let (lev, _) = LeveragePolicy::compute(&cfg, &state, 1.0, 1.0, 120_000);
        assert!(
            lev >= cfg.auto_min,
            "Must not go below auto_min: got {}",
            lev
        );
        assert!(lev <= cfg.auto_max, "Must not exceed auto_max: got {}", lev);

        // Extreme low risk
        let (lev, _) = LeveragePolicy::compute(&cfg, &state, 0.0, 0.0, 120_000);
        assert!(lev >= cfg.auto_min);
        assert!(lev <= cfg.auto_max);
    }

    #[test]
    fn test_rate_limit() {
        let cfg = auto_config(); // max_change_per_min = 1.0
        let state = LeverageState {
            current_leverage: 10.0,
            last_change_ts: 0,
            last_risk_score: 0.0,
            last_reason: String::new(),
            apply_state: "OFF".into(),
            apply_error: String::new(),
        };

        // After 90 seconds (1.5 min), max delta = 1.5
        let (lev, _) = LeveragePolicy::compute(&cfg, &state, 1.0, 1.0, 90_000);
        let delta = (lev - 10.0).abs();
        assert!(
            delta <= 1.5 + 0.001,
            "Delta {} exceeds rate limit 1.5",
            delta
        );
    }

    #[test]
    fn test_cooldown() {
        let cfg = auto_config(); // cooldown = 60s
        let state = LeverageState {
            current_leverage: 8.0,
            last_change_ts: 50_000,
            last_risk_score: 0.0,
            last_reason: String::new(),
            apply_state: "OFF".into(),
            apply_error: String::new(),
        };

        // At t=80s (30s since last change < 60s cooldown), should keep current
        let (lev, reason) = LeveragePolicy::compute(&cfg, &state, 0.001, 0.0005, 80_000);
        assert_eq!(lev, 8.0, "Cooldown active: leverage must not change");
        assert!(
            reason.contains("cooldown"),
            "Reason should mention cooldown"
        );
    }

    #[test]
    fn test_manual_mode() {
        let mut mgr = LeverageManager::new();
        let cfg = LeverageConfig {
            mode: LeverageMode::Manual,
            manual_value: 7.0,
            ..Default::default()
        };
        mgr.set_config("BTCUSDT", cfg).unwrap();

        assert_eq!(mgr.get_effective_leverage("BTCUSDT"), 7.0);

        // update_auto should not change manual mode
        let lev = mgr.update_auto("BTCUSDT", 0.005, 0.002, 1_000_000);
        assert_eq!(lev, 7.0);
    }

    #[test]
    fn test_fixed_mode() {
        let mut mgr = LeverageManager::new();
        let cfg = LeverageConfig {
            mode: LeverageMode::Fixed,
            fixed_value: 12.0,
            ..Default::default()
        };
        mgr.set_config("ETHUSDT", cfg).unwrap();

        assert_eq!(mgr.get_effective_leverage("ETHUSDT"), 12.0);
    }

    #[test]
    fn test_config_validation_rejects_bad() {
        let bad1 = LeverageConfig {
            manual_value: 0.5,
            ..Default::default()
        };
        assert!(bad1.validate().is_err());

        let bad2 = LeverageConfig {
            auto_min: 10.0,
            auto_max: 3.0,
            ..Default::default()
        };
        assert!(bad2.validate().is_err());

        let bad3 = LeverageConfig {
            auto_max: 200.0,
            ..Default::default()
        };
        assert!(bad3.validate().is_err());

        // New: validate refs
        let bad4 = LeverageConfig {
            auto_vol_ref: 0.0,
            ..Default::default()
        };
        assert!(bad4.validate().is_err(), "vol_ref=0 must fail");

        let bad5 = LeverageConfig {
            auto_spread_ref: -1.0,
            ..Default::default()
        };
        assert!(bad5.validate().is_err(), "spread_ref<0 must fail");

        let bad6 = LeverageConfig {
            auto_max_change_per_min: 0.0,
            ..Default::default()
        };
        assert!(bad6.validate().is_err(), "max_change=0 must fail");
    }

    #[test]
    fn test_unknown_symbol_returns_default() {
        let mgr = LeverageManager::new();
        let lev = mgr.get_effective_leverage("UNKNOWN");
        assert_eq!(lev, 5.0); // Default manual_value
    }

    #[test]
    fn test_high_risk_lowers_leverage() {
        let cfg = auto_config();
        let state = LeverageState::new(6.5);

        // Low risk (vol=0, spread=0) -> should target max (10)
        let (low_risk_lev, _) = LeveragePolicy::compute(&cfg, &state, 0.0, 0.0, 120_000);

        // High risk (vol >> ref, spread >> ref) -> should target min (3)
        let (high_risk_lev, _) = LeveragePolicy::compute(&cfg, &state, 0.01, 0.01, 120_000);

        assert!(
            low_risk_lev > high_risk_lev,
            "Low risk leverage {} should be > high risk leverage {}",
            low_risk_lev,
            high_risk_lev
        );
    }

    #[test]
    fn test_configurable_refs() {
        // Two configs that differ only in vol_ref — must produce different risk scores
        let cfg_tight = LeverageConfig {
            mode: LeverageMode::Auto,
            auto_vol_ref: 0.001, // tight ref → vol looks large
            auto_spread_ref: 0.001,
            ..auto_config()
        };
        let cfg_wide = LeverageConfig {
            mode: LeverageMode::Auto,
            auto_vol_ref: 0.01, // wide ref → same vol looks small
            auto_spread_ref: 0.001,
            ..auto_config()
        };
        let state = LeverageState::new(6.5);
        let vol = 0.002;
        let spread = 0.0005;

        let (lev_tight, _) = LeveragePolicy::compute(&cfg_tight, &state, vol, spread, 120_000);
        let (lev_wide, _) = LeveragePolicy::compute(&cfg_wide, &state, vol, spread, 120_000);

        // Tight ref makes vol look maxed (vol/0.001=2.0 clamped to 1.0) → lower leverage
        // Wide ref makes vol look small (vol/0.01=0.2) → higher leverage
        assert!(
            lev_wide > lev_tight,
            "Wide ref should produce higher leverage: {} vs {}",
            lev_wide,
            lev_tight
        );
    }

    #[test]
    fn test_auto_init_midpoint() {
        let mut mgr = LeverageManager::new();
        let cfg = LeverageConfig {
            mode: LeverageMode::Auto,
            auto_min: 4.0,
            auto_max: 12.0,
            ..Default::default()
        };
        mgr.set_config("BTCUSDT", cfg).unwrap();
        // AUTO should initialize at midpoint (4+12)/2 = 8.0
        assert_eq!(mgr.get_effective_leverage("BTCUSDT"), 8.0);
    }

    #[test]
    fn test_proto_enum_roundtrip() {
        assert_eq!(LeverageMode::from_proto(0), LeverageMode::Manual); // UNSPECIFIED → Manual
        assert_eq!(LeverageMode::from_proto(1), LeverageMode::Manual);
        assert_eq!(LeverageMode::from_proto(2), LeverageMode::Auto);
        assert_eq!(LeverageMode::from_proto(3), LeverageMode::Fixed);

        assert_eq!(LeverageMode::Manual.to_proto(), 1);
        assert_eq!(LeverageMode::Auto.to_proto(), 2);
        assert_eq!(LeverageMode::Fixed.to_proto(), 3);
    }

    #[test]
    fn test_set_config_normalizes_integer_leverage_values() {
        let mut mgr = LeverageManager::new();
        let cfg = LeverageConfig {
            mode: LeverageMode::Manual,
            manual_value: 10.5,
            fixed_value: 7.6,
            auto_min: 3.4,
            auto_max: 10.6,
            ..Default::default()
        };

        mgr.set_config("BTCUSDT", cfg).unwrap();
        let loaded = mgr.get_config("BTCUSDT");
        assert_eq!(loaded.manual_value.fract(), 0.0);
        assert_eq!(loaded.fixed_value.fract(), 0.0);
        assert_eq!(loaded.auto_min.fract(), 0.0);
        assert_eq!(loaded.auto_max.fract(), 0.0);
    }

    #[test]
    fn test_auto_update_returns_integer_leverage() {
        let mut mgr = LeverageManager::new();
        let cfg = LeverageConfig {
            mode: LeverageMode::Auto,
            auto_min: 3.0,
            auto_max: 11.0,
            ..Default::default()
        };
        mgr.set_config("BTCUSDT", cfg).unwrap();
        let lev = mgr.update_auto("BTCUSDT", 0.0017, 0.0008, 120_000);
        assert_eq!(lev.fract(), 0.0);
    }
}
