# OMF-002 — DATA-SOURCE AUDIT

*Read-only research project. TradingOS production FROZEN, R148 frozen, mode=MANUAL, AUTO disabled.*

---

## 1. Executive Verdict

**`DATA GAP — LIQUIDATION CLOSED, FILL-RATE STILL OPEN`**

- OMF-001 закрыто (7 microstructure hypotheses REJECT)
- FINAL edge research закрыто (11 REJECT, 1 INCONCLUSIVE, 1 DATA GAP)
- **OMF-002 Phase 1: DATA-SOURCE AUDIT** выполнен
- Liquidation data gap **ЗАКРЫТ** через read-only WS collector
- **Sample size = 0** (рынок спокоен с начала collector)
- Дальше: дождаться minimum sample, потом hypothesis tests

---

## 2. Data Source Audit Matrix

| Source | Type | Status | Notes |
|---|---|---|---|
| **microstructure_collector** | WS (REST) | LIVE, 15 symbols, 11d+ | OK |
| trade_collector | WS | LIVE, BTCUSDT only | partial — Priority 5 needs expansion |
| **liquidation_collector_omf002** | WS | **NEW** (started 15:50Z 2026-08-20), 15 symbols | 0 events so far (calm market) |
| funding REST | polling 5min | LIVE, 66d cap | OK |
| OI REST | polling 5min | LIVE, 8d@1h | short |
| Klines REST | per-call | ad-hoc, 365d+ | OK |
| liquidations REST | n/a | HTTP 404, no public history | n/a (now covered by WS) |

### Priority ranking per OMF-002 spec

| # | Source | OMF-002 status |
|---|---|---|
| **P1** | Liquidation events/clusters | **DATA GAP CLOSED** (WS collector running, awaiting events) |
| P2 | Order-book liquidity withdrawal | OK (microstructure has bid_depth_01pct, ask_depth_01pct) |
| P3 | Aggressive-flow + price-response divergence | OK (microstructure has taker_imb, n_trades, mid) |
| P4 | Trade-size / burst microstructure | partial (trade_collector only BTCUSDT) |
| P5 | Cross-sectional leader/laggard | OK (microstructure 15 syms) |

---

## 3. Infrastructure Setup

### New file: `/root/tradingos_lab/edge_factory/data/liquidation_collector_omf002.py`

- Multi-symbol (15 majors)
- Read-only WS stream (`allLiquidation`)
- Parquet hourly files
- Watchdog: warns after 90s of no events
- Drops into `/root/data_buffer/raw/bybit/liquidations/`
- **NO** integration with TradingOS, no execution, no strategy

### New service: `liquidation-collector-omf002.service`

- Read-only systemd unit
- MemoryMax=512M, CPUQuota=20%
- NoNewPrivileges=true
- Auto-restart=always
- Started 15:50:00Z 2026-08-20

---

## 4. TradingOS State (UNCHANGED)

- mode=MANUAL
- AUTO disabled
- Guardian active, deposit_guard active
- R148 frozen
- P3 read-only, n=9
- exit-shadow, exit_shadow_engine, microstructure_collector, trade_collector running

---

## 5. Next Steps

1. Wait for first liquidation events to accumulate
2. After 24h+ of data, run Priority 1 hypothesis test:
   - H1.1: aggressive liquidation burst → continuation
   - H1.2: liquidation cluster → exhaustion/reversal
   - H1.3: cross-symbol liquidation cascade
3. Per OMF-002 rule: hard OOS NET criterion, cost stress 1.0/1.2/1.5/2.0×, anti-cherry
4. PASS requires: OOS NET > costs, anti-cherry > 0, per-symbol non-concentrated, time-stable

## FINAL VERDICT: **`DATA SOURCE COLLECTOR LIVE, AWAITING SAMPLE`**

NO hypothesis tested yet. Next: hypothesis test after minimum sample collected.
