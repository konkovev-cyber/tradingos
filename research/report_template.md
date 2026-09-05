# Research Report Template

## Experiment ID

`{ID}` — `{Name}`

## Status

`{PLANNED | SHADOW | COMPLETE | REJECTED | ARCHIVED}`

## 1. Hypothesis

{One-sentence market hypothesis}

## 2. Period

| Start | End | Total candles |
|-------|-----|---------------|
| {date} | {date} | {N} |

## 3. Signal Generation

| Metric | Value |
|--------|-------|
| Total signals | {N} |
| Signal rate | {N} per 1000 candles |
| LONG / SHORT | {N} / {N} |

## 4. Performance

| Metric | Value |
|--------|-------|
| Total trades | {N} |
| Win Rate | {N}% |
| Profit Factor | {N} |
| Expectancy | {N} |
| Max Drawdown | {N}% |
| Avg MFE | {N} |
| Avg MAE | {N} |
| Avg Bars Held | {N} |
| Avg MFE/MAE Ratio | {N} |

## 5. Exit Distribution

| Reason | Count | % |
|--------|-------|---|
| SL | {N} | {N}% |
| TP | {N} | {N}% |
| EXPIRY | {N} | {N}% |

## 6. A/B Comparison (if applicable)

| Metric | Profile A | Profile B |
|--------|-----------|-----------|
| SL multiple | {N}× | {N}× |
| TP multiple | {N}× | {N}× |
| Win Rate | {N}% | {N}% |
| PF | {N} | {N} |
| Avg MAE | {N} | {N} |
| Avg Bars Held | {N} | {N} |

## 7. Failure Analysis

| Failure Mode | Occurrence | Impact |
|--------------|------------|--------|
| {mode} | {N} trades | {description} |
| {mode} | {N} trades | {description} |

## 8. Verdict

```
[ ] ACCEPT — edge confirmed, proceed to next stage
[ ] REJECT — no edge found, archive strategy
[ ] INCONCLUSIVE — insufficient data, extend observation
[ ] REWORK — modify hypothesis and retest
```

## 9. Decision Rationale

{2-3 sentence explanation of the verdict}
