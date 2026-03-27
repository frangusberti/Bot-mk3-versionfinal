# Offline RL Training Pipeline

This document outlines the complete workflow for training the RL agent on historical data using the new Offline Pipeline.

## 1. Data Recording

The new Multi-Symbol Recorder captures L2 market data and saves it to Parquet files.

**Running the Recorder:**
1. Open the Bot GUI (`python python/bot_gui/main.py`).
2. Go to `Recorder` tab.
3. Enter symbols (e.g., `BTCUSDT,ETHUSDT`).
4. Set `Rotation Interval` (default 60 min).
5. Click **Start Recording**.

**Automatic Processing:**
- Every 60 minutes, the recorder rotates to a new `run_id`.
- The previous run is automatically stopped and **normalized**.
- Normalized datasets are added to `data/index/datasets_index.json`.

## 2. Training (Offline)

You can train the PPO agent on the collected historical data. The system uses "Episode Windowing" to slice long datasets into manageable chunks (e.g., 30-minute windows).

**Using the GUI:**
1. Go to the new **Training** tab.
2. Configure parameters:
   - **Symbols**: `BTCUSDT`
   - **Total Steps**: `1,000,000` (recommended for initial test)
   - **Window Size**: `1800` (30 mins)
   - **Stride**: `300` (5 mins overlap)
3. **Run with Focus Mode** (Recommended): Check "Training Focus Mode" to disable other tabs and save CPU.
4. Click **Start Offline Training**.
5. Watch the logs. The GUI runs `python/bot_ml/offline_train.py` in the background.

**Using CLI:**
```bash
python python/bot_ml/offline_train.py --symbol BTCUSDT --steps 1000000 --window 1800 --threads 6
```

Logs and models are saved to `python/runs_train/<run_name>/`.

## 3. CPU Optimization Mode (Module 6.1)

To prevent system freezing during training, the pipeline implements several optimizations:

### Thread Limiting
- Automatically detects CPU cores and uses only 50% (max 8) for PyTorch.
- Sets `OMP_NUM_THREADS` and `MKL_NUM_THREADS` to avoid thread contention.

### Reduced Overhead
- **Logging**: Console output is throttled to once every 1000 steps.
- **Garbage Collection**: Forced `gc.collect()` runs every ~5000 steps to keep RAM stable.
- **Process Priority**: Training runs with `BELOW_NORMAL_PRIORITY_CLASS` on Windows.

### Focus Mode (GUI)
- When enabled in the Training tab, all other tabs (Recorder, Replay, Features, System, Analytics) pause their background updates.
- This ensures the GUI stays responsive even under heavy load.

## 4. Evaluation

To verify the model's performance on held-out windows (or the same windows):

**CLI Only (currently):**
```bash
python python/bot_ml/offline_eval.py --model_path python/runs_train/<run_name>/final_model --symbol BTCUSDT
```

This will run deterministic episodes and print:
- Average Reward
- Win Rate
- Final Equity

## 5. Key Components

- **Episode Builder**: `python/bot_ml/episode_builder.py` — Reads `datasets_index.json` and generates windows.
- **Window Environment**: `python/bot_ml/window_env.py` — Gym wrapper that cycles through windows.
- **RL Service**: The Rust backend handles the replay simulation (`ReplayEngine`) with accurate order book reconstruction.

## 6. Troubleshooting

- **No episodes found**: Check `data/index/datasets_index.json`. Ensure `usable_for_backtest` is true.
- **RAM Usage High**: Reduce `--batch-size` or `--n-steps` in `offline_train.py`.
- **System Freeze**: Ensure "Low Priority Mode" is checked in the GUI.
