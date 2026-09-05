# RISK/REWARD EDGE HUNT — Final Report

*Read-only research. Production FROZEN (mode=MANUAL, R148, AUTO disabled, P3 n=9 read-only).*

---

## VERDICT: `NO ECONOMIC RR EDGE FOUND`

---

## 1. Method

- **Data**: 20 liquid Bybit perps × 8,640 M15 bars each (90 days, 2026-05-22 → 2026-08-20) — real OHLCV
- **Events**: 44,183 across 6 pre-registered families (no indicator optimization)
- **Entry**: close of signal bar (taker), SL = 1.0×ATR14 and 1.5×ATR14 variants
- **LE-conservative intrabar**: stop and target in same bar → STOP counts first
- **Splits**: TRAIN 60% / VAL 15% / OOS 25% (chronological by event ts)
- **Costs**: taker RT 11 bps + slippage 2 bps = 13 bps, converted to R via sl_bps per trade
- **Targets ladder**: 1R/2R/3R/4R, horizon 96 bars (24h)

## 2. Event families

| Family | Definition | n |
|---|---|---:|
| A1 RANGE_BREAK | close > prior 20-bar high (LONG) / < low (SHORT) | 14,963 |
| A2 COMPRESS_EXPAND | ATR pct-rank <20% + bar range >2×ATR, direction = bar | 1,560 |
| B1 SWEEP_RECLAIM | wick beyond 20-bar extreme, close back inside | 15,823 |
| B2 FAILED_BREAK | prior bar broke range, current closed back inside | 4,224 |
| C1 EXTREME_DEV | close > EMA20 ± 3×ATR (mean-revert direction) | 7,609 |
| D1 IMPULSE_PULLBACK | 3 directional bars >2.5 ATR + small counter bar | 4 → INSUFFICIENT |

## 3. Raw payoff asymmetry EXISTS (gross, before costs)

Best families at SL=1.5×ATR, OOS:

| Family | T | WR OOS | ExpGross R | ExpNet R | PF | NoTop3 |
|---|---|---:|---:|---:|---:|---:|
| A2 | 3R | 35% | +0.451 | **−0.235** | 0.78 | −0.257 |
| B2 | 3R | 29% | +0.303 | **−0.044** | 0.95 | −0.054 |
| B1 | 3R | 27% | +0.251 | **−0.100** | 0.89 | −0.103 |
| B2 | 2R | 39% | +0.236 | **−0.111** | 0.86 | −0.118 |

**Positive gross expectancy exists** (B2 @3R gross +0.303R, A2 @3R +0.451R) — the RR-asymmetry the hunt was looking for is REAL at the gross level. OOS WR is stable vs TRAIN (no IS/OOS flip on hit rates; A2 even improves OOS: 25%→35% @3R).

## 4. But costs destroy every family

**Cost_R is the killer**: median SL = 41 bps (SL=1.0×ATR) / 62 bps (SL=1.5×ATR) → cost_R = 13/41 = **0.314R** (SL=1.0) or 13/62 = **0.209R** (SL=1.5) per round trip.

| Best case (B2@3R, SL=1.5) | Value |
|---|---:|
| Gross expectancy | +0.303R |
| Cost per trade | −0.209R |
| **NET expectancy** | **−0.044R** |
| NET after anti-cherry (no top-3) | −0.054R |
| Median NET | −1.195R |

**Every single family × SL × target combination is NET-negative OOS.** The best (B2@3R SL=1.5, −0.044R) is within noise of zero but still negative, and median trade is deeply negative.

## 5. Anti-cherry / per-symbol / side (B2 @2R, SL=1.5, OOS)

- **Per-symbol**: 9 positive / 11 non-positive out of 20 symbols (not concentrated — the edge absence is broad, not cherry)
- **Side**: SHORT +0.002R, LONG −0.244R — no stable side edge
- **NoTop3**: remains negative (−0.118R) — result not driven by outliers; it's uniformly ~zero-to-negative

## 6. Random control

Pooled unconditional WR@2R=37% → random-direction baseline expNET = **−0.213R**. The best families (B2 −0.111R, A2 −0.381R... wait A2@2R is −0.381) beat random by ~+0.1R at gross level — a real but sub-cost selection effect.

## 7. Why this fails — the structural math

The market DOES offer RR-asymmetry (gross positive at 2R/3R targets on breakout-failure and compression-expansion events). But:

1. **ATR-based stops on 15m crypto are ~41-62 bps** — small
2. **Taker cost floor is 13 bps** = 21-32% of the entire risk unit
3. To clear costs, a family needs gross expectancy ≥ 0.21-0.31R; best observed OOS gross = 0.30-0.45R at 3R targets, i.e. **1.4-1.5× cost, leaving ≈ nothing after slippage stress**
4. Under LE-conservative intrabar rules and 24h horizon, median trade never reaches target (median NET ≈ −1.2R = stop)

**Break-even requirement**: gross WR@3R ≈ (1+cost_R)/3 → 40% (SL=1.5). Best OOS WR@3R = 35% (A2). Gap: 5pp — again within cost friction, not a signal failure.

## 8. Comparison with prior waves

Consistent with: VEX-001 (+33bps gross vs 60bps floor), H2 (+25.8bps OOS @maker-only 4.2bps fee), T32.F ($8.8 headroom unmonetizable). This is now the **4th independent confirmation** that liquid-crypto M15 events carry real but sub-cost gross edges at taker execution.

## 9. What could change the verdict

| Lever | Required |
|---|---|
| Maker execution (4 bps RT → cost_R ≈ 0.065R) | B2@3R NET → **+0.24R OOS** — becomes positive but needs real fill-rate/AS validation (prior H1: fill 23-44%, AS −3bps symmetric → net effect ≈ 0) |
| Wider stops (SL = 3×ATR ≈ 124bps) | cost_R → 0.10R, but WR@3R drops as risk widens; not tested per anti-optimization rule |
| Larger targets only | median trade stays −1R; expectancy already best at 3R |

**The only surviving path remains maker-entry validation (H2 infrastructure)** — everything else closes.

## 10. Files

- /tmp/rr_edge/{SYM}_m15.parquet — 90d raw klines × 20 syms
- /tmp/rr_edge/build_events.py — event library builder
- /tmp/rr_edge/events.parquet — 44,183 events
- /tmp/rr_edge/rr_ladder.py + economics.py — analysis
- /tmp/rr_edge/outcomes_sl{1.0,1.5}.parquet — forward outcomes

## Production Safety (UNCHANGED)

mode=MANUAL, AUTO disabled, R148 frozen, P3 n=9 read-only, Guardian/deposit_guard active, 100/100 tests pass, Bridge TCP 5555 LISTENING, all collectors live.
