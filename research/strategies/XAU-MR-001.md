# XAU-MR-001: Gold VWAP Mean Reversion

## Market: XAUUSD
## Timeframe: M5
## Based on: MR-001 (BTC) → adapted for Gold

## Key Differences from BTC

| Factor | BTC | XAUUSD |
|--------|-----|--------|
| Trading hours | 24/7 | Session-based |
| Volatility | Higher, erratic | Lower, structured |
| Spread | 0.01% | 0.2-0.5 pips |
| Liquidity | Variable | High during London/NY |
| Gaps | Frequent | Rare |
| News impact | Crypto-specific | Macro (NFP, FOMC, CPI) |

## Hypothesis

Gold in Asian session forms a range. Price deviations from VWAP during low-volatility periods revert. London/NY sessions provide liquidity for reversion.

## Entry Logic

```
REQUIRED:
  ADX < 20 (RANGE)
  ATR percentile 20-60
  Asian session preferred (00:00-08:00 GMT)

LONG:
  Close < LowerBB (2σ below VWAP)
  Next bar closes back inside → ENTRY

SHORT:
  Close > UpperBB
  Next bar closes back inside → ENTRY
```

## Exit Logic

```
TP: VWAP
SL: ATR × 1.5
Max hold: 24 bars
```

## Risk Adjustments for Gold

- Spread: 0.2-0.5 pips → SL must be > spread × 5
- News blackout: NFP, FOMC, CPI, ECB — 30 min before/after
- Session filter: only Asian and early London (00:00-12:00 GMT)

## Priority

★★★★★ — Next sprint after BTC cycle
