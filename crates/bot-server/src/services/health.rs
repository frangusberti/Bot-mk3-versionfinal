use tonic::{Request, Response, Status};
use bot_core::proto::health_service_server::HealthService;
use bot_core::proto::{Empty, HealthReport, ComponentHealth as ProtoComponentHealth};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::time::{sleep, Duration};

use bot_data::health::HealthMonitor;

pub struct HealthServiceImpl {
    health_monitor: Arc<HealthMonitor>,
}

impl HealthServiceImpl {
    pub fn new(health_monitor: Arc<HealthMonitor>) -> Self {
        Self { health_monitor }
    }
}

#[tonic::async_trait]
impl HealthService for HealthServiceImpl {
    type StreamHealthStream = ReceiverStream<Result<HealthReport, Status>>;

    async fn stream_health(
        &self,
        _request: Request<Empty>,
    ) -> Result<Response<ReceiverStream<Result<HealthReport, Status>>>, Status> {
        let (tx, rx) = mpsc::channel(4);
        let monitor = self.health_monitor.clone();

        tokio::spawn(async move {
            loop {
                // Generate Health Report from Monitor
                let system_status = monitor.get_system_status();
                let components_map = monitor.get_report();
                
                // Convert internal ComponentHealth to Proto ComponentHealth
                let mut proto_components = HashMap::new();
                for (name, health) in components_map {
                    proto_components.insert(name, ProtoComponentHealth {
                        status: health.status.to_string(),
                        message: health.message,
                        last_heartbeat: health.last_heartbeat,
                        metrics: health.metrics,
                    });
                }
                
                let report = HealthReport {
                    system_status,
                    components: proto_components,
                };

                if tx.send(Ok(report)).await.is_err() {
                    break;
                }
                sleep(Duration::from_secs(1)).await;
            }
        });

        Ok(Response::new(ReceiverStream::new(rx)))
    }
}
