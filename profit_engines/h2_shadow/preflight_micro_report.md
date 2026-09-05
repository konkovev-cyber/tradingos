# H2 Shadow — MICRO Pre-Flight Report (max_notional_usd 150 -> 50)

**Date**: 2026-08-12 05:08 UTC · **System root**: `/root/tradingos/profit_engines/h2_shadow/`
**Operator**: execution-change contract (config-only micro change). Service left RUNNING and **paused=true**. No restart, no unpause, no orders.

---

## VERDICT: **READY_FOR_USER_ENABLE**

| Gate | Result |
|---|---|
| ONLY CONFIG CHANGE | `max_notional_usd` 150.0 -> 50.0 (nothing else) |
| FROZEN H2 CHANGES | **0** |
| EXECUTOR CHANGES | **0** |
| UNIVERSE CHANGES | **0** (37 symbols, untouched) |
| REAL ORDERS PLACED DURING PREFLIGHT | **0** (create_order/get_order/cancel_order invoked 0 times) |

---

## 1. Config change (the only change)

Backup: `config.json.bak-20260812T050532Z` (reversible). Diff backup vs current:

```diff
 29c29
 <     "max_notional_usd": 150.0,
 ---
 >     "max_notional_usd": 50.0,
```

JSON valid; `python3 -c "import json; json.load(open('config.json'))"` passes; `universe` = 37 symbols (unchanged).

> Note: `risk.note` still reads "HARD MAX_NOTIONAL = $150" — descriptive string only; changed to keep the diff to the single allowed value. User may want to update it on next config edit.

## 2. Frozen H2 / executor / universe diffs

- `config.json` `frozen` section: byte-identical vs backup (diff shows only line 29, in `risk`).
- All code SHA-256 identical pre/post change (h2_lib, h2_detector, h2_executor, h2_gates, h2_logger, bybit_client, run_oats/start/stop): **0 changes**.
- `universe` array: 37 symbols, identical.

## 3. Kill-switch (fail-closed) verification

- `state.json` = `{"paused": true, reason: "...pre-flight in fail-closed state...", updated_by: main_agent}` — **paused=true verified** (also byte-identical before/after preflight).
- Fresh-read fail-closed simulation (in-process, executor `process_signal`, dry-run, fake client): paused -> **NO_TRADE** at L1, reason `paused:...`, **ZERO API calls**. PASS.
- Corrupt/missing state fail-closed paths were proven in the prior OAT (`oat_report.md` Gate 2, `test_killswitch.py` PASS). Not re-run here: that harness temporarily writes `paused=false` (its resume/lifecycle cases) — doing so during a live watch window would violate the contract's zero-tolerance "no unpause" rule, so the paused-path was re-simulated without touching state.json instead.

## 4. Synthetic min-notional + cap test (dry-run, no live orders)

Grounded in real read-only Bybit facts fetched 2026-08-12 (`instruments-info` + tickers): BTC min_qty 0.001/step 0.001, ETH 0.01/0.01, SOL 0.1/0.1, BNB 0.01/0.01, XRP 0.1/0.1; min_notional = $5 on all; honest min = max($5, min_qty·price) = BTC $63.71, ETH $18.86, SOL $7.62, BNB $6.14, XRP $5.00. Equity used: $79.58.

Range cases per symbol: tight 40bps (raw notional $1250 -> capped) and wide 2000bps (raw $25 -> uses less).

| sym | case | notional (cap=50) | min-gate result | notional@limit |
|---|---|---|---|---|
| BTCUSDT | 40bps / 2000bps | 50.0 / 25.0 | **NO_TRADE** (`below_min_qty:0.0<0.001`, honest min $63.71 > $50 cap) | 0 |
| ETHUSDT | 40bps / 2000bps | 50.0 / 25.0 | ok | $37.72 / $18.86 |
| SOLUSDT | 40bps / 2000bps | 50.0 / 25.0 | ok | $45.70 / $22.85 |
| BNBUSDT | 40bps / 2000bps | 50.0 / 25.0 | ok | $49.12 / $24.56 |
| XRPUSDT ($5-min sym) | 40bps / 2000bps | 50.0 / 25.0 | ok | $49.92 / $24.96 |

- Cap enforcement (`h2_executor.py::_sizing`, line ~141): `notional = min(raw, max_notional_usd)`; all 10 cases `notional <= $50`, capped flag correct. **Cap never exceeded.**
- BTC -> NO_TRADE via the first of the two existing fail-closed min gates (`below_min_qty`, line 239); the `below_min_notional` gate (line 241) is the second. Both gates untouched (pre-existing, contract frozen).
- Non-BTC symbols pass all min gates; all sized notional <= $50; margin gate passed (worst case $49.12@3x·1.25 ≈ $20.5 < $79.58 equity). No symbol removed from the universe.
- `create_order` / `get_order` / `cancel_order` invoked **0** times (fake client raises if touched). **REAL ORDERS PLACED: 0.**

Harness: `scripts/tests/preflight_sizing_test.py` (NEW temp file, in-process fake-client pattern identical to `test_killswitch.py`; ledger redirected to a temp file, deleted by the script; no writes to state.json). Could not be removed afterward due to a sandbox `rm` guard — safe to delete; it touches nothing.

## 5. Service health + heartbeat freshness

- `systemctl is-active tradingos-h2-shadow` = **active** (running since 2026-08-12 01:30 MSK; Main PID 786).
- Last `poll_heartbeat` in `pilot_ledger.jsonl`: **11s old** at check time (cycle ~15s, n_active=37) — fresh.
- Errors in ledger: exactly 1 `loop_error` ("The read operation timed out", 2026-08-11 19:33Z) — **predates the current service incarnation** (started 22:40Z). Zero errors since.
- Ledger integrity: 825 rows, 584 heartbeats, all JSON-parseable.

## 6. Operational notes for the user's enable step

- The running process instantiated `H2Executor` once at service start (`h2_detector.py` `make_handoff`), so it still holds `max_notional=150` in memory. **The $50 cap takes effect on the next service start/restart** — which is part of the user's SEPARATE live-enable action (this contract performed no restart).
- While paused, no path can place orders regardless of config.

## 7. STOP state

Config changed (reversible via `config.json.bak-20260812T050532Z`). Service running, **state.json still paused=true**. No restart performed. Live enable remains an explicit user action.
