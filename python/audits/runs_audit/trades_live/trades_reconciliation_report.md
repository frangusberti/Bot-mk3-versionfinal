# BotMK3 Trade Ledger Reconciliation Report

**STATUS: PASS**

## 1. Accounting Semantics
- `trade_pnl_effect`: The isolated, mathematically strict net PnL produced explicitly by tracking this trade sequence (Gross - Fees).
- `account_equity_before` / `account_equity_after`: Snapshots of the total portfolio equity at the exact ticks the entry and exit fills were completed. As defined by Opción B, `account_equity_change` includes Mark-to-Market residual of open inventory, funding rates, and unrelated fees that may happen during the holding period, so it is NOT functionally expected to equal `trade_pnl_effect` 1:1.

## 2. Validation Checks
- **Trade Closure Math:** `gross_pnl - total_fees == net_pnl` (Strictly enforced at finalization)
- **Qty Consistency:** `entry_qty == exit_qty` (Enforced up to 1e-6 tolerance)
- **Timeline Integrity:** `holding_time_ms >= 0`

## 3. Results Summary
- **Total Consolidate Trades:** 50
- **Total Raw Fills Logged:** 107
- **Systematic Errors [FAIL]:** 0
- **Warnings [WARN]:** 0

