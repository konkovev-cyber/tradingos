# TradingOS Incidents Log

> Every significant system failure. Format: Date | Problem | Root Cause | Fix | Prevention

---

## INC-2026-0727-001: get_open_orders() returns empty (FIXED 2026-07-27)

**Date:** 2026-07-27
**Problem:** `client.get_open_orders()` returned empty response (no orders visible)
**Root Cause:** Wrong endpoint — used `/v5/order/list` instead of `/v5/order/realtime`
**Fix:**
- `client.py:233`: `/v5/order/list` → `/v5/order/realtime`
- Added `settleCoin=USDT` auto-injection for orders without symbol
- Added `/v5/order/realtime` to `ENDPOINT_METHODS` whitelist in `request_builder.py`

## INC-2026-0725-001: Bybit API 401 (RESOLVED 2026-07-27)

---

## INC-2026-0725-002: TP Unreachable

**Date:** 2026-07-25
**Problem:** DOGEUSDT position has TP at 0.08, which is 14.9% from entry (0.06966). For DOGE volatility, this may never be reached.
**Impact:** Capital frozen. Guardian doesn't receive close event. Statistics distorted.
**Root Cause:** TP set as fixed value without ATR/volatility validation.
**Fix:** Implement adaptive TP validator (ATR-based max distance). Pending.
**Prevention:** All TP calculations must go through volatility check before exchange submission.

---

## INC-2026-0721-001: Main Loop Hang

**Date:** 2026-07-21
**Problem:** Main trading loop hangs on first cycle (get_klines/get_ticker without timeout). Position Management never executes.
**Impact:** Guardian never runs. Positions unprotected.
**Root Cause:** `_process_symbol_srt()` never returns on network timeout.
**Fix:** Extract Position Management Loop as independent asyncio task (engine.py:655).
**Prevention:** All network calls must have explicit timeouts.

---

## INC-2026-0721-002: Scalper Auto-Restart

**Date:** 2026-07-21
**Problem:** scalper-v6.service, market-agent-bot/collector services keep auto-restarting every ~2 min.
**Impact:** Resource waste. Potential ghost trades.
**Root Cause:** systemd restart policy + services not properly stopped.
**Fix:** Disabled systemd services: scalper-v6, market-agent-bot, market-agent-collector, smart-scalper, market-validation-bot.
**Prevention:** Audit systemd services before enabling trading.

---

## INC-2026-0725-001: Bybit API 401 (RESOLVED 2026-07-27)

**Date:** 2026-07-25 (resolved 2026-07-27)
**Problem:** All Bybit API authenticated endpoints returned 401
**Root Cause:** **HEADER TYPO** — Using `X-BAPI-APIKEY` instead of `X-BAPI-API-KEY` (with HYPHEN!)
**Fix:** Updated diagnostic tool with correct Bybit V5 headers:
```python
# CORRECT headers:
headers = {
    'X-BAPI-API-KEY': api_key,      # WITH HYPHEN
    'X-BAPI-TIMESTAMP': timestamp,
    'X-BAPI-SIGN': signature,
    'X-BAPI-RECV-WINDOW': recv_window
}
# CORRECT sign format:
sign_string = f"{timestamp}{api_key}{recv_window}{payload}"
```
**API Key in use:** `FyZUY69SLCbYUqw2jM`
**Verification:** PASS — wallet balance, positions, open orders all accessible

**Lesson learned:** Always reference `/root/trading_brain_v4/exchange/bybit/auth_middleware.py` for header names.

---

## INC-2026-0720-001: Session Amnesia

**Date:** 2026-07-20 (ongoing)
**Problem:** Each new AI session started analysis from scratch. Re-discovering processes, positions, API status every time.
**Impact:** Time wasted. Errors introduced by incomplete context.
**Root Cause:** No persistent memory system.
**Fix:** Created /root/tradingos/memory/ system (AI_CONTEXT.md, CURRENT_STATE.md, DECISIONS.md, INCIDENTS.md, OPERATIONS.md).
**Prevention:** Every AI session reads AI_CONTEXT.md first. Update CURRENT_STATE.md at end of session.
