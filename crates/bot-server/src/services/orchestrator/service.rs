use tonic::{Request, Response, Status};
use tokio::sync::{mpsc, oneshot};
use bot_core::proto::orchestrator_service_server::OrchestratorService;
use bot_core::proto::{
    StartOrchestratorRequest, StartOrchestratorResponse,
    StopOrchestratorRequest, StopOrchestratorResponse,
    GetOrchestratorStatusRequest, OrchestratorStatus,
    OrchestratorEvent, StreamOrchestratorEventsRequest,
    SetModeRequest, SetModeResponse, UpdateConfigRequest, UpdateConfigResponse,
    RiskConfigProto, RiskStatusProto, CommissionPolicyProto, CommissionStatsProto,
    KillSwitchRequest, HealthStatusProto,
};
use super::engine::OrchestratorCommand;
use tokio_stream::wrappers::ReceiverStream;

pub struct OrchestratorServiceImpl {
    cmd_tx: mpsc::Sender<OrchestratorCommand>,
}

impl OrchestratorServiceImpl {
    pub fn new(cmd_tx: mpsc::Sender<OrchestratorCommand>) -> Self {
        Self { cmd_tx }
    }
}

#[tonic::async_trait]
impl OrchestratorService for OrchestratorServiceImpl {
    async fn start_orchestrator(
        &self,
        request: Request<StartOrchestratorRequest>,
    ) -> Result<Response<StartOrchestratorResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::Start(request.into_inner(), tx)).await
            .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn stop_orchestrator(
        &self,
        request: Request<StopOrchestratorRequest>,
    ) -> Result<Response<StopOrchestratorResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::Stop(request.into_inner(), tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn get_orchestrator_status(
        &self,
        request: Request<GetOrchestratorStatusRequest>,
    ) -> Result<Response<OrchestratorStatus>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::GetStatus(request.into_inner(), tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    type StreamOrchestratorEventsStream = ReceiverStream<Result<OrchestratorEvent, Status>>;

    async fn stream_orchestrator_events(
        &self,
        _request: Request<StreamOrchestratorEventsRequest>,
    ) -> Result<Response<ReceiverStream<Result<OrchestratorEvent, Status>>>, Status> {
        let (tx, rx) = mpsc::channel(100);
        self.cmd_tx.send(OrchestratorCommand::SubscribeEvents(tx)).await
            .map_err(|_| Status::internal("Engine closed"))?;
        Ok(Response::new(ReceiverStream::new(rx)))
    }

    async fn set_mode(
        &self,
        request: Request<SetModeRequest>,
    ) -> Result<Response<SetModeResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::SetMode(request.into_inner(), tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn update_config(
        &self,
        request: Request<UpdateConfigRequest>,
    ) -> Result<Response<UpdateConfigResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::UpdateConfig(request.into_inner(), tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn reset_paper_state(
        &self,
        _request: Request<bot_core::proto::Empty>,
    ) -> Result<Response<UpdateConfigResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::ResetPaperState(tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn reload_policy(
        &self,
        request: Request<bot_core::proto::ReloadPolicyRequest>,
    ) -> Result<Response<bot_core::proto::ReloadPolicyResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::ReloadPolicy(request.into_inner(), tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    // ── Risk & Commission RPCs ──

    async fn update_risk_config(
        &self,
        request: Request<RiskConfigProto>,
    ) -> Result<Response<UpdateConfigResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::UpdateRiskConfig(request.into_inner(), tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn get_risk_status(
        &self,
        _request: Request<bot_core::proto::Empty>,
    ) -> Result<Response<RiskStatusProto>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::GetRiskStatus(tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn update_commission_policy(
        &self,
        request: Request<CommissionPolicyProto>,
    ) -> Result<Response<UpdateConfigResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::UpdateCommissionPolicy(request.into_inner(), tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn get_commission_stats(
        &self,
        _request: Request<bot_core::proto::Empty>,
    ) -> Result<Response<CommissionStatsProto>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::GetCommissionStats(tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn reset_risk_state(
        &self,
        _request: Request<bot_core::proto::Empty>,
    ) -> Result<Response<UpdateConfigResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::ResetRiskState(tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn kill_switch(
        &self,
        request: Request<KillSwitchRequest>,
    ) -> Result<Response<UpdateConfigResponse>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::KillSwitch(request.into_inner(), tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }

    async fn get_health_status(
        &self,
        _request: Request<bot_core::proto::Empty>,
    ) -> Result<Response<HealthStatusProto>, Status> {
        let (tx, rx) = oneshot::channel();
        self.cmd_tx.send(OrchestratorCommand::GetHealthStatus(tx)).await
             .map_err(|_| Status::internal("Engine closed"))?;
        rx.await.map_err(|_| Status::internal("Engine dropped response"))
            .map(Response::new)
    }
}
