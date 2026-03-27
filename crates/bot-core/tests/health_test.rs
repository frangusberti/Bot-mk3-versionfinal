use bot_core::health::{HealthStatus, HealthReport};

#[test]
fn test_health_aggregation() {
    assert_eq!(HealthStatus::Healthy.aggregate(HealthStatus::Healthy), HealthStatus::Healthy);
    assert_eq!(HealthStatus::Healthy.aggregate(HealthStatus::Degraded), HealthStatus::Degraded);
    assert_eq!(HealthStatus::Degraded.aggregate(HealthStatus::Critical), HealthStatus::Critical);
    assert_eq!(HealthStatus::Critical.aggregate(HealthStatus::Healthy), HealthStatus::Critical);
}

#[test]
fn test_report_update() {
    let mut report = HealthReport::new();
    assert_eq!(report.system_status, HealthStatus::Healthy);

    report.update_component("Exchange", HealthStatus::Degraded, None);
    assert_eq!(report.system_status, HealthStatus::Degraded);

    report.update_component("Risk", HealthStatus::Critical, Some("Drawdown limit".into()));
    assert_eq!(report.system_status, HealthStatus::Critical);

    report.update_component("Risk", HealthStatus::Healthy, None);
    // Should return to Degraded because Exchange is still Degraded
    assert_eq!(report.system_status, HealthStatus::Degraded);
}
