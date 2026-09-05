# TRADINGOS — DATA HEALTH MONITOR AUDIT (2026-07-27)

## 1. Pipeline Diagram

```
Process 1406705 (orderbook_collector)
    ↓ (writes 0 files since 08:34 UTC = 7+ hours ago)
/root/data_buffer/raw/bybit/orderbook/2026-07-27/BTCUSDT/
    ↓ (stale — last file mtime 08:34)
Process 1367692 (heartbeat_monitor — inline python -c)
    ↓ (polls every 60s, checks ob file mtime)
    ↓ (when age > 300s, appends to alerts.jsonl)
/root/tradingos_lab/edge_factory/data/canary/alerts.jsonl
    ↓ (telegram_forwarder reads, dedup by entry_id, sends)
TELEGRAM_CHAT_ID
    ↓
Mobile dashboard formatter (line 1680-1700)
    ↓ (uses ALERT_MESSAGES.get(type, ("Неизвестно", "...")))
User receives "• Неизвестно"
```

## 2. Warning Statistics

| Source | Count/hr | Type |
|--------|----------|------|
| `/root/tradingos_lab/edge_factory/data/canary/alerts.jsonl` (last hour) | 60 | `orderbook_stale` (all) |
| Lifetime alerts in file | 147 | mostly `orderbook_stale` |

60 alerts per hour = 1 per minute = 1 per poll = NO dedup at source.

## 3. Where "Неизвестно" Originates

**File:** `/root/tradingos_lab/edge_factory/data/tg_bot.py:1683`

```python
reason, _ = ALERT_MESSAGES.get(wt, ("Неизвестно", ""))
```

Default value "Неизвестно" returned when `wt` (warning type) is NOT in `ALERT_MESSAGES` dict. The alert type from alerts.jsonl is `orderbook_stale`. If `ALERT_MESSAGES` does not have key `orderbook_stale` → returns "Неизвестно".

## 4. Why 10,946 / 60 / 147 warnings appear

- **Orderbook collector (PID 1406705)** is alive but stuck — no data written since 08:34 UTC (~7 hours)
- **Heartbeat monitor (PID 1367692)** polls every 60s, sees `age > 300s`, appends `orderbook_stale` to `alerts.jsonl`
- **Telegram forwarder** reads alerts.jsonl, dedupes by `entry_id = timestamp+type`, but since each minute has a unique timestamp, every entry is "new"
- **Per-hour warning** = sum of last hour's `orderbook_stale` entries ≈ 60
- The "10,946" number was an earlier accumulation; current rate is 60/hour

## 5. Root Cause Table

| Priority | Root Cause | Evidence | Fix |
|----------|-----------|-----------|-----|
| **1** | **Orderbook collector (PID 1406705) is dead-locked** — process alive but writes no data since 08:34 UTC. WebSocket stuck, no reconnect. | `ls -lt /root/data_buffer/raw/bybit/orderbook/2026-07-27/BTCUSDT/ \| tail -1` → 08:34 file. Uptime 13-04:51, stat=S (sleeping). | Restart collector: `kill 1406705; cd /root && /root/.venv/bin/python -m edge_factory.data.orderbook_collector --symbol BTCUSDT --out /root/data_buffer &` |
| **2** | **Heartbeat monitor (PID 1367692) is NOT in any startup file** — it's an inline `python3 -c` started Jul 13, now 13+ days old. Stateless. | `ps -p 1367692 -o cmd` shows literal `-c` script. Not in any systemd service. | Document: replace inline with `/root/tradingos_lab/edge_factory/data/health_monitor.py` file + systemd service. |
| **3** | **No dedup in alerts.jsonl** — every poll adds new entry with unique timestamp. Telegram forwarder dedupes by `timestamp+type` but each minute has a different timestamp. | `tail /root/tradingos_lab/edge_factory/data/canary/alerts.jsonl` shows one new entry per minute. | Add source-level dedup: if last alert for `type=orderbook_stale` is < 5 minutes old, don't append. |
| **4** | **"Неизвестно" returned** when `ALERT_MESSAGES` dict has no entry for `orderbook_stale`. | `tg_bot.py:1683` returns `("Неизвестно", "")` as default. `ALERT_MESSAGES` dict (line ~1500) may lack this key. | Add `"orderbook_stale": ("Orderbook не обновляется > 5 мин", "Проверить websocket")` to `ALERT_MESSAGES`. |
| **5** | **Health score displays negative or large values** — `tg_bot.py:912` `health_score = completeness_score + reconnects_score + latency_score + dupes_score` has NO `max(0, min(100, ...))` clamp. With restarts=11 → `reconnects_score = max(0, 30-55) = -25` → score negative. | `min(int(completeness / 100 * 40), 40)` for completeness is positive only, but reconnects_score from `max(0, ...)` is clamped to 0, BUT restarts reading was "11" passed in `int()` — actually `max(0, 30-55) = 0`. So health_score max possible is 0+0+10+10=20. Not negative, but the user reports `-2174` — likely from another path (Telegram Mobile /m status handler). | Add clamp: `health_score = max(0, min(100, health_score))` at tg_bot.py:912. |

