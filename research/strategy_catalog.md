# TradingOS Research Catalog

## Цель

Параллельно текущим тестам — подготовить очередь проверяемых торговых гипотез.
Каждая гипотеза проходит фильтр: рыночное обоснование → данные → эксперимент → verdict.

## Принципы

- Не ищем Святой Грааль. Ищем повторяемые рыночные неэффективности.
- 90% идей умирают до написания кода — это нормально.
- Деньги = следствие доказанного преимущества, а не цель исследования.
- Ни одна стратегия не идёт на реальный счёт без: History + Walk Forward + Shadow Demo.

## Приоритет рынков

1. **XAUUSD** — уже есть MT5, RoboForex, инфраструктура
2. **EURUSD** — стабильность, высокая ликвидность
3. **BTCUSDT** — после доказательства на forex

## Классы стратегий

| Класс | Описание | Режим |
|-------|----------|-------|
| MR | Mean Reversion | RANGE |
| TF | Trend Following | TREND |
| VOL | Volatility Breakout | TRANSITION |
| LS | Liquidity Sweep | RANGE/TREND |
| SESSION | Session Patterns | ANY |
| GRID | Adaptive Grid | RANGE |
| FILTER | Risk Filters | ANY |
