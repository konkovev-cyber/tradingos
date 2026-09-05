# CIO PLAYBOOK v1.0

Operational handbook for the CIO role defined in `CIO_OPERATING_DIRECTIVE_v1.md`.

This document is **operational procedure**. It can be updated more often than the directive itself.

---

# Daily Report Template

Every daily report must follow this exact template. The directive requires 7 questions; the playbook defines how to answer each one.

## Header

```text
CIO Daily Report — YYYY-MM-DD
Phase:               MONEY VALIDATION
System uptime:       X hours
Equity:              $X.XX
Closed trades total: N
```

---

## Q1 — Что сегодня ограничивает прибыль?

Назови ОДНУ главную причину.

Подтверди цифрами.

**Format:**
```text
Q1: ONE main limit + data evidence
   Limit:  <name>
   Data:   <2-3 supporting numbers>
   Source: <file/scoreboard/log>
```

**Example:**
```text
Q1: Limit is "0 closed trades, all 4 positions still open".
   Data: 4/4 positions open, 3 in red, 0 closed in 14 hours.
   Source: /root/tradingos/guardian/reality_state.json
```

---

## Q2 — К какому типу относится ограничение?

Один класс из: Market / Strategy / Capital / Operations / Unknown.

Если уверенность ниже 80% — покажи распределение.

**Format:**
```text
Q2: Limitation class
   Primary:    <Class>
   Confidence: <0-100>%
   Distribution:
       Market       NN%
       Strategy     NN%
       Capital      NN%
       Operations   NN%
       Unknown      NN%
```

**Classification rule (operational, can be tuned):**
- `closed_trades ≥ 1` → Unknown (insufficient data)
- `open = 0` → Capital 90% (idle)
- `open_in_red AND avg_holding > 4h` → Market 60%, Strategy 20%, Capital 15%
- `else` → Unknown 50% (default)

---

## Q3 — Можно ли убрать ограничение без изменения стратегии?

YES или NO. Если YES — действие. Если NO — почему.

**Format:**
```text
Q3: Can be removed without strategy change?
   Answer: YES | NO
   If YES:
       Action: <what to do>
       Risk:   <operational risk>
   If NO:
       Reason: <why action not possible>
       Trigger to unlock: <what event would enable action>
```

---

## Q4 — Что известно?

Только подтверждённые факты.

**Format:**
```text
Q4: What we KNOW
   - <fact 1 with data>
   - <fact 2 with data>
   - <fact 3 with data>
```

---

## Q5 — Что неизвестно?

Каких данных не хватает? Что должно произойти, чтобы их получить?

**Format:**
```text
Q5: What we DON'T know
   - <unknown 1>
       Trigger: <what would reveal it>
   - <unknown 2>
       Trigger: <what would reveal it>
```

---

## Q6 — Что изменится после следующего события?

Какие решения станут возможны?

**Format:**
```text
Q6: After next event
   Event:           <concrete trigger>
   New data:        <list>
   Possible actions: <list of decisions that become possible>
```

---

## Q7 — Какое решение принимается сегодня?

ОДНО из: CONTINUE / FIX / SCALE / TUNE / STOP.

**Format:**
```text
Q7: Today's decision
   Decision:  <CONTINUE | FIX | SCALE | TUNE | STOP>
   Why:       <one paragraph>
```

---

# Required KPIs (12 mandatory metrics)

Every report must include:

| KPI | Source | Format |
|-----|--------|--------|
| Infrastructure Health | systemd service status | % active / 3 |
| Operational Health | error count | 0 = OK |
| Capital Growth | equity vs $10.19 start | % delta |
| Capital Utilization | used_capital / equity | % |
| Decision Readiness | 4-axis composite | % |
| Guardian Effectiveness | v3 4-metric | summary |
| Average Holding Time | position lifecycle data | hours |
| Opportunity Cost | rejected_candidates log | rejected/win/loss |
| Current Risk | sum of SL distances | $ |
| Open PnL | Bybit unrealisedPnl | $ |
| Closed PnL | trade_results.jsonl | $ |
| Expected Next Decision | upcoming trigger | text |

---

# DECISION READINESS composite

4 axes, each 0-100%:

```text
Infrastructure Readiness    — services online, integrations stable
Operational Readiness      — no errors, restart count low
Statistical Readiness      — closed trades, Expectancy, PF calculated
Management Readiness       — data sufficient for VALIDATION REVIEW

Overall = avg(4 axes)
```

Display in every report:
```text
DECISION READINESS
  Infrastructure      NN%
  Operational Health   NN%
  Statistical Evidence NN%
  Management Readiness NN%
  Overall Review Readiness NN%
```

---

# DIRECTIVE COMPLIANCE check

End of every report:

```text
DIRECTIVE COMPLIANCE

CIO questions answered:           7/7
Decision issued:                  YES
Evidence separated from assumptions: YES
Recommendation section:           YES
Strategy changes proposed:        NO (or count)
Policy violations:                NONE (or list)
```

If any item is NO — the report does not comply and must be rewritten.

---

# Limitation classification (operational rule)

| Trigger | Class | Confidence |
|---------|-------|------------|
| 0 closed trades | Unknown | 0% (start) |
| ≥1 closed trade | Unknown | increases with N |
| 0 open, ≥0 closed | Capital | 90% |
| open in red, long hold | Market | 60% |
| service errors detected | Operations | 95% |
| risk / equity > threshold | Capital | 85% |
| systematic over-holding pattern | Strategy | only after VALIDATION REVIEW |

---

# CIO Recommendations — section template

After main report, list 0-3 ideas. Each:

```text
IDEA: <name>
  Problem:           <what it solves>
  Why now:           <what triggered this idea>
  Expected effect:   <which KPI improves>
  Risk:              <operational risk>
  Strategy impact:   NONE | LOW | MEDIUM | HIGH
  MV impact:         SAFE | POSTPONE | REJECT
```

If `Strategy impact: HIGH` OR `MV impact: REJECT` → put in BACKLOG only, do not implement.

---

# Filing rule

- Daily report archived at: `/root/tradingos/reports/cio/YYYY-MM-DD.md`
- Decision log: appended to `/root/tradingos/reports/cio/decisions.log`
- Recommendations: `/root/tradingos/reports/cio/recommendations.log`

---

# Evolution rule

This playbook can be updated when:
- A new closed trade reveals a better classification rule
- The 12 KPI list becomes incomplete or redundant
- The DECISION READINESS axes need recalibration

The directive (`CIO_OPERATING_DIRECTIVE_v1.md`) cannot be updated without creating v1.1 or v2.0.
