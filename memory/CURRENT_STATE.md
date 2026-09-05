# TradingOS Current State

> Updated: 2026-07-25 18:30 UTC

---

## System Status: GREEN (Bybit API restored 2026-07-27)

| Category | Status | Detail |
|----------|--------|--------|
| Kernel | 🟢 GREEN | Running 9 days, stable |
| Executor v0 | 🟢 GREEN | API working (X-BAPI-API-KEY header fix) |
| Guardian | 🟡 YELLOW | Dry run only |
| Position Guard | 🟢 GREEN | BingX observation active |
| Telegram Bot | 🟢 GREEN | Notifications working |
| Data Pipeline | 🟢 GREEN | All collectors active |
| API (Bybit) | 🟢 GREEN | Working with FyZUY69SLCbYUqw2jM |
| API (BingX) | 🟢 GREEN | Working, 0 positions |
| Production Hardening | 🟢 GREEN | v1 complete, 29/29 tests passing |

---

## Services

| Service | PID | Uptime | Status |
|---------|-----|--------|--------|
| tradingos.service | 1570009 | 9d 16h | 🟢 Running |
| position-guard.service | 1998237 | 4d 19h | 🟢 Running |
| trading-control.service | 1993950 | 5d 18h | 🟢 Running |
| market-agent-dashboard | 1600587 | 9d 5h | 🟢 Running |
| ubot-watchdog.service | — | — | 🔴 Failed |

---

## Trading

**Mode:** MICRO LIVE (acceptance testing)
**Active Executor:** Executor v0 (OFFLINE due to API 401)
**Acceptance Progress:** 0/30 trades

---

## Positions

### Bybit (live data 2026-07-27)
```
DOGEUSDT BUY 100 @ 0.06966
Mark: 0.07297
PnL: +$0.331 (+4.77%)
SL: 0.0650 | TP: 0.0750
Leverage: 3x
STATUS: OPEN — VERIFIED VIA API
```

### BingX (legacy)
```
No open positions
```

---

## API Status

| Exchange | Key Source | Status | Last OK |
|----------|-----------|--------|---------|
| Bybit (executor_v0) | /root/trading_brain_v4/research/execution/.env | 🔴 401 | Unknown |
| Bybit (ubot_legacy) | /opt/ubot/.env | 🔴 401 | Unknown |
| BingX (ubot_bingx) | /opt/ubot_bingx/.env | 🟢 OK | 2026-07-25 |

---

## Last Incident

**Date:** 2026-07-25
**Problem:** Bybit API 401 on both API keys
**Impact:** Cannot check positions, modify TP/SL, or open new trades
**Fix pending:** User must verify API key in Bybit dashboard

## Acceptance Status

| Test Category | Status |
|---------------|--------|
| Offline Safety Tests | ✅ 29/29 PASS |
| API Authentication | ❌ BLOCKED (401) |
| Exchange Reconciliation | ❌ BLOCKED |
| Live Execution | ❌ BLOCKED |
| Micro Live Gate | ❌ BLOCKED |

---

## Next Action

1. User verifies Bybit API key permissions (IP whitelist, key status)
2. Once API works: check DOGEUSDT position, consider TP adjustment
3. Implement adaptive TP validator (ATR-based max distance)
4. Resume Executor v0 acceptance series
