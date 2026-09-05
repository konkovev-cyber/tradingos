# Sprint Status — 2026-07-11 14:00 UTC

## Active Experiments

| ID | Type | Symbol | TF | Runtime | Signals | Trades | Status |
|----|------|--------|----|---------|---------|--------|--------|
| LS-001 | Live Shadow | BTCUSDT | 5m | 16h | 3 | 3 | COLLECTING |
| MR-001 | Live Shadow | BTCUSDT | 5m | 1h | 2 | 2 | COLLECTING |
| VOL-001 | Live Shadow | BTCUSDT | 15m | 1h | 0 | 0 | WARMING |
| TF-001 | Live Shadow | BTCUSDT | 1h | 1h | 0 | 0 | WARMING |
| MR-001 | Deep Replay | BTCUSDT | 5m | 40min | — | — | FETCHING (210k+ candles) |

## Infrastructure Health

| Component | Status |
|-----------|--------|
| All screen sessions | ✅ 5/5 alive |
| Errors (all logs) | 0 |
| Bybit testnet | ✅ Connected |
| Data Lake | ✅ Writing |

## New Tools Added This Session

| Tool | File | Purpose |
|------|------|---------|
| Strategy Passport 2.0 | `research/strategies/MR-001_passport.md` | Standardized strategy spec with kill conditions |
| Research Score | `research/research_score.py` | Unified ranking (PF + stability + sample + DD + WF + simplicity) |
| Walk Forward | `research/walk_forward.py` | Multi-window train/test validation |
| Monte Carlo | `research/monte_carlo.py` | 10k shuffle simulation, ruin probability |
| Demo Gate | `research/demo_gate.md` | Pre-demo checklist with 4 gates |
| Economic Filter | Built into `research_replay.py` | 0.1% fee + 0.05% slippage per trade |

## Next Actions

1. Wait for MR-001 deep replay to complete
2. Run VOL-001 and TF-001 replays
3. Rank all 4 strategies by Research Score
4. Select candidate(s) for MT5 Demo
