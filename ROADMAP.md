# Bot Mk3 Roadmap

## Phase 1: Foundation (Completed)
- [x] **Architecture Design**: Separation of concerns (Data, Control, Execution).
- [x] **Data Recorder**: Rust backend, Parquet storage, Binance Futures WS.
- [x] **Strict Normalization**: Canonical schema, deterministic dataset IDs.
- [x] **Quality Engine**: Integrity checks, lag/drift metrics, usability flags.
- [x] **Basic GUI**: Recorder control, Health status board.

## Phase 2: Feature Engineering & Analysis (Current)
- [ ] **Feature Engine**: 1m candle aggregation from normalized processed events.
- [ ] **Technical Indicators**: TA-Lib integration (Rust/Python).
- [ ] **Data Loader**: Python Polars/PyArrow loader for training.
- [ ] **Visualization**: Candlestick charts with overlay indicators in GUI.

## Phase 3: Strategy & Execution
- [ ] **Brain Interface**: Python-Rust bridge for model inference.
- [ ] **Risk Engine**: Pre-trade checks (Max DD, Leverage).
- [ ] **Order Manager**: State machine for order lifecycle (New -> Filled).
- [ ] **Paper Trading**: Simulation mode using real-time data.

## Phase 4: Production Hardening
- [ ] **Observability**: Prometheus metrics / Grafana dashboard.
- [ ] **Remote Control**: Authenticated gRPC for remote management.
- [ ] **Kubernetes/Docker**: Containerization for cloud deployment.
- [ ] **Live Execution**: Real money trading.