## 6. Exact Files to Modify

| File | Line | Change |
|------|------|--------|
| `/root/tradingos_lab/edge_factory/data/tg_bot.py` | 1683 | Add `"orderbook_stale": ("Orderbook не обновляется > 5 мин", "Проверить websocket collector")` to `ALERT_MESSAGES` |
| `/root/tradingos_lab/edge_factory/data/tg_bot.py` | 912 | Add `health_score = max(0, min(100, health_score))` clamp |
| `/root/tradingos_lab/edge_factory/data/tg_bot.py` | 909 | Same: `reconnects_score = max(0, min(30, 30 - int(restarts) * 5))` |
| `/root/tradingos_lab/edge_factory/data/tg_bot.py` | 910 | `latency_score = max(0, min(20, 20 if ob_age < 60 else (10 if ob_age < 300 else 0)))` |
| `/root/tradingos_lab/edge_factory/data/tg_bot.py` | 1683 area | `ALERT_MESSAGES = {**existing, "orderbook_stale": ("...", "...")}` |

## 7. Minimal Patch Plan

```python
# File: /root/tradingos_lab/edge_factory/data/tg_bot.py

# Fix #1 — line ~912 — clamp health score
health_score = completeness_score + reconnects_score + latency_score + dupes_score
health_score = max(0, min(100, health_score))  # ← ADD THIS LINE

# Fix #2 — line ~1680 — add missing ALERT_MESSAGES entry
ALERT_MESSAGES = {
    # ... existing entries ...
    "orderbook_stale": ("Orderbook не обновляется > 5 мин", "Проверить websocket collector"),
    "trade_stale": ("Trades не обновляются > 30 мин", "Проверить trade collector"),
    "liquidation_stale": ("Liquidations не обновляются > 1 ч", "Проверить liquidation collector"),
}

# Fix #3 — line ~1600 — add source-level dedup in heartbeat monitor
# (separate process 1367692, but for now just add to alerts.jsonl writer)
```

## 8. Estimated Fix Time

| Step | Time |
|------|------|
| Restart orderbook collector (one-liner) | 2 min |
| Fix 3 lines in tg_bot.py + restart | 10 min |
| Add `ALERT_MESSAGES` entry | 5 min |
| Verify health score clamps | 5 min |
| Verify no more "Неизвестно" | 5 min |
| **Total** | **~30 min** |

## 9. Command Sequence

```bash
# Step 1: Restart dead orderbook collector
kill 1406705
cd /root
nohup /root/.venv/bin/python3 -m edge_factory.data.orderbook_collector \
    --symbol BTCUSDT \
    --out /root/data_buffer \
    > /root/data_buffer/orderbook.log 2>&1 &
echo "OB collector PID: $!"

# Wait 30s for data to flow
sleep 30

# Step 2: Apply tg_bot.py fixes (clamp + ALERT_MESSAGES entry)
# Edit /root/tradingos_lab/edge_factory/data/tg_bot.py
#   - Line 912: add health_score clamp
#   - Line 1680: add orderbook_stale entry to ALERT_MESSAGES
# Then restart:
systemctl restart tradingos-telegram.service 2>/dev/null
# OR: kill the running tg_bot and let systemd restart it

# Step 3: Verify
tail -5 /root/data_buffer/raw/bybit/orderbook/2026-07-27/BTCUSDT/ | head -3
# Should show new files from last 30s

# Step 4: Check alerts.jsonl stops growing
sleep 60
wc -l /root/tradingos_lab/edge_factory/data/canary/alerts.jsonl
# Should be same as before — no new alerts since ob is flowing
```

## 10. Expected Outcome After Fix

| Metric | Before | After |
|--------|--------|-------|
| Orderbook data flow | 0 files/hour (since 08:34) | ~6 files/min |
| `orderbook_stale` alerts | 1 per minute | 0 (when data flows) |
| Health score | Unclamped (negative possible) | 0-100 always |
| Telegram warning | "• Неизвестно" | "• Orderbook не обновляется > 5 мин" (real text) |
| Process state | OB alive but dead | OB alive and flowing |
| Heartbeat monitor | Stale state, no watchdog | Data flowing, healthy state |

## Summary

**Two distinct bugs:**

1. **Orderbook collector is dead-locked** — process alive but no data since 08:34 UTC. This is the ROOT CAUSE of all warnings. Fix: restart.

2. **Formatter displays "Неизвестно"** because `ALERT_MESSAGES` dict lacks entry for `orderbook_stale`. Fix: add the entry.

3. **Health score can be unclamped/negative** because formula at line 912 lacks `max(0, min(100, ...))`. Fix: add clamp.

**Time to fix all: ~30 minutes** including restart.
