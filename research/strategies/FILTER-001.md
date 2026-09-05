# FILTER-001: News Avoidance Filter

## Hypothesis

Макроэкономические новости (NFP, FOMC, CPI, ECB) вызывают экстремальную волатильность,
расширение спредов и проскальзывания. Торговля в эти периоды имеет отрицательное ожидание
для большинства стратегий.

## Market Logic

- За 30 минут до новости: рынок сужается, ликвидность падает
- В момент новости: спреды расширяются в 5-10 раз
- После новости: хаотичные движения, ложные пробои
- Контрагенты: HFT и institutional algos с лучшим исполнением

## Implementation

```python
NEWS_EVENTS = {
    "NFP": "First Friday 12:30 GMT",
    "FOMC": "As scheduled 18:00 GMT",
    "CPI": "Monthly 12:30 GMT",
    "ECB": "As scheduled 12:45 GMT",
}

BLACKOUT_BEFORE = 30  # minutes
BLACKOUT_AFTER = 30   # minutes
```

## Effect on Strategies

| Strategy | News Impact | Action |
|----------|-------------|--------|
| Mean Reversion | Extreme false signals | BLOCK |
| Trend Following | Slippage on entry | BLOCK |
| Breakout | False breakouts | BLOCK |
| Grid | Spread kills grid | BLOCK |

## Priority

★★★★★

## Complexity

LOW — календарь событий + таймер
