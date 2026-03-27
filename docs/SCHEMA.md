# Schema Documentation (v1.0.0)

## Overview
**Version:** 1.0.0
**Serialization:** JSON (over Websocket/REST), Parquet (Storage), Arrow (IPC).
**Versioning Strategy:** Semantic Versioning.

## Market Data Events
### 1. MarketEvent (Enum)
Wrapper for all inbound market data.
```json
{
  "type": "Trade" | "BookSnapshot" | "BookDelta",
  "data": { ... }
}
```

### 2. Trade
Public execution on the exchange.
```json
{
  "exchange": "Binance",
  "symbol": "BTCUSDT",
  "trade_id": "123456789",
  "price": "50000.00",
  "quantity": "0.001",
  "side": "Buy",
  "is_liquidation": false,
  "timestamp": "2023-10-27T10:00:00.123Z"
}
```

### 3. BookSnapshot (L2)
Full depth of the order book (top 50 or 100 levels).
```json
{
  "exchange": "Binance",
  "symbol": "BTCUSDT",
  "last_update_id": 100020,
  "bids": [["50000.00", "1.2"], ["49999.00", "0.5"]],
  "asks": [["50001.00", "0.8"], ["50002.00", "2.0"]],
  "timestamp": "2023-10-27T10:00:00.100Z"
}
```

### 4. BookDelta (L2 Update)
Changes to specific price levels.
```json
{
  "exchange": "Binance",
  "symbol": "BTCUSDT",
  "u": 100021, // Final Update ID
  "U": 100021, // First Update ID
  "b": [["50000.00", "0.0"]], // Quantity 0 means remove level
  "a": [["50001.00", "1.5"]],
  "timestamp": "2023-10-27T10:00:00.150Z"
}
```

## Control Events
### 1. Signal (Brain Output)
```json
{
  "id": "uuid-v4",
  "created_at": "...",
  "symbol": "BTCUSDT",
  "direction": "Long" | "Short" | "Flat",
  "confidence": 0.85,
  "est_net_pnl": 0.002, // Estimated Profit Margin after fees
  "metadata": { "model": "v1.2" }
}
```

### 2. HealthReport
Aggregated status of the system.
```json
{
  "status": "Healthy" | "Degraded" | "Critical",
  "components": {
    "ExchangeConn": { "status": "Healthy", "last_heartbeat": 100 },
    "DataFeed": { "status": "Degraded", "error": "Gap detected in sequence" }
  }
}
```
