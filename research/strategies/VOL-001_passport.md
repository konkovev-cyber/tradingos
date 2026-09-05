# VOL-001: Volatility Compression Breakout — Research Passport

## 1. Market Hypothesis

Периоды низкой волатильности (сжатие) предшествуют периодам высокой волатильности (расширение). Вход в момент расширения после сжатия даёт движение в направлении пробоя.

## 2. Edge Mechanism

| Component | Description |
|-----------|-------------|
| Type | VOLATILITY + MOMENTUM |
| Source | Рынок цикличен: expansion → contraction → expansion |
| Counterparty | Fading-трейдеры, торгующие против breakout |
| Why persistent | Волатильность кластеризуется (GARCH-эффект) |

## 3. Required Data

| Data | Source | Available? |
|------|--------|------------|
| OHLCV M15 | MT5 / Dukascopy | ✅ |
| ATR (14) + percentile (50) | Calculate | ✅ |
| Bollinger Bands width | Calculate | ✅ |
| Donchian Channel (20) | Calculate | ✅ |

## 4. Entry Logic

```
REQUIRED:
  ATR percentile < 20 (compression)
  BB width < 20-period low

LONG:
  Close > Donchian 20 high → ENTRY

SHORT:
  Close < Donchian 20 low → ENTRY
```

## 5. Exit Logic

```
Trailing SL: ATR × 3 (chandelier, trail from extreme)
OR: close opposite side of ATR channel
```

## 6. Expected Trade Frequency

| Estimate | Value |
|----------|-------|
| Expected signals per 1000 candles (M15) | ~5-8 |
| Expected trades per week | ~2-4 |
| Sample size target | 100+ trades |

## 7. Failure Modes

| Mode | Cause | Detection |
|------|-------|-----------|
| False breakout | Price returns inside range | Close inside Donchian next bar |
| Whipsaw | Multiple breakouts | Consecutive false signals |
| No follow-through | Breakout fizzles | Price stalls at 1 ATR |

## 8. Priority

**★★★★** — Sprint 2, complements MR-001 (covers TRANSITION regime)
