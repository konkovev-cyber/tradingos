# SESSION-001: Asian Range Breakout — Research Passport

## 1. Market Hypothesis

Азиатская сессия формирует диапазон (баланс). Лондонская сессия пробивает этот диапазон. После ложного пробоя цена возвращается внутрь диапазона — классический liquidity sweep на сессионных уровнях.

## 2. Edge Mechanism

| Component | Description |
|-----------|-------------|
| Type | BEHAVIORAL + MICROSTRUCTURE |
| Source | Азия: накопление ликвидности. Лондон: крупные игроки снимают стопы |
| Counterparty | Трейдеры, входящие на пробой без подтверждения |
| Why persistent | Сессионная структура — фундаментальное свойство 24h рынка |

## 3. Required Data

| Data | Source | Available? |
|------|--------|------------|
| OHLCV M5 | MT5 / Dukascopy | ✅ |
| Asian High/Low (00:00-08:00 GMT) | Calculate | ✅ (нужен session detector) |
| ATR (14) | Calculate | ✅ |
| Session timestamp | Built-in | ✅ |

## 4. Entry Logic

```
REQUIRED:
  Asian session closed (08:00 GMT)
  Asian range > 0.5 × ATR (meaningful range)

LONG:
  London: price breaks above Asian High
  Price closes back BELOW Asian High → ENTRY

SHORT:
  London: price breaks below Asian Low
  Price closes back ABOVE Asian Low → ENTRY
```

## 5. Exit Logic

```
TP: ATR × 2 from entry
SL: beyond Asian extreme + buffer (Asian Low - 0.5 ATR for LONG)
Max hold: until NY close (21:00 GMT)
```

## 6. Expected Trade Frequency

| Estimate | Value |
|----------|-------|
| Expected signals per day | ~0.5-1.5 |
| Expected trades per week | ~3-7 |
| Sample size target | 100+ trades |

## 7. Failure Modes

| Mode | Cause | Detection |
|------|-------|-----------|
| Strong trend day | No return after breakout | Price continues beyond 2 ATR |
| News overlap | FOMC/NFP during London | Calendar filter |
| No Asian range | Range < 0.5 ATR | Skip trade |
| Asian range too wide | Range > 3 ATR | Skip trade (breakout already happened) |

## 8. Priority

**★★★★** — Sprint 2, gold-specific, high potential
