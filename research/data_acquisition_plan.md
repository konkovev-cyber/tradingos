# Data Acquisition Plan

## Цель

Обеспечить независимые источники исторических данных для backtest и walk forward валидации стратегий.

## Уровни данных

| Level | Source | Use | Cost |
|-------|--------|-----|------|
| L1 | Dukascopy | Research backtest | Free |
| L2 | MT5 Broker (RoboForex) | Final validation | Free (demo) |
| L3 | Live feed (Bybit/MT5) | Shadow execution | Free |

## L1: Dukascopy Historical Data

### Доступ

- Бесплатный CSV-экспорт через Dukascopy Historical Data Feed
- Формат: `[DateTime, Open, High, Low, Close, Volume]`
- Таймфреймы: tick, M1, M5, M15, H1, H4, D1
- Инструменты: XAUUSD, EURUSD, BTCUSDT и 1000+ других

### Формат хранения

```
data/raw/
  xauusd/
    M5/
      2024-01.csv
      2024-02.csv
      ...
  eurusd/
    M5/
      ...
```

### Процесс загрузки

```python
# Псевдокод
def download_dukascopy(symbol, timeframe, year, month):
    url = f"https://datafeed.dukascopy.com/datafeed/{symbol}/{year}/{month:02d}/"
    # Скачать ZIP, распаковать CSV
    # Сохранить в data/raw/{symbol}/{timeframe}/
```

## L2: MT5 History

### Доступ

- MT5 Terminal → View → Symbols → Bars
- Экспорт через CopyRates() в Python
- Формат: OHLCV + spread + tick_volume

### Использование

- Финальная верификация после Dukascopy backtest
- Проверка: совпадают ли результаты на разных источниках

## L3: Live Feed

### Доступ

- Bybit testnet (crypto) — уже есть
- MT5 Demo (forex) — уже есть

### Использование

- Shadow execution (текущий LS-001)
- Проверка latency, slippage, spread в реальном времени

## Data Quality Checks

| Check | Method | Pass criteria |
|-------|--------|---------------|
| Completeness | Count bars per day | > 95% expected |
| Gap detection | Price jump > 5 ATR | Flag for review |
| Spread outliers | Spread > 5× median | Flag for review |
| Cross-source | Dukascopy vs MT5 | Correlation > 0.99 |

## Storage Format

```
data/
  raw/          # Original CSV/ZIP from sources
  processed/    # Parquet with standardized schema
  cache/        # Feature-engineered datasets
```

## Schema (processed)

```yaml
symbol: str
timestamp: datetime (UTC)
open: float
high: float
low: float
close: float
volume: float
spread: float (if available)
source: str  # "dukascopy" | "mt5" | "bybit"
```
