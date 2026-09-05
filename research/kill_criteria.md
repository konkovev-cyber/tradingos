# Kill Criteria — когда останавливать эксперимент

## Принцип

Каждый эксперимент имеет право на жизнь только пока есть шанс доказать преимущество.
Как только данные показывают, что edge нет — эксперимент останавливается и архивируется.

## Общие критерии (для всех стратегий)

| Criterion | Threshold | Action |
|-----------|-----------|--------|
| Profit Factor | < 0.9 at 100+ trades | REJECT |
| Max Drawdown | > 15% | REJECT |
| Walk Forward | < 40% periods profitable | REJECT |
| No signals | 7 days without any signal | REJECT (strategy too rare) |
| Data error | > 5% candles missing | PAUSE, fix data |

## LS-001 — Liquidity Sweep

| Criterion | Threshold | Current | Verdict |
|-----------|-----------|---------|---------|
| Min trades | 30 | 3 | OK |
| PF | < 0.9 at 30+ trades | N/A | OK |
| Max DD | > 15% | ~0.05% | OK |
| No signals | 7 days | 15h | OK |
| Win rate | < 20% at 30+ trades | 33% | OK |

## MR-001 — Mean Reversion (BB)

| Criterion | Threshold | Current | Verdict |
|-----------|-----------|---------|---------|
| Min trades | 100 | 2 | OK |
| PF | < 0.9 at 100+ trades | N/A | OK |
| Max DD | > 10% | 0% | OK |
| No signals | 3 days | 0.1h | OK |
| Win rate | < 30% at 100+ trades | N/A | OK |

## VOL-001 — Compression Breakout

| Criterion | Threshold | Current | Verdict |
|-----------|-----------|---------|---------|
| Min trades | 50 | 0 | OK |
| PF | < 1.0 at 50+ trades | N/A | OK |
| Max DD | > 15% | 0% | OK |
| No signals | 5 days | 0.1h | OK |
| Win rate | < 35% at 50+ trades | N/A | OK |

## TF-001 — Trend Pullback

| Criterion | Threshold | Current | Verdict |
|-----------|-----------|---------|---------|
| Min trades | 50 | 0 | OK |
| PF | < 1.0 at 50+ trades | N/A | OK |
| Max DD | > 15% | 0% | OK |
| Recovery Factor | < 0.8 at 50+ trades | N/A | OK |
| No signals | 5 days | 0.1h | OK |

## Archive procedure

When an experiment is killed:

1. Write final report to `research/reports/{ID}_final.md`
2. Move strategy files to `research/archived/{ID}/`
3. Add entry to `research/graveyard.md` with reason
4. Free up screen session
