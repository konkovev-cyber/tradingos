# FORWARD VALIDATION SETUP AUDIT — Completed

## ACTIVE FORWARD VALIDATION

| Параметр | Значение |
|----------|----------|
| Universe | BTC, ETH, DOGE, SOL, XRP, ADA, BNB (7 symbols) |
| Timeframe | 1m |
| Threshold | 0.55 |
| MAX_OPEN | 1 |
| Режим | OBSERVATION ONLY (no live trades) |
| Period | 1 hour + ongoing |
| Goal | 30+ signals, 30 days |

## CHANGES

1. `tradingos/data/run_observation.py`:
   - default Universe = 7 symbols (proven profitable in 90d backtest)
   - extended log: entry, stop_loss, take_profit, atr, confidence

2. Архивирован старый signal_log → `signal_log_legacy_3symbols.jsonl` (1151 записей)

3. PID: 2568113, polling 7 symbols each 60s

## TRACKED FIELDS

Each entry in `signal_log.jsonl`:

```
timestamp        ISO UTC
symbol           BTCUSDT | ETHUSDT | DOGEUSDT | SOLUSDT | XRPUSDT | ADAUSDT | BNBUSDT
direction        BUY | SELL | NONE
reject_reason    probability_below_threshold | none
rsi              float
adx              float
ema20            float
close            float
candles          200
atr              float
entry            float or null (only if direction != NONE)
stop_loss        float or null
take_profit      float or null
confidence       float
signals_total    cumulative accepted
rejected_probability   cumulative rejected
rejected_no_score       cumulative rejected (no score)
regime           unknown (placeholder, not yet integrated)
```

## METRICS COLLECTION

Per signal:
- Symbol-level signal rate
- Reject reasons (current: 100% `probability_below_threshold`)
- Confidence (current: integrity_score=1.0 always)

Daily aggregate (target after 24h):
- Total checks per symbol
- Accepted/Rejected split
- Conflict rate (symbols producing signals in same minute)

## EXPECTED vs DAILY REPORT FORMAT

**TRADINGOS FORWARD REPORT DAY X**

```
Facts:
  signals_total:   <N>
  accepted:        <N> (current target: 0+, eventual ~11/30d from backtest)
  rejected:         <N>
  symbols (daily): {BTC: n, ETH: n, ...}

Performance estimate:
  expected_PF:     2.14 (from 90d historical)
  expected_WR:     68%
  expected_NetR/30d: +8R

Comparison:
  historical_PF_90d:   2.14
  forward_PF_actual:   TBD (need first accepted signal + outcome)
```

## DECISION POINT (after 30+ signals + observed outcomes)

| Forward PF | Action |
|------------|--------|
| > 1.3, WR > 45% | подготовить Micro Live |
| 1.0–1.3 | продолжить observation |
| < 1.0 | edge не подтверждён |

## CURRENT STATUS (Day 0, +2 min)

- Forward log: 42 entries
- Accepted: 0
- Rejected: 42 (100% probability_below_threshold)
- Universe verified: 7 symbols cycling correctly every 60s
- Observation process stable, no errors

Edge confirmation requires actual `accepted signals` with forward outcomes.
Current market state is flat (ADX ~12-17, RSI ~48-55) — ожидаемо 0 сигналов.
