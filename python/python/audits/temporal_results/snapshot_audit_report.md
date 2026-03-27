# Snapshot Causal Audit Report

Timestamp: 2026-03-16T13:33:44.753978

## Causal Integrity Checks

| Test | Result | Violations |
|------|--------|------------|
| Monotonic Timestamps | ✅ PASS | 0 |
| No Future Data (Age >= 0) | ✅ PASS | 0 |
| Continuity (Step Gap < 2s) | ⚠️ WARN | 7 |

**Max Step Gap:** 2212.0 ms
