# FINAL EDGE RESEARCH — Non-indicator Market Structure

*One research project. Six families. Hard OOS NET criterion. NO continuation of T32 / OMF research. Read-only.*

---

## 1. Executive Verdict

**`NO ECONOMIC EDGE FOUND`** at tradeable horizon on accessible data.

- 6 families × 13 hypotheses tested
- Best TEST spread: **A3 (relative momentum) +11.81 bps** — *appears* close to cost floor but anti-cherry fails (median per-symbol non-positive), likely test-set artifact
- All other families: signal spreads 0.2-3.7 bps against 15 bps cost floor (5-50× cost-fragile)
- **0 PASS, 0 CONDITIONAL, 0 PROMISING**

---

## 2. Data Inventory

| Source | Coverage | Freq | Usable |
|---|---|---|---|
| Microstructure collector (15 liquid majors × 11d+) | 30s | ✓ | ✓ |
| Klines REST (all symbols) | 365+d | 1m+ | ✓ |
| Funding REST (66d cap) | 8h | ✓ | ✓ |
| OI REST (8d@1h / 33d@4h) | 1h-1d | ✓ | short |
| Liquidation REST | — | — | DATA GAP (404) |

---

## 3. Hypothesis Results (6 families × 13 hypotheses)

| Family | ID | Hypothesis | TRAIN spread | TEST spread | Verdict |
|---|---|---|---:|---:|---|
| **A — Cross-sectional** | A1 | BTC vs sym next-N-bucket relative return | ~+0.1 bps | **−0.7 bps** | REJECT |
| | A2 | Cross-sectional taker-imbalance divergence | ~±0.5 bps | **−1.2 bps** (5b) | REJECT |
| | **A3** | **1h-relative momentum quintile** | **+2.00 bps** | **+11.81 bps** | REJECT (anti-cherry fails — non-positive per-symbol) |
| **B — Vol/regime** | B1 | Realized vol (24h) regime → fwd | -0.6 bps | +4.1 bps | REJECT |
| | B2 | Vol expansion (1h/24h) → fwd | -1.5 bps | +6.2 bps | REJECT (TRAIN negative) |
| **C — Funding/basis** | C1 | Funding quintile → 4h fwd | insufficient kline overlap (DATA GAP for fwd at funding ts) | — | INCONCLUSIVE |
| | C2 | OI × price quadrant (8d@1h only) | DATA GAP | — | DATA GAP |
| | C3 | Funding × taker flow interaction | not run (C1 DATA GAP) | — | DATA GAP |
| **D — Event dislocation** | D1 | Spread shock → fwd | -0.5 bps | -0.3 bps | REJECT |
| | D2 | Depth withdrawal → fwd | +0.2 bps | **-3.6 bps** | REJECT |
| | D3 | Volume z>2 → fwd | +2.0 bps | -1.8 bps | REJECT (TRAIN+TEST sign flip) |
| **E — Time regimes** | E1 | UTC hour group → fwd | max-min 3.2 bps | max-min 10.3 bps | REJECT (high drift, fragile) |
| | E2 | BTC regime (1h pos) → fwd | +1.4 bps | **+8.4 bps** | REJECT (test-set artifact; sample too short) |
| **F — Capacity** | F1 | Taker_imb signal by volume bucket | ρ ≤ 0.023 (all) | ρ ≤ 0.023 (all) | REJECT (no edge in low/mid/high volume) |

**Aggregate: 11 REJECT, 1 INCONCLUSIVE, 1 DATA GAP. 0 PASS.**

---

## 4. Best Survivor

**NONE.** The largest TEST effect (+11.81 bps, A3) fails per-symbol anti-cherry (median per-symbol spread non-positive), suggesting test-set artifact. Cross-symbol aggregation is misleading.

---

## 5. False Positives

| Family | TEST effect | Why failed |
|---|---|---|
| A3 | +11.81 bps | per-symbol median non-positive, OOS artifact |
| E2 | +8.4 bps | period drift (TEST period +10 bps/day avg vs TRAIN −0.5 bps/day avg); not regime-related, just drift |
| B2 | +6.2 bps | TRAIN -1.5 bps (opposite sign), unstable |

---

## 6. Data Gaps (would have changed verdict)

| Gap | Limitation |
|---|---|
| **Liquidations history** | REST 404; would have enabled C-tier signal families |
| **OI 1h history > 8d** | current data doesn't overlap with microstructure window; C2/C3 need ≥20d |
| **Funding history > 66d** | REST caps at 66d, would enable longer walk-forward for C1 |
| **Top-of-book tick data per trade** | WS collector stores trades but not aggregated to 1-second features |
| **Cross-venue data (Binance spot, OKX futures)** | would enable family A1 with smaller bid-ask basis |

None of these gaps can be filled without new collection infrastructure (WS liquidations + cross-venue feeds).

---

## 7. Final Economic Verdict

| Family | Best TEST result | Verdict |
|---|---|---|
| A — Cross-sectional relative value | +11.81 bps (anti-cherry fail) | NO-GO |
| B — Volatility/regime | +6.2 bps (TRAIN neg) | NO-GO |
| C — Funding/basis/positioning | INCONCLUSIVE / DATA GAP | DATA GAP |
| D — Event dislocation | -3.6 bps | NO-GO |
| E — Time/regime | +8.4 bps (period drift, fragile) | NO-GO |
| F — Capacity edge | ρ≤0.023 (all buckets) | NO-GO |

**Final verdict: `NO ECONOMIC EDGE FOUND`** at tradeable horizon on accessible data.

Cost floor ~15 bps (taker roundtrip + slippage) is 4-50× larger than all observed signal spreads.

---

## 8. Next Action

`NO NEXT TRADING IMPLEMENTATION — research branch closed.`

**FINAL STANCE: At current universe (15 liquid majors × small-cap via order flow) + 5m-75m horizon + accessible data (REST-only) + $80 capital + taker execution + 15 bps cost floor — no demonstrable systematic edge exists in:**

- Direction (H4 EMA20/EMA50 — 9 alternative models rejected)
- Entry filters (8 features tested, NO-GO)
- Timing (counterfactual shifts NO-GO)
- Exit engine (T32.A-K NO-GO, perfect-MFE $8.816 headroom exists but no online signal)
- P3 live-shadow (n=9 Δ −$0.556, NOT CONFIRMED)
- Microstructure (OMF-001: 7 REJECT, 2 DATA GAP)
- Cross-sectional / vol / funding / event / time / capacity (FINAL: 11 REJECT, 1 INCONCLUSIVE)

**Honest answer: at current capital and cost structure, on accessible data and current universe, no economic edge survives costs and OOS.**

---

## 9. Status of TradingOS (unchanged)

| | |
|---|---|
| mode | MANUAL |
| AUTO | DISABLED |
| R148 | frozen |
| Exit/Entry/Risk/Cap/Guardian/deposit_guard | untouched |
| P3 shadow | read-only, n=9 |
| Microstructure collector | running, 11+ days accumulating |

---

## 10. What remains (options not auto-executed)

1. **Different execution tier**: maker-only (cost floor 4-5 bps instead of 15) requires fundamentally different infrastructure
2. **New data sources**: liquidations history (WS), cross-venue feeds, market-making microstructure
3. **Different universe/timeframe**: equities, macro, spot vs perps
4. **Accept current state**: MANUAL only, no AUTO — and stop searching

The current architectural choice (H4 EMA + $80 capital + taker execution) has been falsified across 6 forensic waves.