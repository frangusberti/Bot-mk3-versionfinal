use std::time::{Instant, Duration};
use std::collections::HashMap;
use serde::Serialize;
use bot_core::proto::SymbolStatus;

#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub enum GateReason {
    ObNotReady,
    FailureCooldownActive,
    HealthDegradedTimeout,
    ObsQualityLowTimeout,
    ConsecutiveLossCooldown,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub enum RiskMode {
    Normal,
    ReducedRisk,
    MakerOnly,
    RiskOff,
    Recovery { trades_remaining: u32 },
}

impl std::fmt::Display for GateReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}", self)
    }
}

pub struct RiskGate {
    pub degraded_since: Option<Instant>,
    pub low_quality_since: Option<Instant>,
    pub normal_since: Option<Instant>,
    pub ob_cooldown_end_ts: Option<Instant>,
    pub loss_cooldown_end_ts: Option<Instant>,
    pub current_block: Option<GateReason>,
    pub risk_mode: RiskMode,
    
    // Thresholds
    pub degraded_timeout: Duration,
    pub low_quality_timeout: Duration,
    pub obs_quality_min: f32,
    pub consecutive_failures_max: u32,
    pub failure_cooldown: Duration,
    pub hysteresis_normal: Duration,
    pub consecutive_losses_max: u32,
    pub loss_cooldown: Duration,
    pub recovery_trades_required: u32,
}

impl Default for RiskGate {
    fn default() -> Self {
        Self {
            degraded_since: None,
            low_quality_since: None,
            normal_since: None,
            ob_cooldown_end_ts: None,
            current_block: None,
            degraded_timeout: Duration::from_secs(30),
            low_quality_timeout: Duration::from_secs(30),
            obs_quality_min: 0.7,
            consecutive_failures_max: 5,
            failure_cooldown: Duration::from_secs(60),
            hysteresis_normal: Duration::from_secs(10),
            loss_cooldown_end_ts: None,
            risk_mode: RiskMode::Normal,
            consecutive_losses_max: 3,
            loss_cooldown: Duration::from_secs(300), // 5 min
            recovery_trades_required: 5,
        }
    }
}

impl RiskGate {
    pub fn new() -> Self {
        Self::default()
    }

    /// Primary gate check. Returns Ok(RiskMode) or Err((Reason, Metrics)) if trading should be blocked.
    pub fn check_gate(&mut self, status: &SymbolStatus, ob_failures: u32, consecutive_losses: u32) -> Result<RiskMode, (GateReason, HashMap<String, u64>)> {
        let now = Instant::now();

        // 1. Maintain Timers
        let health_bad =
            status.health_state == "DEGRADED" && status.obs_quality < self.obs_quality_min;
        let quality_bad = status.obs_quality < self.obs_quality_min;
        let is_normal = !health_bad && !quality_bad;
        
        if is_normal {
            if self.normal_since.is_none() {
                self.normal_since = Some(now);
            }
        } else {
            self.normal_since = None;
        }

        // Health Degradation Timer
        if health_bad {
            if self.degraded_since.is_none() {
                self.degraded_since = Some(now);
            }
        } else {
            self.degraded_since = None;
        }

        // Low Quality Timer
        if quality_bad {
            if self.low_quality_since.is_none() {
                self.low_quality_since = Some(now);
            }
        } else {
            self.low_quality_since = None;
        }

        // OB Failure Cooldown
        if ob_failures >= self.consecutive_failures_max {
            if self.ob_cooldown_end_ts.is_none() {
                self.ob_cooldown_end_ts = Some(now + self.failure_cooldown);
            }
        }

        // Check if cooldown expired
        if let Some(end) = self.ob_cooldown_end_ts {
            if now >= end {
                self.ob_cooldown_end_ts = None;
            }
        }

        // Consecutive Loss Cooldown
        if consecutive_losses >= self.consecutive_losses_max {
            if self.loss_cooldown_end_ts.is_none() {
                self.loss_cooldown_end_ts = Some(now + self.loss_cooldown);
                self.risk_mode = RiskMode::RiskOff;
            }
        }

        if let Some(end) = self.loss_cooldown_end_ts {
            if now >= end {
                self.loss_cooldown_end_ts = None;
                self.risk_mode = RiskMode::Recovery { trades_remaining: self.recovery_trades_required };
            }
        }

        // 2. Enforce Priority Logic (High -> Low)
        
        // Priority 1: ObNotReady (Immediate - Always blocks)
        if status.ob_state != "InSync" {
            // We don't necessarily persist this as a 'current_block' for hysteresis
            // because lack of sync is a transient infra state, not a "health penalty".
            return Err((GateReason::ObNotReady, self.get_metrics(now)));
        }

        // Priority 2: FailureCooldownActive
        if let Some(_end) = self.ob_cooldown_end_ts {
            return Err((GateReason::FailureCooldownActive, self.get_metrics(now)));
        }

        // Priority 2.5: Consecutive Loss Cooldown
        if let Some(_end) = self.loss_cooldown_end_ts {
            return Err((GateReason::ConsecutiveLossCooldown, self.get_metrics(now)));
        }

        // Priority 3 & 4 (Persistence Check)
        if self.current_block.is_none() {
            // Check if we SHOULD enter a block
            if let Some(since) = self.degraded_since {
                if now.duration_since(since) >= self.degraded_timeout {
                    self.current_block = Some(GateReason::HealthDegradedTimeout);
                }
            }
            if self.current_block.is_none() {
                if let Some(since) = self.low_quality_since {
                    if now.duration_since(since) >= self.low_quality_timeout {
                        self.current_block = Some(GateReason::ObsQualityLowTimeout);
                    }
                }
            }
        }

        // 3. Final Hysteresis Gate & Current Block Check
        if let Some(reason) = self.current_block {
            // We are in a blocked state. Check if we can recover.
            let recovered = if let Some(since) = self.normal_since {
                now.duration_since(since) >= self.hysteresis_normal
            } else {
                false
            };

            if recovered {
                self.current_block = None;
            } else {
                return Err((reason, self.get_metrics(now)));
            }
        }

        Ok(self.risk_mode.clone())
    }

