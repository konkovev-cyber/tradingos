# Candidate Selection Rules v1

Фиксируются до просмотра результатов любого replay.

## Minimum Requirements

| Metric | Threshold | Source |
|--------|-----------|--------|
| Trades | > 300 | Historical replay |
| Profit Factor | > 1.25 | Net PnL after fees + slippage |
| Max Drawdown | < 15% | Equity curve |
| Monte Carlo ruin | < 5% | 10k simulations |
| Walk Forward profitable windows | > 70% | 6-month train / 3-month test |
| Profitable months | > 60% | Monthly breakdown |
| No single month | < 50% of total profit | Monthly breakdown |

## Automatic Rejection

Стратегия отклоняется без обсуждения если:

- Прибыль создана одним месяцем (> 50% от всего PnL)
- 50%+ прибыли от 5 лучших сделок
- Max DD пришёлся на один режим рынка
- Trade frequency < 10 сделок в месяц в среднем
- Walk Forward validation PF < 1.0
- Monte Carlo ruin probability > 5%

## Scoring (Research Score)

```
Score = 0.25×PF + 0.20×Stability + 0.20×SampleSize + 0.15×DD + 0.10×WalkForward + 0.10×Simplicity
```

| Score | Status |
|-------|--------|
| > 80 | Strong candidate |
| 60-80 | Candidate |
| 40-60 | Borderline — requires manual review |
| < 40 | Reject |

## Regime Breakdown

Каждая стратегия обязана показать PF отдельно для:

- RANGE (ADX < 20)
- TREND (ADX > 25)
- HIGH VOL (ATR > 90%ile)
- LOW VOL (ATR < 20%ile)
- ASIAN session
- US session

Если PF в любом режиме < 0.8 — стратегия получает предупреждение.

## Capital Efficiency

```
Capital Efficiency = Annual Return % / Max DD %
```

| Ratio | Rating |
|-------|--------|
| > 3 | Excellent |
| 1.5-3 | Good |
| 0.5-1.5 | Marginal |
| < 0.5 | Poor |

## Demo Readiness

Стратегия переходит в MT5 Demo только если:

```
[ ] All minimum requirements met
[ ] No automatic rejection triggered
[ ] Research Score >= 60
[ ] Regime breakdown shows no fatal weakness
[ ] Capital Efficiency >= 1.0
[ ] Risk parameters documented and frozen
[ ] MT5 adapter tested
[ ] Emergency close tested
```
