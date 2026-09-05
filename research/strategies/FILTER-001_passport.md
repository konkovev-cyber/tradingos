# FILTER-001: News Avoidance Filter — Research Passport

## 1. Market Hypothesis

Макроэкономические новости (NFP, FOMC, CPI, ECB) вызывают экстремальную волатильность, расширение спредов и проскальзывания. Торговля в эти периоды имеет отрицательное ожидание для большинства стратегий.

## 2. Edge Mechanism

| Component | Description |
|-----------|-------------|
| Type | RISK MANAGEMENT |
| Source | HFT и institutional algos имеют преимущество в исполнении в моменте новостей |
| Counterparty | Розничные трейдеры, входящие "на эмоциях" |
| Why persistent | Новости — запланированные события, их нельзя избежать, но можно не торговать |

## 3. Required Data

| Data | Source | Available? |
|------|--------|------------|
| News calendar | ForexFactory API / investing.com | ⚠️ Нужен источник |
| Event type | NFP, FOMC, CPI, ECB, etc. | ⚠️ |
| Event time | GMT | ⚠️ |
| Blackout window | ±30 min | Configurable |

## 4. Implementation

```python
NEWS_EVENTS = {
    "NFP":        {"day": "first_friday", "time": "12:30", "impact": "HIGH"},
    "FOMC":       {"day": "scheduled",     "time": "18:00", "impact": "HIGH"},
    "CPI":        {"day": "monthly",       "time": "12:30", "impact": "HIGH"},
    "ECB":        {"day": "scheduled",     "time": "12:45", "impact": "HIGH"},
    "PPI":        {"day": "monthly",       "time": "12:30", "impact": "MEDIUM"},
    "RetailSales": {"day": "monthly",      "time": "12:30", "impact": "MEDIUM"},
}

BLACKOUT_BEFORE = 30  # minutes
BLACKOUT_AFTER = 30   # minutes
```

## 5. Effect on Strategies

| Strategy | News Impact | Action |
|----------|-------------|--------|
| Mean Reversion | Extreme false signals, spread blowout | BLOCK |
| Trend Following | Slippage on entry, fake breakouts | BLOCK |
| Breakout | False breakouts in both directions | BLOCK |
| Grid | Spread kills grid profitability | BLOCK |
| Liquidity Sweep | Sweeps become real breakouts | BLOCK |

## 6. Failure Modes

| Mode | Cause | Detection |
|------|-------|-----------|
| Missed event | Calendar not updated | Manual check |
| False positive | Low-impact event treated as high | Impact filter |
| Timezone error | DST vs GMT | Use UTC everywhere |

## 7. Priority

**★★★★★** — Sprint 1, protects all strategies, zero strategy code
