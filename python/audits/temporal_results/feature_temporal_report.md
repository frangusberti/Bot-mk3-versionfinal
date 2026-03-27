# Feature Temporal Audit Report

Generated: 2026-03-16T13:14:20.098645

## Summary Metrics

- Total Samples: 1001
- Book Age (p50/p95): 0.0 / 10.0 ms
- Trades Age (p50/max): 123.0 / 1868.0 ms
- Obs Quality (avg/min): 1.0000 / 1.0000

## Evaluation against Contract

| Requirement | Metric | Result |
|-------------|--------|--------|
| Book Freshness (p95 < 50ms) | 10.0ms | ✅ PASS |
| Obs Quality (Avg > 0.99) | 1.0000 | ✅ PASS |
