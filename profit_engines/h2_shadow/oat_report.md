# H2 Shadow Run — OAT Report (Execution Pilot Gate)

**Build date**: 2026-08-11 · **Engine**: `/root/tradingos/profit_engines/h2_shadow/`

## 0. Gate order — nothing was placed before all three pre-flight gates passed

| # | Gate | Result | Evidence |
|---|------|--------|----------|
| 0 | Gate #0 synthetic sign test (post-DLF discipline) | **PASS 7/7** | `gate0_sign_test.json` — LONG 100→110=+10%, LONG 100→90=−10%, SHORT 100→90=+10% (explicit `(entry−exit)/entry`, no mirror trick), SHORT 100→110=−10%, partial 50% = +2.5%, expired/cancel = 0. Independent hand-check formulas included. |
| 1 | Detector replay vs frozen replication | **MATCH 474=474** | ported `detect_events` over the frozen 90d cache (97 liquid syms) reproduces `h2rep_squeeze.detect_events` exactly (474 events, up 177 / dn 297, 88 syms). |
| 2 | Fail-closed kill-switch OAT | **PASS** | `scripts/tests/test_killswitch.py` — paused→NO_TRADE with **zero API calls**; corrupt state.json→fail-closed; missing state.json→fail-closed; resume+dry-run→"would place" with `create_order` **never called**; live lifecycle sim (fake client) place→fill→exit→`FILLED` with full ledger trail `attempt→placed→filled→exit_order→exit→outcome`. |

> **No live order was placed before gates 0–2 all passed.** The live read-only scan
> (state.json paused) confirmed placement-blocking on the real infra: 25 candidate
> signals detected and logged, all `NO_TRADE (paused:L1)`, exchange left with
> **0 positions / 0 open orders**.

## 1. Client / infra defects found and fixed during the OAT

| Defect | Fix | Evidence |
|---|---|---|
| Bybit v5 kline `start` returned in ms (string), code applied ×1000 → timestamps 1000× too large | drop the ×1000 (`int(it[0])`) | `bybit_client.py` |
| `_get` returns `result` but public methods unwrapped `.result` again → empty lists | unwrap only once | `bybit_client.py` |
| `wallet_usdt` returned None: `availableToWithdraw` is an empty string → `float('')` raised | tolerant `_f()` helper | `bybit_client.py` |
| `order/realtime` requires `symbol` or `settleCoin` | add `settleCoin=USDT` | `bybit_client.py` |
| Watch-mode first scan would fire **stale historical** warmup signals (25 fired in paused run_once were historical, harmless only because paused) | pin scan frontier to newest closed bar at service start; pre-frontier events are consumed, never fired | `h2_detector.py` |
| `PartiallyFilled` order was closed early; frozen spec keeps the limit resting until filled or N bars | only `Filled` triggers immediate fill; partial keeps resting, cancelled at deadline | `h2_executor.py` |
| Crash mid-order would leave a live order/position | `reconcile()` at service start: finish in-flight order (fill→close, else cancel), close open H2 position, orphan-position on a universe symbol → auto-pause + alert | `h2_executor.py` |

## 2. Live read-only OAT (real API, paused)

- Universe verified live: **37 trading symbols** of the configured 40 (TONUSDT/FETUSDT `Closed`, PEPEUSDT invalid).
- Warmup scan over last ~8 days × 37 syms → **25 valid H2 candidate signals** logged to `signals.jsonl`.
- All 25 executor handoffs → `NO_TRADE` (`paused:L1`). **0 API order calls.** Exchange unchanged.

## 3. OAT verdict

**EXECUTION-READY (infra-correctness gate)** — the detector detects, the kill-switch blocks every
live-order path directly before the API call, the ledger logs the full lifecycle, and the
real-API path was exercised read-only without placing an order. Live placement correctness
(PostOnly acceptance, fill-rate, AS) is measured in the live pilot that follows.

## 4. Frequency gate (addendum)

| metric | value |
|---|---|
| signals observed (warmup window, read-only) | 25 |
| window | ~198 h × 37 syms |
| **signals/symbol/day** | **0.0819** |
| signals/day @ 37-sym scanned universe | 3.03 |
| **signals/day @ frozen 97-liquid universe** | **7.94** |
| projected signals/week · month @97 | 55.6 · 238 |
| confidence | wide band (short window); the 8–12 week service exists to tighten it |

Fills/month, NET $/fill, NET $/month and the T12 $77/mo comparison are reported in the pilot
verdict once live fills accumulate (`scripts/h2_gates.py`).

## 5. Live pilot (as far as this session ran)

- Service running: `h2_detector.py --mode watch` (pid in `logs/h2_watch.pid`), `state.json`
  unpaused by operator decision, frontier pinned, `reconcile_done`, per-poll heartbeats
  (~15s cycle / 37 symbols / 60s cadence), **0 loop/poll errors**.
- **Fresh live signals observed in-session: 0** (H2 signals are rare: ~3/day over 37 syms;
  10–20 fresh signals take days). The 25 signals in `signals.jsonl` are warmup-window
  observations from the paused read-only OAT (all `NO_TRADE`).
- Fills: 0 · fill-rate: N/A · AS: N/A · NET: N/A — live order-level measurement starts at
  the first fresh signal; the fake-client lifecycle simulation already proved the full
  place→fill→exit→ledger path.
- The 8–12 week service is **NOT enabled** (systemd unit installed but `disabled`);
  enabling requires user authorization.
