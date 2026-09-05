# TF-001: EMA Trend Pullback — Research Passport

## 1. Market Hypothesis

В сильном тренде цена после отката к скользящей средней (EMA50) продолжает движение в направлении тренда. Откат — временная коррекция, а не разворот.

## 2. Edge Mechanism

| Component | Description |
|-----------|-------------|
| Type | MOMENTUM |
| Source | Трендовые трейдеры добавляют позиции на откатах |
| Counterparty | Mean reversion трейдеры, ловящие разворот |
| Why persistent | Тренды — фундаментальное свойство рынков с неравновесным потоком |

## 3. Required Data

| Data | Source | Available? |
|------|--------|------------|
| OHLCV H1 | MT5 / Dukascopy | ✅ |
| EMA50, EMA200 | Calculate | ✅ |
| ADX (14) | Calculate | ✅ |
| ATR (14) | Calculate | ✅ |

## 4. Entry Logic

```
REQUIRED:
  ADX > 25 (trend)
  EMA50 > EMA200 (uptrend) or EMA50 < EMA200 (downtrend)

LONG:
  Price pulls back to EMA50
  Bullish rejection candle (close > open, close > EMA50)
  → ENTRY on close

SHORT:
  Price pulls back to EMA50
  Bearish rejection candle (close < open, close < EMA50)
  → ENTRY on close
```

## 5. Exit Logic

```
Trailing SL: ATR × 2 (chandelier, trail from highest high since entry)
Partial TP: ATR × 3 (close 50%)
Remainder: trail until EMA cross or opposite signal
```

## 6. Expected Trade Frequency

| Estimate | Value |
|----------|-------|
| Expected signals per 1000 candles (H1) | ~5-10 |
| Expected trades per week | ~1-3 |
| Sample size target | 100+ trades |

## 7. Failure Modes

| Mode | Cause | Detection |
|------|-------|-----------|
| Trend exhaustion | ADX falling from high | ADX < 25 after entry |
| Whipsaw | Price chops around EMA | Multiple touches without follow-through |
| Range market | ADX < 25 | Filter |
| News reversal | NFP/FOMC flips trend | Calendar blackout |

## 8. Kill Criteria

| Criterion | Threshold |
|-----------|-----------|
| Profit Factor | < 1.2 |
| Max Drawdown | > 15% |
| Avg Win / Avg Loss | < 1.5 |
| Walk Forward | < 50% periods profitable |

## 9. Priority

**★★★★** — Sprint 2, complements MR-001 (covers TREND regime)
