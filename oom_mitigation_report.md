# OOM Mitigation Report & Low-Memory Workflow

This document addresses the repeated Out-Of-Memory (OOM) crashes experienced during Phase 27.

## 1. Top 3 OOM Causes (Likely)

1.  **Audit Accumulation**: [ppo_eval_checkpoint.py](file:///C:/Bot%20mk3/python/ppo_eval_checkpoint.py) was accumulating 10k+ entries in Python lists (`actions`, `inventory_history`, `trades`) during every checkpoint audit. These high-frequency lists caused peak memory spikes, especially when multiple evaluations ran concurrently.
2.  **Context Bloat**: Loading the full `implementation_plan_grand_slam.md` and historical session logs during agent reasoning cycles.
3.  **Audit Frequency**: Running two 10k-step audits (Baseline/Adversarial) every 50k steps created periodic 400MB+ memory peaks.

## 2. Mitigations Implemented

-   **[ppo_eval_checkpoint.py](file:///C:/Bot mk3/python/ppo_eval_checkpoint.py)**: Refactored to use **Incremental Aggregation**.
    -   Lists replaced by `collections.Counter` and running averages.
    -   Removed `inventory_history` list; using `avg_qty`, `min`, `max` trackers instead.
    -   Removed `trades` and `mtm_history` list accumulation; causal breakdown now updates incrementally.
-   **Execution Mode**: Transitioned to **Memory-Safe Mode** (Minimized file loading).

## 3. Recommended Next Steps

-   **Audit Step Reduction**: Reduce `steps_per_eval` from 10,000 to **5,000** in [ppo_vnext_p3_5.py](file:///C:/Bot%20mk3/python/ppo_vnext_p3_5.py) to halve peak audit memory.
-   **Report Splitting**: Break `walkthrough.md` into `walkthrough_phase27.md` to prevent file-loading OOM.

## 4. Low-Memory Workflow (Operational Protocol)

1.  **Scoped Prompts**: Avoid "Check everything". Use "Check file X for Y".
2.  **Summarized Loads**: Use `view_content_chunk` or `grep` instead of full `view_file` for files > 500 lines.
3.  **Incremental Reporting**: Checkpoints should be summarized in the chat; save the full forensic JSON to disk instead of printing it all at once.
4.  **Process Management**: Ensure Orphaned `bot-server` processes are killed before starting new runs to free up port/socket memory.

## 5. Minimal Safe Workflow for Next Report

1.  Restart Server on 50053.
2.  Run [ppo_vnext_p3_5.py](file:///C:/Bot%20mk3/python/ppo_vnext_p3_5.py) with `steps_per_eval=5000`.
3.  Request "Checkpoint Summary" (just the scorecard table) instead of the full audit JSON.
