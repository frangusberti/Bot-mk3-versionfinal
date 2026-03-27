# Health Keys & Status Board

## Philosophy
**Aggregation Logic:**
- If ANY Child is **Critical** -> Parent is **Critical**.
- If ANY Child is **Warning** -> Parent is **Warning** (unless Critical).
- Else -> Parent is **Healthy**.

## Health Tree
- **System**
    - **Connectivity**
        - `ExchangeWS` (Binance/Bybit)
        - `ExchangeREST` (Binance/Bybit)
    - **DataPipeline**
        - `Recorder` (Disk Space, Write Errors)
        - `FeatureEngine` (Lag < 5ms)
    - **Execution**
        - `RiskEngine` (Drawdown, Leverage)
        - `OrderManager` (Unreconciled Orders)
    - **Intelligence**
        - `Brain` (Inference Latency < 50ms)

## Status Codes
- **Healthy (Green):** Normal operation.
- **Degraded (Yellow):** Performance issue, non-critical error (e.g., high latency, minor data gap recovered).
- **Critical (Red):** Immediate stop required (e.g., Exchange disconnected, Risk limit breached).
