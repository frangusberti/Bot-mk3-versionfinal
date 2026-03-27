# Architecture: Bot Mk3 (Module 0)

## Overview
Status: **Draft (Module 0)**
Date: 2026-02-14

**Core Philosophy:**
1.  **L2 Only:** Orderbook is reconstructed from Snapshots + Deltas. No L3 dependency.
2.  **1m Context:** Decision making happens on 1m boundaries, but execution is tick-aware.
3.  **Net Profitability:** Every trade must have positive expected value after fees (taker ~0.045%).
4.  **Determinism:** Replay must match Live execution exactly.

## Data Plane (Rust)
**Source -> Normalizer -> EventBus -> Recorder**

### 1. Market Data Stream (`bot-data`)
- **Websocket:** Connects to Binance/Bybit.
- **Normalization:** Converts raw JSON/Protobuf to internal `MarketEvent`.
- **Packet Gap Detection:** If a sequence number is skipped:
    1.  Flag `DataHealth` as Warning.
    2.  Buffer incoming packets.
    3.  Request REST Snapshot.
    4.  Rebuild book.
    5.  Resume.

### 2. Recorder (`bot-recorder`)
- **Format:** Parquet.
- **Partitioning:** `symbol/date/type`.
- **Buffering:** In-memory batching (e.g., 100ms or 1000 events) -> Zero-copy write to disk.

### 3. Frame Builder (`bot-features`)
- **Input:** Stream of `MarketEvent`.
- **Output:** `Frame` (OHLCV + Aggregated Metrics).
- **Timeframe:** 1m (Primary).
- **Latency:** Must emit `< 5ms` after candle close.

## Control Plane (Rust -> Tauri)
**Health -> Status Board -> GUI**

### 1. Status Board (`bot-core::health`)
- **Structure:** Tree.
- **Aggregation:** `Child Error => Parent Warning/Error`.
- **Heartbeats:** Components must "check-in" every N seconds.

### 2. GUI (Tauri)
- **Role:** Visualization & Manual Control.
- **Communication:** Front-end polls state or receives push via Tauri Events.
- **Security:** Localhost only.

## Execution Plane (Rust)
**Brain -> Risk -> OrderManager -> Exchange**

### 1. Risk Engine (`bot-engine`)
- **Gatekeeper:** Checks leverage, max drawdown, open orders.
- **Latency:** Microsecond scale.
- **Pre-Trade Checks:**
    - `Est. Fee > Std. Deviation`?
    - `Slippage Tolerance` met?

### 2. Order Manager
- **State:** Tracks every order lifecycle (New -> Partial -> Fill).
- **Reconciliation:** Periodically queries REST to match WS events.
