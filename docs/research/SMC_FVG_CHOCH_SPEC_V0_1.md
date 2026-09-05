# SMC-FVG-CHOCH Research Spec v0.1

**Status:** CANDIDATE (not implemented)
**Type:** Signal Engine v0.2 / Research Candidate
**Date:** 2026-07-21

---

## Hypothesis

Smart Money Concepts (CHOCH + FVG + Fibonacci confluence) can identify high-probability reversal entries with R:R > 2:1.

---

## Signal Pipeline

### Step 1 — Market Structure Detector

Identify trend and detect Change of Character (CHOCH):

```
Downtrend pattern:
  LH → LL → LH → LL

Then:
  Price breaks previous High
  = CHOCH (trend reversal signal)
```

Requires formal definition:
- Swing: 5 candles left + 5 candles right (or ATR-based)
- Break: close above/below previous swing point
- Confidence: volume + displacement confirmation

### Step 2 — FVG Detector

Fair Value Gap = 3-candle pattern:

```
Bullish FVG:
  Candle1 High < Candle3 Low
  = Gap between them
  
  Top: Candle1 High
  Bottom: Candle3 Low
  Mid: (Top + Bottom) / 2
```

Filters needed:
- Minimum gap size (ATR-based)
- Volume confirmation
- Session filter (London/NY only)

### Step 3 — Fibonacci Confluence

FVG location relative to recent swing:
- 50%-61.8% retracement zone
- If FVG overlaps → VALID
- If outside → SKIP

### Step 4 — Entry Model

```
Entry: FVG midpoint
SL: below FVG low (or recent swing low)
TP: 2R, 3R, or next structure level

Position size: 1R risk
Risk per trade: 0.25-0.5%
```

---

## Known Weaknesses

1. **Subjectivity** — CHOCH/FVG detection varies by algorithm. Need strict formal rules.
2. **FVG abundance** — most FVGs are noise. Need volume + session + displacement filters.
3. **Backtest difficulty** — 3-4R targets look good on paper, need to verify: consecutive stops, BE rate, false CHOCH rate.

---

## Integration with TradingOS

| Layer | Role |
|-------|------|
| Research Factory | Hypothesis validation, backtest |
| Signal Engine | CHOCH detector → FVG detector → Entry |
| Risk Governor | Position sizing, 1R risk |
| RTS Guardian | BE, trailing, profit lock |
| Reality Engine | KPI: rule adherence, avg R, expectancy |

---

## Decision

**Status:** CANDIDATE — not for implementation until:
1. Formal CHOCH rule set written
2. FVG detector with filters designed
3. Backtest protocol defined
4. 1000+ historical setups validated

**Next step when ready:** Create as Signal Engine v0.2 in Research Factory.
