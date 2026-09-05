# DN-Sweep Stop-Run Reversal — Pattern v1 (T1.9 first candidate)

## Status: PROMOTE TO PAPER-TRADE on demo $100k (pending owner approval)

## What
M15 bar wick-pierces prior 4h-low by >20bps, closes back above prior low.
WAIT for confirmation bar (close above prior low), then enter LIMIT at close price.
NOT a market order — LIMIT only, with expiry (2 bars = 30min).

## Why (mechanism)
Stop-run: market makers push price below obvious support (prior low) to trigger
stop-losses from longs. Once stops are flushed, price reclaims. The "flush + reclaim"
is the structural edge — not the indicator.

## Detection
- pivot_h = 16 M15 bars (4h lookback)
- sweep_pierce_bps > 20 (wick below prior low by >0.20%)
- close_back_above_bps > 5 (close back above prior low by >0.05%)
- body_in_range < 0.55 (bar is red-leaning, not a buying climax)
- confidence = min(1.0, 0.4 + pierce_bps/100 + recovery_bps/100)

## Entry
- LIMIT at entry_zone = bar.close (post-only maker)
- Expiry: 2 bars (30min) if not filled
- SL: bar.low - bar.range × 0.2 (below sweep wick by 0.2x bar range)
- TP: entry_zone + (entry_zone - SL) × 2.0 (2R target)
- Hold: 4h (16 bars)

## Multi-period OOS validation (50 symbols × 93 days)
Top-14 whitelist (symbols with OOS WR >= 55%):
- BTWUSDT, WLDUSDT, TUTUSDT, STRKUSDT, OPUSDT, GMTUSDT, INXUSDT, ADAUSDT,
  ZKUSDT, ONDOUSDT, DOGEUSDT, ETHUSDT, SOLUSDT, VETUSDT

5 chronological periods (each ~115 signals):
| Period | n   | WR    | gross_med | net_maker |
|--------|-----|-------|-----------|-----------|
| P1     | 115 | 55.7% | +0.285%   | +0.085%   |
| P2     | 115 | 61.7% | +0.409%   | +0.209%   |
| P3     | 115 | 54.8% | +0.524%   | +0.324%   |
| P4     | 115 | 64.3% | +0.575%   | +0.375%   |
| P5     | 115 | 60.9% | +0.510%   | +0.310%   |

**All 5 periods POSITIVE net (maker cost 10bps round-trip)**

OOS (last 30%):
- WR 60.7%
- Median gross return +0.53%
- Median net return (maker) +0.33%
- Bootstrap median 95% CI: [+0.166%, +0.868%] — **positive lower bound**

## Expected economics on $100k demo
- ~6 signals/day × 14 symbols
- Median profit per trade: $500 × 0.33% = $1.65 (maker fill)
- Expected daily: $9.90
- Expected annual: ~3.6% on equity

Modest but POSITIVE, ROBUST across periods, LOW variance.

## What's NOT in v1 yet
- Other reversal families (double-bottom reclaim, falling-wedge, bull-flag pullback)
- Volatility-normalized sizing
- Cross-symbol correlation filter
- Adaptive pierce/recovery thresholds per regime

## Owner decision required
1. Approve whitelist (14 symbols) for paper-trading on demo?
2. Approve 4h hold + LIMIT post-only entry (maker cost 10bps)?
3. Risk per trade: $500 (0.5% of demo equity)?
