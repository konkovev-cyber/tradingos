# TradingOS Ownership Control

> Every action must have an owner. No orphaned trades.

---

## ACTIVE EXECUTOR

```
NAME:       Executor v0
PID:        OFFLINE (API 401)
PATH:       /root/trading_brain_v4/research/execution/executor_v0.py
VERSION:    v0 (build 2026-07-24)
EXCHANGE:   Bybit
CAN TRADE:  YES (when API works)
```

**Authority:**
- Open positions on Bybit
- Modify TP/SL on Bybit positions
- Close positions on Bybit

**NO OTHER COMPONENT HAS THIS AUTHORITY.**

---

## OWNER PER COMPONENT

| Component | PID | Can Open | Can Modify TP/SL | Can Close | Notes |
|-----------|-----|----------|-------------------|-----------|-------|
| Executor v0 | OFFLINE | YES | YES | YES | Sole authority on Bybit |
| Guardian | — | NO | NO | NO | Blocks only, DRY RUN |
| Position Guard | 1998237 | NO | NO | NO | BingX observation only |
| Telegram Bot | 1993950 | NO | NO | NO | Notifications only |
| Kernel | 1570009 | NO | NO | NO | Monitoring only |
| Collectors | various | NO | NO | NO | Data pipeline |
| Research Runners | various | NO | NO | NO | Shadow/paper only |
| ubot (legacy) | DEAD | **NEVER** | **NEVER** | **NEVER** | DO NOT RESTART |
| ubot_bingx (legacy) | DEAD | **NEVER** | **NEVER** | **NEVER** | DO NOT RESTART |

---

## POSITION OWNERSHIP

### Bybit

| Symbol | Owner | Entry | Status |
|--------|-------|-------|--------|
| DOGEUSDT | Executor v0 | 0.06966 | Open — API 401 blocks verification |

**Rule:** Only Executor v0 may modify this position.

### BingX

No open positions.

---

## CONFLICT DETECTION

If multiple executors are found trading simultaneously:

```
MULTIPLE EXECUTORS DETECTED
ACTION REQUIRED

1. Identify all trading processes
2. Determine which is authorized
3. Stop unauthorized executor
4. Log incident in INCIDENTS.md
5. Notify user immediately
```

**Never silently resolve executor conflicts.**

---

## HOW TO CHECK OWNERSHIP

```bash
# Who owns Bybit trading?
grep -r "CAN TRADE: YES" /root/tradingos/memory/OWNERSHIP.md

# Is executor running?
pgrep -f "executor_v0.py"

# Who created a position?
cat /root/tradingos/memory/AI_CONTEXT.md | grep -A5 "Current Positions"
```

---

## OWNERSHIP TRANSFER

If ownership needs to change (e.g., new executor version):

1. Log decision in DECISIONS.md
2. Update this file
3. Update AI_CONTEXT.md
4. Update CURRENT_STATE.md
5. Notify user

**Never transfer ownership without logging.**

---

*Last updated: 2026-07-25*
