# Run Layout Documentation

## Directory Structure
The bot organizes runtime artifacts as follows:

```
/run
├── /logs                   (Text logs, rotated daily)
│   ├── bot-main.log
│   └── bot-error.log
├── /data                   (Market Data Lake)
│   ├── /raw
│   │   └── /BTCUSDT
│   │       └── /2023-10-27
│   │           └── trades_1000.parquet
│   └── /processed          (Normalized/Features)
├── /models                 (Trained Brains)
│   ├── /v1.0.0
│   │   ├── model.pt
│   │   └── manifest.json
├── /config                 (Configuration)
│   ├── main_config.toml
│   └── secrets.toml
└── /state                  (Persistence)
    └── system_state.json   (Last processed ID, open orders)
```

## Execution Flow
1.  **Startup:** Load config -> Check Health -> Connect to Exchange.
2.  **Runtime:** Stream Data -> Record -> Feature Engine -> Brain -> Execution.
3.  **Shutdown:** Cancel Open Orders (configurable) -> Flush Recorder -> Exit.
