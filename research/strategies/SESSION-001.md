# SESSION-001: Asian Range Breakout

## Hypothesis

Азиатская сессия формирует диапазон (баланс). Лондонская сессия пробивает этот диапазон. После ложного пробоя цена возвращается внутрь диапазона.

## Market Logic

- Азия: низкая волатильность, накопление ликвидности
- Лондон: крупные игроки входят, снимают стопы за азиатскими уровнями
- Ложный пробой → возврат — классический liquidity sweep на сессионных уровнях
- Контрагенты: трейдеры, входящие на пробой без подтверждения

## Data Requirements

- OHLCV
- Asian High / Asian Low (00:00-08:00 GMT)
- ATR
- Session timestamp

## Entry Logic

```
Условия:
1. Asian session closed (08:00 GMT)
2. London session: price breaks Asian High or Asian Low
3. Price closes BACK inside Asian range

Вход:
- LONG: break above Asian High, close below Asian High
- SHORT: break below Asian Low, close above Asian Low
```

## Exit Logic

```
- TP: ATR × 2 from entry
- SL: beyond Asian extreme (Asian Low - buffer for LONG, Asian High + buffer for SHORT)
- Max hold: until NY close (21:00 GMT)
```

## Failure Modes

| Mode | Cause | Detection |
|------|-------|-----------|
| Strong trend day | No return after breakout | Price continues beyond 2 ATR |
| News overlap | FOMC/NFP during London | Calendar filter |
| No Asian range | Range < 0.5 ATR | Skip trade |

## Priority

★★★★★

## Complexity

MEDIUM — нужен session detector + Asian range calculator
