fn main() {
    // This should compile if AnalyticsServiceServer exists
    let _ = bot_core::proto::analytics_service_server::AnalyticsServiceServer::new(TestService);
}

struct TestService;
#[tonic::async_trait]
impl bot_core::proto::analytics_service_server::AnalyticsService for TestService {
    async fn get_session_metrics(
        &self,
        request: tonic::Request<bot_core::proto::SessionRequest>,
    ) -> Result<tonic::Response<bot_core::proto::SessionMetricsResponse>, tonic::Status> {
        unimplemented!()
    }
    
    async fn get_equity_curve(
        &self,
        request: tonic::Request<bot_core::proto::SessionRequest>,
    ) -> Result<tonic::Response<bot_core::proto::EquityCurveResponse>, tonic::Status> {
        unimplemented!()
    }
    
    async fn list_sessions(
        &self,
        request: tonic::Request<bot_core::proto::Empty>,
    ) -> Result<tonic::Response<bot_core::proto::SessionListResponse>, tonic::Status> {
        unimplemented!()
    }
}
