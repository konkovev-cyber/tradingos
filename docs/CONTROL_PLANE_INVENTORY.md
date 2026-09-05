# TradingOS Control Plane Inventory v1

**Date:** 2026-07-21
**Purpose:** Map all existing data sources, tools, and services for Unified State Layer.

---

## 1. Running Services (8 active)

| Service | Path | Role | State |
|---------|------|------|-------|
| `ubot-bingx.service` | `/opt/ubot_bingx` | LIVE trading bot | RUNNING |
| `pie-observer.service` | `/root/tradingos/scripts` | PIE observer | RUNNING |
| `position-guard.service` | `/root/tradingos/services` | PG experiment (SHADOW) | RUNNING |
| `tradingos-telegram.service` | — | Telegram bot | RUNNING |
| `trade-collector.service` | — | Edge Factory collector | RUNNING |
| `scalper-v6.service` | — | Bybit bot | RUNNING |
| `market-agent-bot.service` | — | Market Agent | RUNNING |
| `trading-control.service` | — | Trading Control Panel | RUNNING |

---

## 2. Databases

### TradingOS (root)

| DB | Tables | Rows | Role |
|----|--------|------|------|
| `tradingos_data.db` | events, live_assist_log, position_events | 6771+243+2662 | PIE data, events |
| `tradingos_audit.db` | — | — | Audit trail |
| `tradingos_deployment.db` | — | — | Deployment state |
| `tradingos_execution.db` | — | — | Execution records |
| `tradingos_governor.db` | — | — | Governor state |
| `tradingos_learning.db` | — | — | Learning data |
| `tradingos_markers.db` | — | — | Market markers |

### ubot_bingx

| DB | Tables | Rows | Role |
|----|--------|------|------|
| `bot_state.db` | trade_journal | 12 | Real closed/open trades + PnL |

### Lab

| DB | Tables | Role |
|----|--------|------|
| `edge_factory/edge_registry.db` | experiments, failures, theses, nightly_reports | Edge lifecycle |
| `edge_factory/production.db` | decay_checks, shadow_runs, promotion_gate | Production readiness |
| `edge_factory/cemetery.db` | cemetery | Dead edges |
| `edge_factory/guardian.db` | incidents, trade_log, strategy_health | Health monitoring |
| `data_engine/data.db` | events, collector_state | Data collection |

---

## 3. JSONL Logs (3)

| Log | Rows | Role |
|-----|------|------|
| `position_guard_shadow.jsonl` | 538+ | PG decisions (HOLD/MOVE_SL/SKIPPED) |
| `position_action_shadow.jsonl` | 190+ | Action Shadow decisions (APPROVE/REJECT) |
| `bingx_reality_diff.jsonl` | 1 | BingX vs PIE diff |

---

## 4. Read-Only Tools (12)

| Tool | Data Source | Output |
|------|-----------|--------|
| `experiment_monitor.py` | PG + Action logs | Gate status (READY/COLLECTING) |
| `decision_review_report.py` | PG + Action logs + Capital | Full review (DQ, EV, verdict) |
| `capital_intelligence_report.py` | ubot DB + PIE DB | Portfolio, risk concentration |
| `capital_overview.py` | ubot DB + PIE DB + PG log | PnL by system |
| `action_shadow_report.py` | Action Shadow log | Approved/rejected breakdown |
| `action_shadow_outcome.py` | Action Shadow log + PIE DB | Per-signal ROI |
| `position_guard_report.py` | PG log | DQ, decision breakdown |
| `position_guard_outcome.py` | PG log + PIE DB | Per-signal ROI |
| `position_guard_health.py` | systemd + PG log | Service health |
| `position_guard_dashboard.py` | systemd + PG log + PIE DB | Control Plane view |
| `experiment_snapshot.py` | All logs | Daily evidence snapshot |
| `bingx_reality_check.py` | BingX API + PIE DB | BingX vs PIE diff |

---

## 5. Lab Modules (ready for import)

| Module | Export | Lines | Role in Control Plane |
|--------|--------|------:|----------------------|
| `capital_engine` | `CapitalAllocationEngine.allocate()` | 301 | Capital allocation |
| `decision_engine` | `DecisionEngine`, `Decision` | 98 | Auto-scoring, verdicts |
| `reality_engine` | `RealitySimulator`, `CostModel` | 104 | Cost simulation |
| `paper_engine` | `PaperEngine`, `PaperOrder`, `PaperPosition` | 112 | Paper execution |
| `portfolio_engine` | `dashboard.py`, stress tests | 138 | Portfolio intelligence |
| `risk_budget` | wrapper | 14 | Risk budgeting |

---

## 6. Unified State Layer — Data Source Mapping

### Research State
```
Source:  edge_factory/edge_registry.db
         edge_factory/production.db
         tradingos_data.db (events)
Tool:    decision_review_report.py
Answer:  "What experiments are running? What's the evidence quality?"
```

### Position State
```
Source:  tradingos_data.db (position_events, live_assist_log)
         position_guard_shadow.jsonl
         position_action_shadow.jsonl
Tool:    position_guard_dashboard.py
Answer:  "How many positions? What actions are pending?"
```

### Capital State
```
Source:  /opt/ubot_bingx/bot_state.db (trade_journal)
         tradingos_data.db (live_assist_log)
Tool:    capital_intelligence_report.py
Answer:  "Where is the money? What's the risk?"
```

### Execution State
```
Source:  systemctl status (ubot-bingx, pie-observer, position-guard)
         /opt/ubot_bingx/*.log
Tool:    position_guard_health.py
Answer:  "Are bots running? Any errors?"
```

---

## 7. What's Missing for Unified State

| Gap | Current State | Needed |
|-----|--------------|--------|
| Unified JSON snapshot | Each tool outputs separate reports | Single `tradingos_state.json` |
| Cross-domain queries | No tool combines Research+Position+Capital | `control_plane/state_collector.py` |
| Real-time updates | Tools run on-demand | Event-driven or cron-based |
| Human-readable dashboard | CLI tools only | CEO Dashboard (web/TG) |

---

## 8. Migration Priority

```
Phase 1 — Unified State (read-only):
  Import existing tool outputs into single JSON
  No new logic, just aggregation

Phase 2 — Decision Engine:
  Import lab/decision_engine
  Auto-score experiments
  PROMOTE/CONTINUE/REJECT

Phase 3 — Paper Execution:
  Import lab/paper_engine
  Virtual portfolio simulation
  Action vs HOLD comparison

Phase 4 — CEO Dashboard:
  Web/TG interface
  Real-time state visualization
```
