use tonic::{Request, Response, Status};
use bot_core::proto::market_service_server::MarketService;
use bot_core::proto::{MarketSubscription, MarketSnapshot};
use tokio::sync::{mpsc, broadcast};
use tokio_stream::wrappers::ReceiverStream;

pub struct MarketServiceImpl {
    snapshot_tx: broadcast::Sender<MarketSnapshot>,
}

impl MarketServiceImpl {
    pub fn new(snapshot_tx: broadcast::Sender<MarketSnapshot>) -> Self {
        Self { snapshot_tx }
    }
}

#[tonic::async_trait]
impl MarketService for MarketServiceImpl {
    type SubscribeMarketSnapshotStream = ReceiverStream<Result<MarketSnapshot, Status>>;

    async fn subscribe_market_snapshot(
        &self,
        request: Request<MarketSubscription>,
    ) -> Result<Response<ReceiverStream<Result<MarketSnapshot, Status>>>, Status> {
        let symbol_req = request.into_inner().symbol;
        let mut rx = self.snapshot_tx.subscribe();
        let (tx, r_stream) = mpsc::channel(16);

        tokio::spawn(async move {
            while let Ok(snap) = rx.recv().await {
                 if snap.symbol == symbol_req && tx.send(Ok(snap)).await.is_err() { break; }
            }
        });

        Ok(Response::new(ReceiverStream::new(r_stream)))
    }
}
