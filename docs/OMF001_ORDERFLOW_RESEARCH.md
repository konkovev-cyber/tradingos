# OMF-001 — ORDERFLOW / MICROSTRUCTURE EDGE RESEARCH

**Status:** COMPLETE — branch closed
**Date:** 2026-08-20
**Verdict:** `NO MICROSTRUCTURE EDGE FOUND`
**Production:** MANUAL, R148 frozen, untouched

---

## 1. Executive Verdict

**`NO MICROSTRUCTURE EDGE FOUND`** at tradeable horizon on available data.

| | |
|---|---|
| OOS NET | inconclusive (all signal spreads < cost floor) |
| Capacity | n/a (no tradeable signal exists) |
| Truth | honest |

Decision rule satisfied: signal sizes 0.3–4.5 bps; **cost floor 15 bps** (taker roundtrip + slippage). Every tested microstructure feature is cost-fragile.

---

## 2. Data Availability

| Source | History | Freq | Symbols | Usable |
|---|---|---|---|---|
| **WS trades buffer** (collector) | 38 d (13.07→19.08) | tick | BTCUSDT only | ✓ |
| **Microstructure collector** (live) | 11.7 d (08.08→20.08) gapless | 30s | **15 liquid majors** | ✓ |
| Orderbook buffer (old) | 21 d (14.07→03.08) | ~10s | 21 sym | partial (stale) |
| Klines REST | 365+d | 1m+ | all | ✓ |
| OI REST | 8d (1h) / 33d (4h) / 200d (1d) | 1h+ | all | ✓ (short) |
| Funding REST | ~66 d cap | 8h | all | ✓ |
| **Liquidations REST** | **HTTP 404 — no public history** | — | — | **DATA GAP** |
| REST recent-trade | startTime ignored | — | — | ✗ no history |

---

## 3. Information Value (Phase 2)

| Feature | ρ fwd 60m (TRAIN/TEST) | Quintile spread | Verdict |
|---|---|---|---|
| taker_imb_5m | +0.011 / +0.013 | 1.5–2.5 bps | REJECT |
| imb_10/20/50 (book) | -0.011…+0.046 | ~1–3 bps | REJECT |
| spread_bps | +0.005 / -0.015 | — | REJECT |
| Extreme taker events (\|imb\|>0.8) | TEST: extreme BUY +7.3bps vs NEUTRAL +12.9 — агрессия *хуже* нейтрала | — | REJECT |
| Per-symbol ρ (TEST) | -0.082…+0.068, mixed signs | — | no consistency |

Critical: spread 1–2.5 bps; cost floor 15 bps; **ratio 6–10×**. Increasing sample size cannot close this gap.

---

## 4. Hypothesis Results (Phase 3) — 10 hypotheses

All tested on real microstructure data (15 syms × 11.7 d, n=50,429 pooled 5m buckets, train 70%/test 30%).

| ID | Hypothesis | TEST effect | Verdict |
|---|---|---|---|
| **M1** | Taker imbalance → forward | ρ=+0.017 (15b), spread Q1→Q5 = **+4.5 bps** at 75m horizon | **REJECT** (cost-fragile: 15bps floor vs 4.5bps signal) |
| **M2** | Delta acceleration → forward | \|ρ\| ≤ 0.006 at all horizons | **REJECT** (no signal) |
| **M3** | Absorption (high taker vol → smaller fwd) | TEST diff = **-0.08 bps** (high-vol ≈ rest) | **REJECT** (no effect) |
| **M4** | Exhaustion (extreme flow → reversal) | TEST EXTREME reversal 49% vs NEUTRAL 52% — **less reversal at extreme** | **REJECT** (counter-signal) |
| **M5** | Book imbalance (imb_10) → forward | \|ρ\| ≤ 0.018 | **REJECT** (no signal) |
| **M6** | Liquidity shock (spread ↑ + depth ↓) → adverse | TEST diff = **-1.86 bps** (shock ahead worse by ~1.9bps, but noisier) | **REJECT** (cost-fragile) |
| **M7** | Liquidation pressure | — | **DATA GAP** (REST 404) |
| **M8** | OI + price + flow interaction | — | **DATA GAP** (OI history 8d@1h only within microstructure window) |
| **M9** | Trade intensity shock (n_trades_z>2) → continuation | Insufficient sample (rolling z-score NaN) | **INCONCLUSIVE** |
| **M10** | Cross-sectional (sym−BTC taker_imb) → forward | \|ρ\| ≤ 0.019 | **REJECT** (no signal) |

