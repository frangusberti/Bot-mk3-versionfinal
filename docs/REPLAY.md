# Replay Engine Documentation

## Overview
The Replay Engine is a deterministic system for simulating market data feeds from historical datasets. It ensures strict total ordering of events, supports multiple clock modes, and integrates with the bot's gRPC architecture.

## Architecture

### Components
1. **ReplayEngine (`crates/bot-data/src/replay/engine.rs`)**:
   - Core logic for reading, sorting, and emitting events.
   - Uses a `BinaryHeap` (Min-Heap) to merge-sort events from multiple Parquet files (or streams) if needed.
   - Manages state (Running, Paused, Stopped).
   - Handles quality gating via `quality_report.json`.

2. **ReplayService (`crates/bot-server/src/services/replay.rs`)**:
   - gRPC interface for the GUI and external clients.
   - Spawns a dedicated blocking task for the replay loop.
   - Manages control signals (Start, Stop, Pause, Step, Speed).
   - Implements UI sampling to prevent GUI freezing.

3. **BatchedReplayReader (`crates/bot-data/src/replay/reader.rs`)**:
   - Efficiently reads Parquet files in batches.
   - converts Parquet rows to `ReplayEvent` structs.

### Event Structure
Events are lightweight structs optimized for high throughput:
- **Core Fields**: `ts_exchange`, `price`, `quantity`, `side` (used for matching/ticks).
- **Extended Fields**: `mark_price`, `funding_rate`, `open_interest` (optional, for specific streams).
- **Ordering Keys**: `ts_exchange`, `ts_local`, `sequence_id`, `file_part`, `row_index`.

## Total Stable Ordering
Determinism is guaranteed by a strict comparator in `ReplayRow`:

1. **Primary Timestamp (`clock_ts`)**: Derived from `ClockMode` (Exchange, Local, or Canonical).
2. **Secondary Timestamp**: Tie-breaker if primary timestamps match (e.g., if using Local clock, break ties with Exchange TS).
3. **Stream Priority**:
   - `Depth` (Highest)
   - `BookTicker`
   - `AggTrade`
   - `Trade`
   - `MarkPrice`
   - `Funding`
   - `Liquidation`
   - `OpenInterest` (Lowest)
4. **Sequence ID**: Logical sequence from source if available.
5. **File Part Index**: Deterministic order of file loading.
6. **Row Index**: Original row number in the Parquet file.

This ensures that even if two events have identical timestamps, their order is always the same across multiple replay runs.

## Usage

### gRPC API
- **StartReplay**: Initializes engine with a dataset and config.
- **StopReplay**: Terminates the active replay.
- **ControlReplay**: Supports `PAUSE` (0), `RESUME` (1), `STEP` (2), `SET_SPEED` (3).
- **StreamReplayEvents**: Streams events. Supports `ui_sample_every_n` to reduce bandwidth for GUIs.

### Quality Gating
By default, the engine checks `quality_report.json` in the dataset directory.
- If `usable_for_backtest` is `false`, replay is rejected.
- Use `allow_bad_quality = true` in `ReplayConfig` to override this.

### GUI
The Python GUI (`tabs/replay.py`) provides full control:
- Select Dataset.
- Set Clock Mode and Speed.
- Start/Pause/Step/Stop.
- Visual feedback on Time, Price, and Event Rate.

## Configuration (`ReplayConfig`)
- `speed`: Playback speed (1.0 = real-time, 0.0 = paused/max-speed depending on impl, >100 = fast-forward).
- `clock_mode`: 
  - `0` (Exchange): Uses `ts_exchange`.
  - `1` (Local): Uses `ts_local` (reception time).
  - `2` (Canonical): Uses `ts_canonical`.
- `ui_sample_every_n`: Sends only every Nth event to the gRPC stream (backend processes all).
- `ui_max_events_per_sec`: Throttles the stream rate.
