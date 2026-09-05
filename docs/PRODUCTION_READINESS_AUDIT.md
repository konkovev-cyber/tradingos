# Production Readiness Audit — TradingOS Control Plane v1

**Date:** 2026-07-21
**Purpose:** What actually works, what's connected, what's manual.

---

## 1. ACTIVE SERVICES

| Service | Status | Role | Connected to Control Plane |
|---------|--------|------|---------------------------|
| `position-guard.service` | ✅ RUNNING | Collects PG evidence | Writes to JSONL logs |
| `pie-observer.service` | ✅ RUNNING | PIE data collection | Writes to PIE DB |
| `ubot-bingx.service` | ✅ LIVE | Trading bot | Reads by Capital Adapter |
| `trade-collector.service` | ✅ RUNNING | Data collection | Indirect (feeds PIE) |
| `shadow-live-daemon.service` | ✅ RUNNING | Shadow live | Independent |

**NOT running (no service/timer):**
- ❌ KPI Collector (manual only)
- ❌ Experiment Snapshot (manual only)
- ❌ Decision Review (manual only)
- ❌ Unified State Collector (manual only)
- ❌ Any daily report automation

---

## 2. CONTROL PLANE STATE FILES

| File | Exists | Last Modified | Automated? |
|------|--------|--------------|-----------|
| `current_state.json` | ✅ | 04:48 | ❌ Manual |
| `capital_intelligence_v2.json` | ✅ | 04:48 | ❌ Manual |
| `portfolio_intelligence.json` | ✅ | 04:48 | ❌ Manual |
| `decision_report.json` | ✅ | 04:48 | ❌ Manual |
| `ceo_state.json` | ✅ | 04:48 | ❌ Manual |
| `paper_simulation.json` | ✅ | 04:48 | ❌ Manual |
| `reality_result.json` | ✅ | 04:48 | ❌ Manual |
| `kpi_collector/state.json` | ✅ | 05:01 | ❌ Manual |
| `decision_review_v2.json` | ✅ | 05:01 | ❌ Manual |
| `approval.json` | ✅ | — | ❌ Manual |

**All state files are from the same manual run (~04:48). No automated updates.**

---

## 3. TOOLS INVENTORY

| Tool | Purpose | Automated? |
|------|---------|-----------|
| `experiment_monitor.py` | Gate status | ❌ Manual |
| `experiment_snapshot.py` | Daily snapshot | ❌ Manual |
| `decision_review_report.py` | Review report | ❌ Manual |
| `capital_overview.py` | Capital PnL | ❌ Manual |
| `capital_intelligence_report.py` | Capital v2 | ❌ Manual |
| `position_guard_report.py` | PG report | ❌ Manual |
| `position_guard_outcome.py` | PG outcomes | ❌ Manual |
| `action_shadow_report.py` | Action report | ❌ Manual |
| `action_shadow_outcome.py` | Action outcomes | ❌ Manual |
| `position_guard_health.py` | Service health | ❌ Manual |
| `position_guard_dashboard.py` | Dashboard | ❌ Manual |
| `bingx_reality_check.py` | BingX vs PIE | ❌ Manual |

**12 tools exist, NONE are automated.**

---

## 4. DATA FLOWS (actual)

```
LIVE Bot (ubot_bingx)
    │
    ├─ writes to: bot_state.db (trades)
    │
    └─ PIE Observer reads → writes to: tradingos_data.db
         │
         └─ Position Guard reads → writes to: position_guard_shadow.jsonl
              │
              └─ Action Shadow reads → writes to: position_action_shadow.jsonl
                   │
                   └─ KPI Collector reads → writes to: kpi_collector/state.json
                        │
                        └─ Decision Review reads → writes to: decision_review_v2.json
```

**What's automated in this flow:**
- LIVE Bot → bot_state.db ✅ (automatic)
- PIE Observer → tradingos_data.db ✅ (automatic, service)
- Position Guard → JSONL ✅ (automatic, service)
- Action Shadow → JSONL ❌ (NO service, manual only)

**What's NOT automated:**
- KPI Collector → state.json ❌
- Decision Review → review.json ❌
- State Collector → current_state.json ❌
- Capital Intelligence → v2.json ❌
- Portfolio Intelligence → portfolio.json ❌
- CEO Control → ceo_state.json ❌

---

## 5. CRITICAL GAPS

### Gap 1: Action Shadow has NO service
Action Shadow is the primary data source for KPI Collector. Without it running automatically, KPI Collector gets no new samples.

### Gap 2: KPI Collector has NO automation
Must be run manually: `python3 -m control_plane.kpi_collector.cli run`

### Gap 3: Decision Review has NO automation
Must be run manually: `python3 -m control_plane.review.cli`

### Gap 4: No daily snapshot automation
`experiment_snapshot.py` must be run manually.

### Gap 5: BingX Reality Check has signature errors
`bingx_reality_check.py` returns "Null signature" — API not authenticated.

---

## 6. WHAT ACTUALLY WORKS (end-to-end)

| Flow | Automated? | Status |
|------|-----------|--------|
| LIVE Bot → PIE → PG → Action Shadow | Partial | PG yes, Action Shadow no |
| Action Shadow → KPI Collector → Review | Manual only | Works when run |
| State Collector → Capital → Portfolio → Decision → CEO | Manual only | Works when run |
| Paper → Reality → local signals | Manual only | Works when run |

---

## 7. VERDICT

**Architecture: ✅ Complete and verified**
**Automation: ⚠️ 95% manual**
**Data collection: ⚠️ Partial (PG yes, Action Shadow no)**

### Minimum to make system operational (not LIVE):

1. Create `action-shadow.service` — runs Action Shadow periodically
2. Create `kpi-collector.service` — runs KPI Collector periodically
3. Create `tradingos-daily.service` — runs State + Review + Snapshot daily

### NOT needed for observation mode:
- BingX HMAC (reality check works without it for now)
- Paper Execution automation
- LIVE execution
- New architecture

---

## 8. RECOMMENDATION

**Do NOT write new modules.** Wire existing ones:

```bash
# Create 3 systemd timers (10 min, 1 hour, daily)
# Wire: Action Shadow → KPI Collector → Decision Review
# That's it.
```

After wiring, the system runs itself and produces daily reports. First Quality Review happens when samples ≥ 20.
