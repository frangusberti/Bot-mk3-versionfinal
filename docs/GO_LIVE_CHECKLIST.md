# GO LIVE CHECKLIST (Risk/Orchestrator)

## Objetivo
Checklist mínimo para pasar de paper a live con controles operativos verificables.

## Requisitos bloqueantes
- [ ] `cargo fmt --all -- --check` en CI
- [ ] Tests de riesgo críticos en CI:
  - [ ] `reduce_only_detects_closing_trade`
  - [ ] `leverage_apply_requires_cooldown_and_delta`
  - [ ] `test_order_rate_exceeded`
- [ ] Tracking actualizado en `TRACKING_CAMBIOS_RISK_ORCHESTRATOR.txt`
- [ ] Sin desync de orderbook sostenido (> 5 min) en paper
- [ ] Sin errores críticos repetidos de inferencia/policy

## Validaciones operativas recomendadas
- [ ] Rechazos de riesgo auditados por razón (health/leverage/rate/notional)
- [ ] latencia de inferencia estable
- [ ] reconcile de órdenes sin divergencias relevantes
- [ ] replay vs paper/live con comportamiento consistente

## Comandos de validación rápidos
```bash
cargo fmt --all -- --check
cargo test -p bot-server reduce_only_detects_closing_trade -- --nocapture
cargo test -p bot-server leverage_apply_requires_cooldown_and_delta -- --nocapture
cargo test -p bot-server test_order_rate_exceeded -- --nocapture
```
