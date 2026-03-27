use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use chrono::{DateTime, Utc};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum HealthStatus {
    Healthy,
    Degraded,
    Critical,
}

impl HealthStatus {
    /// Returns the more severe status.
    pub fn aggregate(self, other: Self) -> Self {
        match (self, other) {
            (HealthStatus::Critical, _) | (_, HealthStatus::Critical) => HealthStatus::Critical,
            (HealthStatus::Degraded, _) | (_, HealthStatus::Degraded) => HealthStatus::Degraded,
            (HealthStatus::Healthy, HealthStatus::Healthy) => HealthStatus::Healthy,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HealthReport {
    pub system_status: HealthStatus,
    pub components: HashMap<String, ComponentHealth>,
    pub generated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ComponentHealth {
    pub status: HealthStatus,
    pub message: Option<String>,
    pub last_heartbeat: DateTime<Utc>,
    pub metrics: HashMap<String, String>, // Simple key-value metrics
}

impl HealthReport {
    pub fn new() -> Self {
        Self::default()
    }
}

impl Default for HealthReport {
    fn default() -> Self {
        Self {
            system_status: HealthStatus::Healthy,
            components: HashMap::new(),
            generated_at: Utc::now(),
        }
    }
}

impl HealthReport {
    pub fn update_component(&mut self, name: &str, status: HealthStatus, message: Option<String>) {
        let component = self.components.entry(name.to_string()).or_insert(ComponentHealth {
            status: HealthStatus::Healthy,
            message: None,
            last_heartbeat: Utc::now(),
            metrics: HashMap::new(),
        });
        
        component.status = status;
        component.message = message;
        component.last_heartbeat = Utc::now();
        
        self.recalculate_system_status();
    }
    
    fn recalculate_system_status(&mut self) {
        let mut agg = HealthStatus::Healthy;
        for c in self.components.values() {
            agg = agg.aggregate(c.status);
        }
        self.system_status = agg;
    }
}
