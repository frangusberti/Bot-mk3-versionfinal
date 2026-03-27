# Trade Ledger Uniqueness Audit Report

**Date:** 2026-03-16  
**Dataset:** `stage2_eval` (Replay)  
**Trades:** 200 | **Fills:** 1000

## Verdict: NOT A BUG — DEGENERATE REPLAY DATA

### Root Cause

The `stage2_eval` dataset is a **fixed, short replay segment** of ~18 ticks of BTCUSDT market data. Every episode reset in the Rust `bot-server` starts at **tick 0 of that same segment**. Because the replay is deterministic:

1. The same 4 limit orders sit in the book at the same prices
2. The same BBO crossing happens at the same timestamps
3. The forced-close fill at episode end hits the same mid_price

This means every episode produces fills at **identical prices** (`68837.75`, `68839.61`), with **identical quantities** (`0.02905x`), resulting in **identical VWAP entry/exit** and **identical PnL**.

### Evidence

| Column | Unique Values | Verdict |
|---|---|---|
| `trade_id` | 200 | ✅ OK (unique UUIDs) |
| `episode_id` | 35 | ✅ OK (multiple episodes) |
| `step_open` | 200 | ✅ OK (incrementing) |
| `step_close` | 200 | ✅ OK (incrementing) |
| `side` | **1** | ⚠️ DEGENERATE |
| `avg_entry_price` | **1** | ⚠️ DEGENERATE |
| `avg_exit_price` | **1** | ⚠️ DEGENERATE |
| `entry_qty` | **1** | ⚠️ DEGENERATE |
| `net_pnl` | **1** | ⚠️ DEGENERATE |
| `fees_total` | **1** | ⚠️ DEGENERATE |
| `open_time_event` | **1** | ⚠️ DEGENERATE |
| `close_time_event` | **1** | ⚠️ DEGENERATE |

### Answers to User's Questions

1. **Is it normal?** YES, given the replay constraint. Every episode replays the exact same 18-tick market snapshot. Deterministic replay = deterministic fills = identical trades.

2. **Is there an export or consolidation bug?** NO. The `trade_id` column has 200 unique UUIDs. The `step_open`/`step_close` columns increment correctly. The auditor is correctly creating separate trade records — they just happen to have identical economic content because the underlying market data is identical.

3. **Is the TXT repeating rows by error?** NO. The TXT faithfully reflects the CSV. The CSV also has 200 rows with 1 unique pattern.

4. **Does `trade_audit_full.csv` also show this?** YES. Both the TXT and the CSV show the same single repeated pattern. This is the data, not a rendering bug.

### How to Fix This

To get **diverse** trade data, one of these must change:

- **Use a longer/varied dataset** (e.g. `stage2_train` instead of `stage2_eval`, or merge multiple data windows)
- **Use randomized episode start offsets** within the replay (requires Rust-side `ReplayConfig` change to support `random_start_offset`)
- **Run against the live paper-trading endpoint** instead of deterministic replay

The auditor code itself is correct and will produce diverse output as soon as the underlying market data varies across episodes.
