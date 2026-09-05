# Conditional Research Report v1

## Date: 2026-07-11

## Executive Summary

| Strategy | Variant | Trades | WR | PF | Net PnL | Verdict |
|----------|---------|--------|----|----|---------|---------|
| LS-001 | B (3×/4.5× ATR) | 2,898 | 53.0% | 1.33 | +5,210 | **CANDIDATE** |
| MR-001 | B (3×/4.5× ATR) | 2,346 | 32.7% | 0.71 | -6,009 | REJECT |
| TF-001 | n/a | 1 | 0% | n/a | -0.25 | REJECT (too rare) |
| VOL-001 | n/a | 0 | n/a | n/a | 0 | REJECT (no signals) |

## LS-001 Deep Dive (the survivor)

### Overall: 2,898 trades, 53% WR, PF 1.33, Net +5,210

### Where edge lives:

| Dimension | Bucket | Trades | WR | PF | PnL |
|-----------|--------|--------|----|----|------|
| ADX | Low (<15) | 2,892 | 54.5% | 1.33 | +6,848 |
| ATR%ile | Mid (30-70) | 1,044 | 56.5% | 2.01 | +4,723 |
| Day | Saturday | 405 | 59.5% | 2.55 | +3,054 |
| Day | Wednesday | 417 | 53.5% | 0.93 | -305 |
| Hour | 12:00 | 152 | 59.2% | 3.09 | +1,511 |
| Hour | 17:00 | 120 | 54.2% | 3.36 | +971 |
| Hour | 10:00 | 110 | 50.0% | 0.57 | -697 |

### Edge is concentrated in:
- **ADX < 15** (99.8% of all trades — entire edge here)
- **Mid ATR volatility** (PF 2.01)
- **Weekend sessions** (Saturday PF 2.55)
- **US/London hours** (12, 17, 19, 21 — PF > 2.5)

### Next hypothesis (not parameter fishing):
LS-001 v2: same logic + filter `ADX<15 AND hour in {12,17,19,21} AND day != Wed`
This is CONDITIONAL ANALYTICS, not optimization.

## MR-001 Autopsy

### Overall: 2,346 trades, 32.7% WR, PF 0.71

No edge in any regime. All hour buckets PF < 1 except hour 10 (PF 3.16, 116 trades, n/s).
Saturday worst: PF 0.23, -2,273.

**Verdict: Strategy concept is wrong for BTC 5m.** Mean reversion via Bollinger Bounce doesn't work with 3×ATR stops on BTC — the trend is too strong, the bounce never reaches TP.

## Lessons Learned

1. **Bug in signal metadata pipeline**: `atr` and `current_price` were not being passed from strategy → SigObj → executor. Fixed. Without this fix, ALL trades hit EXPIRY (100% vs realistic SL=74%/TP=21%/EXPIRY=5%).

2. **Profile A (1.5×ATR) is too tight for BTC 5m**: 74.8% SL, positions die immediately. Profile B (3×ATR) lets winners breathe.

3. **Edge lives in conditions, not in the base strategy**: LS-001 is profitable overall, but the edge is concentrated in 3 specific dimensions. Without conditional analysis, you wouldn't know this.

4. **TF-001 and VOL-001 are too rare**: TF-001 found 1 signal in 103k H1 candles. VOL-001 found 0 signals in 34k M15 candles. These are not strategies — they are filters that never fire.

## Next Steps

1. Build LS-001 v2 with conditional filter (ADX<15, hour∈{12,17,19,21}, day≠Wed)
2. Replay LS-001 v2 — expect fewer trades but higher PF
3. If LS-001 v2 passes Walk Forward, prepare MT5 Demo Package
4. Parallel: prepare XAUUSD equivalent (different market, different behavior)