    /// Records that a candidate trade was explicitly evaluated. Used for path-independent recovery decay.
    pub fn record_trade_evaluated(&mut self) {
        if let RiskMode::Recovery { ref mut trades_remaining } = self.risk_mode {
            if *trades_remaining > 0 {
                *trades_remaining -= 1;
            }
            if *trades_remaining == 0 {
                self.risk_mode = RiskMode::Normal;
            }
        }
    }

    fn get_metrics(&self, now: Instant) -> HashMap<String, u64> {
        let mut m = HashMap::new();
        if let Some(end) = self.ob_cooldown_end_ts {
            m.insert("cooldown_remaining_ms".to_string(), end.saturating_duration_since(now).as_millis() as u64);
        }
        if let Some(since) = self.degraded_since {
            m.insert("degraded_duration_ms".to_string(), now.duration_since(since).as_millis() as u64);
        }
        if let Some(since) = self.low_quality_since {
            m.insert("low_quality_duration_ms".to_string(), now.duration_since(since).as_millis() as u64);
        }
        if let Some(since) = self.normal_since {
            m.insert("normal_duration_ms".to_string(), now.duration_since(since).as_millis() as u64);
        }
        m
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mock_status(health: &str, quality: f32, ob_state: &str) -> SymbolStatus {
        SymbolStatus {
            health_state: health.to_string(),
            obs_quality: quality,
            ob_state: ob_state.to_string(),
            ..SymbolStatus::default()
        }
    }

    #[test]
    fn test_risk_gate_degraded_timer() {
        let mut gate = RiskGate::new();
        let status = mock_status("DEGRADED", 1.0, "InSync");
        
        // Immediate check: still OK because timeout is 30s
        // Wait, hysteresis starts at None, so it will be blocked initially until 10s of Normal.
        // Let's first establish a NORMAL base.
        let normal = mock_status("NORMAL", 1.0, "InSync");
        gate.normal_since = Some(Instant::now() - Duration::from_secs(11));
        
        assert!(gate.check_gate(&normal, 0, 0).is_ok());

        // Now move to DEGRADED
        let res = gate.check_gate(&status, 0, 0);
        assert!(res.is_ok()); // Still OK, timeout not reached
        
        gate.degraded_since = Some(Instant::now() - Duration::from_secs(31));
        let res = gate.check_gate(&status, 0, 0);
        assert!(res.is_err());
        assert_eq!(res.unwrap_err().0, GateReason::HealthDegradedTimeout);
    }

    #[test]
    fn test_risk_gate_hysteresis() {
        let mut gate = RiskGate::new();
        let normal = mock_status("NORMAL", 1.0, "InSync");
        
        // Initially normal_since is None
        assert!(gate.check_gate(&normal, 0, 0).is_err());
        
        // Stabilize for 5s
        gate.normal_since = Some(Instant::now() - Duration::from_secs(5));
        assert!(gate.check_gate(&normal, 0, 0).is_err());

        // 11s
        gate.normal_since = Some(Instant::now() - Duration::from_secs(11));
        assert!(gate.check_gate(&normal, 0, 0).is_ok());

        // Any non-normal tick resets it
        let degraded = mock_status("DEGRADED", 1.0, "InSync");
        gate.check_gate(&degraded, 0, 0).unwrap_err();
        assert!(gate.normal_since.is_none());
    }

    #[test]
    fn test_risk_gate_failures_cooldown() {
        let mut gate = RiskGate::new();
        let normal = mock_status("NORMAL", 1.0, "InSync");
        gate.normal_since = Some(Instant::now() - Duration::from_secs(11));

        // Trigger cooldown
        let res = gate.check_gate(&normal, 5, 0);
        assert!(res.is_err());
        assert_eq!(res.unwrap_err().0, GateReason::FailureCooldownActive);
        assert!(gate.ob_cooldown_end_ts.is_some());

        // Check expiry (mock)
        gate.ob_cooldown_end_ts = Some(Instant::now() - Duration::from_secs(1));
        assert!(gate.check_gate(&normal, 0, 0).is_ok());
    }
}
