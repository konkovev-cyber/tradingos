# TradingOS H2 SHADOW — Maker-Order Execution Pilot

**VERDICT: EXECUTION-READY** (OAT passed; live pilot observing real PostOnly order
lifecycle — 8–12 week service NOT enabled; requires user authorization)

**CURRENT SERVICE STATE (2026-08-11 ~18:50Z):** watch service RUNNING
(`h2_detector.py --mode watch`, pid in `logs/h2_watch.pid`), `state.json` unpaused for
the authorized live pilot, frontier pinned, reconcile done, 0 errors, no fresh signals
yet. Kill-switch stays fail-closed at every restart (re-set `"paused": true` for any
unsupervised run).

---

## What this is

The final execution gate for the **H2 squeeze→expansion boundary-maker** mechanism — the
only surviving candidate of the 12-experiment Profit Hunt 2.0 campaign (OOS NET med
+25.8bps @maker, bootstrap CI [18.3,49.0], LONG+SHORT both positive, delayed-entry
control collapses = real timing edge; capital thresholds $250/$500/$1,000 per T11/T12).
This engine measures the ONE thing research could not: **real order-level maker
execution** — real PostOnly limit orders at the crossed 4h-range boundary, actual fills,
rejects, adverse selection, and the true signal frequency.

Frozen to the h2rep replication (`/tmp/profit_hunt2/h2rep/`): deep squeeze
(ATR-pct≤5, BBW-pct≤8, persist ≥5/8, 4h/24h range ≤0.75, tight ≤5×ATR), first ≥3×-volume
breach of the 4h range in **either** direction (direction-agnostic frozen form), resting
limit AT the boundary, fill window 5 M15 bars, exit next-bar-close after fill.
**No filters, no new indicators, no taker entry, no SL/TP.**

## VERDICT: EXECUTION-READY

---

## Safety model (frozen, do not weaken)

- **NO market/taker entry ever.** PostOnly-only. A PostOnly reject (would-cross) is data,
  logged, never retried as a taker order.
- **Kill-switch `state.json` is fail-closed**: `{"paused": true}` by default. Fresh read
  before EVERY live-order path (4 checkpoints; the final one immediately before the API
  call). Missing/corrupt file ⇒ **PAUSED**. Nothing cached.
- **Risk is frozen**: risk-per-trade **$5** (0.5% of the $1,000 model);
  `notional = $5 / (range_bps/10000)`, **HARD MAX_NOTIONAL = $150**, enforced in code
  (`h2_executor.py::_sizing`).
- **Max 1 concurrent position.** Concurrency is enforced against the runtime journal
  (and the exchange) before every placement.
- **Margin gate**: `notional/leverage(3x) × 1.25 ≤ available equity` else NO_TRADE.
  *Account reality: the live account holds ~$79.66 USDT; a capped $150 notional at 3x
  needs ~$62.50 margin — the pilot fits but leaves little headroom. The live contour is
  paused, so no account contention is expected. Do not run H2 live while other engines
  trade this account.*
- **Exit**: the position is closed with a real **market reduce-only** order at the next
  M15 boundary after fill (allowed — the NO-TAKER rule covers the entry path only).
- **Crash recovery**: on service start `reconcile()` cancels leftover orders, closes any
  open H2 position, and auto-pauses on an orphan position.
- **Per-symbol API errors never kill the loop**; numpy scalars sanitized before JSON.

## Files

| path | purpose |
|---|---|
| `config.json` | frozen params, risk ($5/$150/3x), universe (37 live syms), paths |
| `state.json` | kill-switch (default `paused: true`) |
| `signals.jsonl` | every candidate H2 signal (append-only) |
| `pilot_ledger.jsonl` | append-only order ledger (attempt/placed/filled/cancelled/rejected/exit/outcome/AS) |
| `h2_runtime.json` | crash-recovery journal (in-flight order / open position) |
| `gate0_sign_test.json` | Gate #0 sign test artifact |
| `oat_report.md` | OAT results (incl. defect log) |
| `scripts/h2_lib.py` | ported frozen detector (verbatim semantics) + JSON sanitizer |
| `scripts/bybit_client.py` | thin Bybit v5 REST client (auth pattern from live contour; keys from `/root/trading_brain_v4/research/execution/.env`) |
| `scripts/h2_detector.py` | M15 scanner: `--watch` (service), `--once`, `--replay` |
| `scripts/h2_executor.py` | order lifecycle: kill-switch ×4, sizing, PostOnly, poll, cancel, exit, reconcile |
| `scripts/h2_logger.py` | fail-closed state + JSONL ledger |
| `scripts/h2_gates.py` | gate metrics incl. **normalized frequency** and NET $/mo projection |
| `scripts/tests/` | Gate #0, detector synthetic, kill-switch OAT |
| `scripts/run_oats.sh` | full offline OAT suite |
| `scripts/start_h2.sh` / `stop_h2.sh` | manual start/stop (nohup) |
| `tradingos-h2-shadow.service` | systemd unit (installed to `/etc/systemd/system/`, **disabled**) |

## How to run

```bash
cd /root/tradingos/profit_engines/h2_shadow/scripts

# 1. Offline OAT suite (Gate #0 + synthetic detector + kill-switch + replay)
./run_oats.sh            # all must pass

# 2. Read-only live scan (state.json paused=true) — logs signals, places nothing
python3 h2_detector.py --mode once

# 3. LIVE PILOT (operator decision): explicitly unpause, then watch
#    edit ../state.json  ->  "paused": false   (NO script flag can do this for you)
./start_h2.sh
./stop_h2.sh

# 4. Gates on demand
python3 h2_gates.py
```

### Service enable for the 8–12 week collection (user authorization required)

```bash
sudo systemctl daemon-reload
sudo systemctl enable tradingos-h2-shadow
# verify state.json is unpaused by operator, then:
sudo systemctl start tradingos-h2-shadow
```

## state.json semantics

- `{"paused": true}` — **default**. Detector still scans and logs signals
  (observations); executor returns NO_TRADE at checkpoint 1 before any API call.
- `{"paused": false}` — placement enabled. MUST be set by an operator; it is the ONLY
  switch between scanning and real money.
- missing/corrupt file — treated as paused (fail-closed).

## Config reference (frozen params)

`config.json → frozen`: `atr_win=480`, `q_atr_pct=5`, `q_bbw_pct=8`, `persist_k=5`,
`persist_m=8`, `rcomp_gate=0.75`, `range_bars=16`, `range_tight_atr4=5.0`,
`vol_gate=3.0`, `max_gate_atr4=3.0`, `cooldown=24h`, `liq_thr=$3000`, direction filter **none**.
`risk`: `risk_per_trade_usd=5`, `max_notional_usd=150` (HARD cap), `leverage=3`,
`margin_buffer_x=1.25`. `execution`: `fill_window_bars=5`, `poll_sec=25`,
`max_concurrent_positions=1`, `taker_entry=false`.

## VERDICT: EXECUTION-READY
