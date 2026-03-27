use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use chrono::Utc;
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub enum ComponentStatus {
    Ok,
    Warning,
    Error,
    Starting,
}

impl std::fmt::Display for ComponentStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            ComponentStatus::Ok => "OK",
            ComponentStatus::Warning => "WARNING",
            ComponentStatus::Error => "ERROR",
            ComponentStatus::Starting => "STARTING",
        };
        write!(f, "{}", s)
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct ComponentHealth {
    pub status: ComponentStatus,
    pub message: String,
    pub last_heartbeat: String,
    pub metrics: HashMap<String, String>,
}

pub struct HealthMonitor {
    components: Arc<Mutex<HashMap<String, ComponentHealth>>>,
}

impl HealthMonitor {
    pub fn new() -> Self {
        Self::default()
    }
}

impl Default for HealthMonitor {
    fn default() -> Self {
        Self {
            components: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

impl HealthMonitor {
    pub fn update_component(&self, name: &str, status: ComponentStatus, message: &str) {
        let mut components = self.components.lock().unwrap();
        let health = components.entry(name.to_string()).or_insert(ComponentHealth {
            status: ComponentStatus::Starting,
            message: "".to_string(),
            last_heartbeat: Utc::now().to_rfc3339(),
            metrics: HashMap::new(),
        });

        health.status = status;
        health.message = message.to_string();
        health.last_heartbeat = Utc::now().to_rfc3339();
    }

    pub fn update_metric(&self, name: &str, key: &str, value: &str) {
        let mut components = self.components.lock().unwrap();
        if let Some(health) = components.get_mut(name) {
            health.metrics.insert(key.to_string(), value.to_string());
            health.last_heartbeat = Utc::now().to_rfc3339();
        }
    }

    pub fn get_report(&self) -> HashMap<String, ComponentHealth> {
        self.components.lock().unwrap().clone()
    }
    
    pub fn get_system_status(&self) -> String {
        let components = self.components.lock().unwrap();
        let mut system_status = "Healthy".to_string();
        
        for health in components.values() {
            match health.status {
                ComponentStatus::Error => return "Critical".to_string(),
                ComponentStatus::Warning => system_status = "Degraded".to_string(),
                _ => {}
            }
        }
        system_status
    }
}