**Aggregate:** 7 REJECT, 2 DATA GAP, 1 INCONCLUSIVE. **0 PASS, 0 CONDITIONAL.**

---

## 5. Failed Hypotheses — why each closed

| ID | Reason for closure |
|---|---|
| M1 | Spread 4.5bps < 15bps cost floor (3.3×) |
| M2 | All ρ ≤ 0.006 — pure noise |
| M3 | HIGH-vol ahead ≠ rest ahead (no differential) |
| M4 | Extreme flow gives *less* reversal (49% vs 52%) — wrong direction signal |
| M5 | All ρ ≤ 0.018 — order-book imbalance shows no systematic 5m-75m edge |
| M6 | -1.86bps differential but within noise band |
| M7 | Liquidation REST 404; WS collector not deployed |
| M8 | OI 1h history limited to 8d, microstructure 11.7d → insufficient overlap |
| M9 | Insufficient rolling-window sample for z-score |
| M10 | All ρ ≤ 0.019 — no cross-sectional microstructure signal |

---

## 6. Capacity

| Metric | Value |
|---|---|
| Min order size | BTC ~0.001 (~$80 at $80k) |
| Our capital | $80 |
| Tradeable size | 1 position max @ 0.001 BTC |
| Expected slippage at our size | 2–5 bps |
| Roundtrip cost | ~15 bps |
| Max tradeable signal | 4.5 bps (TEST M1 75m) |
| Daily turnover limit | 1–2 trades at our liquidity |

Edge is **structurally cost-fragile** at our capital. Even at higher capital, signal sizes are at noise band.

---

## 7. Final Decision

**Q: Есть ли новый information edge в order-flow/microstructure, которого не было в старой EMA20/EMA50 architecture?**

**A: NO.**

- 8 of 10 microstructure hypotheses returned effect sizes < 4.5 bps
- Cost floor (taker 11 bps + slippage 4 bps) = 15 bps
- **Every tested signal is 3–50× smaller than the cost floor**
- Larger sample size cannot close this gap (effect size is the limit, not statistical power)
- M1 (best) shows +4.5 bps at 75m horizon in TEST — small positive, but inconsistent at shorter horizons and inside noise band

**Architecture verdict:** current EMA20/EMA50 direction + all tested microstructure features cannot produce tradeable edge at our capital and costs. Not a bug in any single component — a structural economic limit.

---

## 8. Next Action

`NO NEXT TRADING IMPLEMENTATION — research branch closed.`

**OMF-001 closed.** No new exit/entry/timing/filter/microstructure research on the same architecture.

**Options that remain (not auto-executed):**

1. **Different time horizon** — sub-minute + maker-only (cost floor ~4–5 bps instead of 15) requires fundamentally different execution infrastructure
2. **Different data** — full order-book reconstruction + cross-exchange arbitrage requires dedicated real-time collection beyond REST APIs
3. **Different universe** — equity/macro/spot vs perps regime, or market-making structure
4. **Accept current state** — MANUAL only on confirmed setups, no AUTO

---

## 9. Status of TradingOS

| | |
|---|---|
| mode | MANUAL |
| AUTO | DISABLED |
| R148 | frozen |
| exit / entry / risk / cap / Guardian / deposit_guard | untouched |
| P3 shadow | read-only, n=9 |
| Microstructure collector | still running (live, 11.7d+ data accumulating) |
| Production | FROZEN, safe |
| Daily loss | -$0.52 (last full day, recovered next morning) |

---

**FINAL:** OMF-001 branch closed. No microstructure edge at tradeable horizon on accessible data. The HONEST answer — no new information edge — is a valid research result.