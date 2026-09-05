# TradingOS Operations Manual

> Rules for ANY AI agent working on this system.
> Violation = immediate task failure.

---

## MANDATORY STARTUP SEQUENCE

Before ANY action, execute:

```
1. READ /root/tradingos/memory/AI_CONTEXT.md
2. READ /root/tradingos/memory/CURRENT_STATE.md
3. CHECK active executor (which PID, which exchange)
4. CHECK open positions
5. CHECK API status
6. OUTPUT:
   MEMORY LOADED
   Current Executor: [name] [status]
   Current Mode: [mode]
   Current Risks: [list]
```

---

## RULES

### Trading
1. **Only Executor v0 can open/modify/close positions on Bybit**
2. **Never restart ubot or ubot_bingx** — they are legacy, DOWN intentionally
3. **Never modify TP/SL without checking CURRENT_STATE.md first**
4. **Never change API keys without user confirmation**
5. **Never run `rm -rf` or destructive git operations**

### Code Changes
6. **Never modify trading code without explicit approval**
7. **Never add new strategies during acceptance testing**
8. **Never create new bots/daemons without approval**
9. **Always use virtual environments** (/root/trading_brain_v4/.venv)

### Communication
10. **Always state what you're about to do before doing it**
11. **Always confirm before actions visible to others** (push, deploy, API calls)
12. **Never guess API keys or credentials** — read from .env files

### Memory
13. **Update CURRENT_STATE.md at end of session**
14. **Log new incidents in INCIDENTS.md**
15. **Log new decisions in DECISIONS.md**

---

## PROCESS OWNERSHIP

| Process | Owner | Can Modify Money | Can Restart |
|---------|-------|-----------------|-------------|
| Executor v0 | User + AI (with approval) | YES | YES |
| Guardian | System | NO (blocks only) | YES |
| Position Guard | System | NO | YES |
| Telegram Bot | System | NO | YES |
| Kernel | System | NO | YES |
| Collectors | System | NO | YES |
| Research Runners | System | NO | YES |
| ubot (legacy) | DEAD | NO | **NEVER** |
| ubot_bingx (legacy) | DEAD | NO | **NEVER** |

---

## EMERGENCY PROCEDURES

### If API returns 401
1. Do NOT retry endlessly
2. Log incident in INCIDENTS.md
3. Notify user: "Bybit API 401 — need key verification"
4. Continue with other tasks (BingX, code review, etc.)

### If position is losing > 5%
1. Do NOT auto-close
2. Check CURRENT_STATE.md for who owns the position
3. Recommend action to user
4. Let user decide

### If Guardian blocks a trade
1. Do NOT bypass guardian
2. Log the block reason
3. Explain to user why it was blocked
4. Wait for user decision

---

## QUICK COMMANDS

```bash
# Full system status
cat /root/tradingos/memory/CURRENT_STATE.md

# Who is trading
ps aux | grep -E "executor|trading" | grep -v grep

# Check positions (if API works)
cd /root/trading_brain_v4 && .venv/bin/python3 -c "from exchange.bybit.client import BybitClient; ..."

# Recent decisions
cat /root/tradingos/memory/DECISIONS.md

# Recent incidents
cat /root/tradingos/memory/INCIDENTS.md

# API health test
curl -s "https://api.bybit.com/v5/market/time" | python3 -m json.tool
```
