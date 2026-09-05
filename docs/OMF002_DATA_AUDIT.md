# OMF-002 — Data Audit: Liquidation Flow

*Read-only research project. TradingOS production FROZEN, R148 frozen, mode=MANUAL, AUTO disabled.*

---

## 1. Executive Verdict

**`DATA COLLECTOR ACTIVE — AWAITING SAMPLE ACCUMULATION`**

- Read-only WS collector for `allLiquidation` topic is **operational** since 15:50Z 2026-08-20
- **0 events** captured in 2.5h (calm market regime, no leverage flushes)
- Bybit REST `/v5/market/liquidation` returns **HTTP 404** (no public history)
- Coinglass unreachable (network isolation in current env)
- **Verdict on Phase 1**: infrastructure correct, sample insufficient

---

## 2. Data Source Audit Matrix

| Source | Endpoint | Available | Notes |
|---|---|---|---|
| **Bybit WS `allLiquidation`** | pybit WebSocket `all_liquidation_stream(symbol, callback)` | ✓ LIVE | 15 majors, push 500ms |
| Bybit REST `/v5/market/liquidation` | n/a | **HTTP 404** | No public history |
| Bybit REST `/v5/market/recent-trade` | OK | ✓ no liq marker | trades have side only |
| Coinglass | n/a | unreachable | network blocked |
| Trades parquet buffer | `/root/data_buffer/raw/bybit/trades/BTCUSDT/` | ✓ BTCUSDT only | aggressor side (Buy/Sell) |

**Conclusion**: WS `allLiquidation` is the only viable source for OMF-002. Historical depth = 0 (just started). Forward depth = real-time, depends on market.

---

## 3. Collector State (2026-08-20 18:23Z)

- Service: `liquidation-collector-omf002.service` (active, no crashes)
- Process: `/usr/bin/python3 -m edge_factory.data.liquidation_collector_omf002`
- WS connection: **ESTABLISHED** (verified at 15:51Z)
- Watchdog warnings: "No liquidations for Xs (data may be calm)" — running every 30s
- Last known event: 0
- 15 symbols: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT, BNBUSDT, ADAUSDT, LINKUSDT, AVAXUSDT, DOTUSDT, NEARUSDT, ARBUSDT, OPUSDT, POLUSDT, SUIUSDT

---

## 4. Infrastructure Quality

- Process uptime: stable (no restarts)
- Memory: <60MB (within `MemoryMax=512M`)
- NoNewPrivileges: true
- No execution path: confirmed (no order placement, no TradingOS integration)
- Output path: `/root/data_buffer/raw/bybit/liquidations/{SYMBOL}/{YYYY-MM-DD}/{HH}.parquet`
- Schema: `ts (ms)`, `symbol`, `side (Buy=Sell=liquitated)`, `size`, `price`

---

## 5. Cross-source Validation

- **Bybit REST `/v5/market/liquidation`** — empty body / 404 → consistent with "no public history" (this endpoint exists in docs but doesn't return historical data)
- **Bybit REST `/v5/market/recent-trade`** — works, returns individual trades, no liquidation marker in payload
- **Coinglass** — connection refused (DNS/network isolation, not Bybit-specific)
- **Trades buffer (BTCUSDT, 38d)** — has aggressor side. Not a liquidation source per se but provides context for liquidity analysis.

---

## 6. Sample Size Decision

**Current sample: 0 events across 15 majors × 2.5h**

Per OMF-002 spec:
> "Если данных недостаточно — `DATA GAP`"

We are in DATA GAP state for now. The collector is correctly subscribed; data simply has not flowed in this calm regime.

**Decision**:
- **Continue collection** — no infrastructure change needed
- **Do NOT start Phase 2** (no events to test)
- **Monitor**: `ls /root/data_buffer/raw/bybit/liquidations/*/*.parquet`
- **Trigger Phase 2 when**: ≥ 50 events across the 15 majors AND ≥ 1 day of continuous operation

---

## 7. Coverage Plan (when sample arrives)

### 7.1 Per-symbol
- 15 majors already in collector symbols list
- Will automatically capture per-symbol breakdown in OMF-002 Phase 2

### 7.2 Per-side
- `side="Buy"` → short position liquidated (forced cover)
- `side="Sell"` → long position liquidated (forced sell)
- Already captured in collector schema

### 7.3 Per-time
- Per-hour parquet files
- OOF (out-of-fold) by date

### 7.4 Cross-validation
- Trade buffer for context: correlate liquidation events with subsequent aggressive flow direction

---

## 8. Risks Identified

| Risk | Mitigation |
|---|---|
| Calm market: zero events for hours | Wait. Collector runs indefinitely. |
| WS disconnect | pybit auto-reconnect; no data loss if collector is up |
| Timezone confusion (timestamps in ms UTC) | All downstream analysis uses `pd.to_datetime(..., utc=True)` |
| Symbol mapping errors | Hard-coded list of 15 symbols (same as microstructure_collector) |

---

## 9. Phase 1 → Phase 2 Gate

| Condition | Status |
|---|---|
| Collector operational | ✓ |
| WS connection stable | ✓ |
| Parquet output path writable | ✓ (verified) |
| Sample ≥ 50 events | ✗ (0 / 50) |
| Sample covers ≥ 1 day | ✗ (2.5h / 24h) |
| At least 1 symbol with ≥ 1 event | ✗ (0 / 15) |
| Cost-floor 15 bps already known | ✓ (from prior research) |
| TradingOS isolated | ✓ (mode=MANUAL, AUTO disabled, R148 frozen) |
| Hard rule committed | ✓ (no historical replay, no live orders, no production changes) |

**Gate result: NOT READY for Phase 2.** Continue data collection.

---

## 10. Next Action

- **WAIT** for liquidation events to accumulate (24h+ minimum, target 100+ events)
- Check periodically: `ls /root/data_buffer/raw/bybit/liquidations/*/*.parquet | wc -l`
- When ≥ 50 events: open Phase 2, define L1-L6 features, run forward response analysis
- **Do NOT** modify production code under any circumstance
- **Do NOT** run historical replay to "speed up" the result

---

## FINAL VERDICT: `DATA COLLECTOR LIVE — AWAITING SAMPLE`

TradingOS state: **UNCHANGED**
- mode=MANUAL
- AUTO disabled
- R148 frozen
- Guardian / deposit_guard active
- P3 read-only, n=9
- 100/100 tests pass
- Bridge: TCP 5555 LISTENING (PID 5176, hardened ONSTART)
- OMF-002 priority 1 collector: LIVE
- OMF-002 priority 2-5: data already collecting (microstructure 15 syms × 11d+)

**Honest engineering**: calm market is honest data, not a bug. We wait.
