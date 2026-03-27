# Bot Mk3: PPO & Audit Workflow Guide

Following the recent backend upgrades (7-action space, Maker Alpha rewards, gRPC sync), here is how you can manage the RL pipeline manually.

## 1. Where is the Training Data?
The environment pulls data from the `runs/` directory.
- **Main Dataset**: `runs/stage2_train/`
- **Evaluation Dataset**: `runs/stage2_eval/`
- **Structure**: Each dataset folder must contain a `normalized_events.parquet` and a `dataset_manifest.json`.

## 2. How to Run PPO Manually (CLI)
If you want to train without AI assistance, you can use the pilot script directly:

```powershell
# Open a terminal in c:\Bot mk3
python python/pilot_retrain_ppo.py --dataset_id stage2_train --train_steps 50000
```

- **Output**: Models are saved to `python/runs_train/pilot_stage2_train/`.
- **Logs**: TensorBoard logs are written to the same directory. You can view them with:
  `tensorboard --logdir python/runs_train/`

## 3. How to Obtain the Audit
After training, you MUST audit the results using the **Maker Alpha Scorecard**:

```powershell
python python/maker_validation_harness.py --model python/runs_train/pilot_stage2_train/pilot_model.zip --dataset stage2_eval --steps 5000
```

This will print a table with:
- **Maker Ratio**: (Goal: >80%)
- **Toxic Fill Rate**: (Goal: <15%)
- **Net PnL**: Actual economic performance under a conservative (Full Queue) model.

## 4. GUI & Backend Workflow Integration
Since the React frontend is currently skeletal, the workflow is "Hybrid":

1. **Config**: (GUI/File) Set risk params in `server_config.toml`.
2. **Train**: (CLI) Run `pilot_retrain_ppo.py`.
3. **Audit**: (CLI) Run `maker_validation_harness.py`.
4. **Deploy**: The GUI's "Live" tab (once implemented) will point to the `pilot_model.zip` in the `models/` registry.

> [!IMPORTANT]
> Because PPO is computationally expensive and requires real-time logging, the CLI remains the most reliable interface for training until the React "Training Lab" components are fully bound to the gRPC service.
