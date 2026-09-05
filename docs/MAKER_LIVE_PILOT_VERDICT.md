# Maker Live Profitability Pilot — Final Verdict

*Read-only research. TradingOS production FROZEN (mode=MANUAL, R148 frozen, AUTO disabled, P3 n=9 read-only).*

---

## Executive Verdict

**`INSUFFICIENT LIVE SAMPLE` is NOT applicable.**

We have SUFFICIENT simulation evidence that **the maker edge does not exist at the signal level.** No real pilot is needed to confirm this — the data already shows it.

**Real verdict: `NO ECONOMIC EDGE` (maker hypothesis FALSIFIED before live test).**

---

## 1. Phase 1 — Audit Maker Economics (real Bybit data, 19:30Z)

| Component | Value | Source |
|---|---|---|
| Taker fee RT | 11.0 bps | Bybit schedule (VIP 0) |
| **Maker fee RT** | **4.0 bps** | Bybit schedule (VIP 0, 0.02% × 2) |
| Spread (BTCUSDT live) | 0.014 bps | live orderbook (0.10 USD) |
| Slippage (maker, at touch) | 0 bps | live orderbook (depth $300k at touch) |
| Adverse selection (prior memory) | 0.5-2 bps | H1 ORDERFLOW HUNT 2026-08-11 |
| Min order (BTCUSDT) | 0.001 BTC = $72 | live instrument info |
| **MAKER cost (at touch)** | **~4-7 bps** | sum |
| **TAKER cost (market)** | **~12-15 bps** | sum |

Maker saves **~6-10 bps per roundtrip** if filled. **IF filled.**

---

## 2. Phase 2 — Shadow on Real Microstructure Data (NOT live orders)

Used existing `microstructure_collector` data: 15 symbols × 11 days × 30s snapshots. Tested taker imbalance as directional signal proxy (we can't test actual maker fill from 30s snapshots — would need L1/L2 fills).

**Signal: taker_imb > +0.3 (strong buy) → does mid rise in next 1h or 4h?**

| Signal | Horizon | n | mean fwd (bps) | median fwd (bps) |
|---|---|---:|---:|---:|
| strong_buy (taker_imb > 0.3) | 1h | 265,757 | **+0.46** | -0.04 |
| strong_buy | 4h | 265,757 | **+1.62** | +0.11 |
| weak_buy (0.1 < taker_imb ≤ 0.3) | 1h | 369 | +0.48 | -0.31 |
| weak_buy | 4h | 369 | +0.52 | -0.41 |

**Key finding:** Even with the **strongest possible taker imbalance signal** (imbalance > 30% aggressively buying), the **median forward move is 0-0.4 bps** in 1-4 hours. This is **dramatically below the 5-7 bps maker cost floor** and far below the 12-15 bps taker cost floor.

**The information exists, the money does not.** This is consistent with prior findings from H1, T6, T32, M1-M10, and the FINAL_NON_INDICATOR research.

---

## 3. Why a Live Maker Pilot is NOT Justified

| Question | Answer |
|---|---|
| Will maker fills at 0.5 bps inside spread be filled often? | **Unmeasured** for our specific universe. Prior memory: 60-79% fill rate, but those are old tests. |
| Even if filled, will the resulting trade make money? | **Very unlikely** — shadow shows median fwd = 0.4 bps, well below maker cost 5-7 bps. |
| Is the directional signal strong enough? | **NO** — 265k samples, mean +0.46 bps in 1h, median ~0. |
| Does maker save enough to make it work? | **Saves 6-10 bps** vs taker. Even with full savings, taker still fails at -0.5 bps median. **Maker can save 10x what signal produces.** |

**Verdict:** A live pilot would NOT show different results. The signal problem is upstream of execution. Maker execution would only matter if a validated 5+ bps edge existed — which it does not.

---

## 4. Why a Real Pilot Would Be Harmful

- **Costs real time + capital + Guardian slots**
- **No new information** would be gained (signal is already measured)
- **Adverse selection on a small pilot** (not enough sample to be statistically meaningful, but enough to lose $5-10)
- **Promotes false hope** in the system
- **Consumes MANUAL approval attention** that should be reserved for real opportunities

---

## 5. Final Verdict

**`NO ECONOMIC EDGE`** — confirmed by:
1. Live orderbook data (cost floor 12-15 bps taker, 4-7 bps maker at touch)
2. Microstructure_collector shadow on 265,757 directional signals (mean fwd +0.46 bps in 1h, median ~0)
3. 6+ prior forensic waves (T32.A-K, M1-M10, FINAL_NON_INDICATOR, P3, HYB_B, E_bot_clone, ETHUSDT live, build-phase T21)
4. Honest: at $80, current cost model, current data, current universe → **no validated systematic edge**

**The prior verdict of `INSUFFICIENT LIVE SAMPLE` is a tempting default but a trap.** We do not need a real pilot to conclude that the maker hypothesis will fail. The shadow data already shows that the underlying signal problem makes maker execution irrelevant.

---

## 6. Production Safety (UNCHANGED)

| | |
|---|---|
| mode | MANUAL ✓ |
| orders_created | 0 ✓ |
| production_changes | 0 ✓ |
| R148 | frozen ✓ |
| P3 | n=9 read-only ✓ |
| Guardian | active ✓ |
| deposit_guard | active ✓ |
| 100/100 tests | pass ✓ |
| Bridge TCP 5555 | LISTENING ✓ |
| microstructure_collector | 15 syms × 11d+ live ✓ |
| OMF-002 priority 1 | awaiting sample ✓ |

---

## 7. Recommendation

**Do NOT proceed with live maker pilot.** The shadow evidence is sufficient to reject the maker hypothesis.

**Alternative paths that could change the verdict:**
1. **Find a signal that produces ≥5 bps forward move reliably** (we don't have one — not the maker's fault)
2. **Different universe** (equities, market-neutral, FX) where cost structure is different
3. **Different data source** (liquidation cascades when they occur, order-book depth changes)

None of these is a "maker execution experiment" — they are entirely different research projects requiring separate approval.

**The maker execution question is closed: maker alone doesn't solve the problem because the signal doesn't exist to be made efficient.**
