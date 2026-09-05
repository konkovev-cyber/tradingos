# TradingOS v2 — Operational Rollout Plan

## Дата: 2026-07-05

## Фазы эксплуатации

### Фаза 0: System Lock (текущая)
- [x] Архитектура завершена (11 модулей)
- [x] CLI работает (25+ команд)
- [x] BTC Phase3 shadow собирает данные
- [ ] Full replay mode на истории
- [ ] Stability verification (T7 drift check)
- [ ] Execution shadow test (PAPER_LIVE)

### Фаза 1: MICRO LIVE
- [ ] First 100 Trades Protocol
- [ ] 5 USDT max per trade
- [ ] 1-2 позиции max
- [ ] Только BTC/ETH
- [ ] Hourly monitoring
- [ ] Autostop triggers active

### Фаза 2: Behavioral Calibration
- [ ] T7 learning freeze на 48 часов
- [ ] Threshold calibration
- [ ] Real regime mismatch analysis
- [ ] Slippage model calibration

### Фаза 3: Controlled Scaling
- [ ] Capital Release Framework active
- [ ] Stability/Multiplier/Time formulas
- [ ] Stage transitions with gates
- [ ] Auto-rollback on regression

### Фаза 4: Portfolio Expansion
- [ ] Add SOL, BNB, XRP
- [ ] Correlation stability check
- [ ] Portfolio Governor as primary brain

### Фаза 5: Full Scale
- [ ] 200+ trades verified
- [ ] Stable regime transitions
- [ ] No execution anomalies
- [ ] Full capital deployment

## Key Documents
- FIRST_100_TRADES_PROTOCOL.md
- CAPITAL_RELEASE_FRAMEWORK.md
- LIVE_FAILURE_PREDICTION_MAP.md
- ARCHITECTURE.md

## Current Status
- Architecture: COMPLETE
- Simulation: COMPLETE
- Live Readiness: PAPER stage
- Capital Exposure: 0% (not yet)
