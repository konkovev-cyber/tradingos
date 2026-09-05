# XAU-VOL-001: Gold Volatility Compression Breakout

## Market: XAUUSD
## Timeframe: M15
## Based on: VOL-001 (BTC) → adapted for Gold

## Hypothesis

Gold exhibits strong volatility compression before major economic releases (NFP, FOMC, CPI). The breakout after compression often produces sustained directional moves.

## Key Differences from BTC

| Factor | BTC | XAUUSD |
|--------|-----|--------|
| Compression trigger | Random | Often before news |
| Breakout direction | Any | Often follows USD |
| False breakout rate | Higher | Lower during London |
| Volume confirmation | Tick volume only | Tick volume only |

## Entry Logic

```
REQUIRED:
  ATR percentile < 20 (compression)
  BB width < 20-period low
  London or NY session (08:00-17:00 GMT)

LONG:
  Close > Donchian 20 high → ENTRY

SHORT:
  Close < Donchian 20 low → ENTRY
```

## Exit Logic

```
Trailing SL: ATR × 3
OR: close opposite side of ATR channel
```

## Risk Adjustments

- News blackout: NFP, FOMC, CPI, ECB — 30 min before/after
- Session filter: London/NY only (Asia too quiet for breakouts)
- False breakout filter: if price returns inside Donchian within 3 bars → exit

## Priority

★★★★ — Next sprint after BTC cycle
