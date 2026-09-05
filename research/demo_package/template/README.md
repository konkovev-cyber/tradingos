# Demo Package — {STRATEGY_ID}

## 1. Passport

**File:** `research/strategies/{ID}_passport.md`

## 2. Backtest Report

| Metric | Value |
|--------|-------|
| Period | |
| Total candles | |
| Total signals | |
| Total trades | |
| Win Rate | |
| Gross PnL | |
| Fees | |
| Slippage | |
| Net PnL | |
| Profit Factor | |
| Max Drawdown | |
| Avg MFE | |
| Avg MAE | |
| Avg Bars Held | |

## 3. Walk Forward

| Window | Train PF | Test PF | Train Trades | Test Trades |
|--------|----------|---------|--------------|-------------|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| **Avg** | | | | |

## 4. Monte Carlo

| Metric | Value |
|--------|-------|
| Simulations | 10,000 |
| Avg Final Capital | |
| Median Final Capital | |
| Worst Final Capital | |
| Avg Max DD | |
| Worst Max DD | |
| Ruin Probability | |
| **Passed** | |

## 5. Risk Config

```yaml
strategy: {ID}
symbol: BTCUSDT
timeframe: 5m

position_size: 0.01
max_concurrent: 1
max_daily_trades: 5
max_daily_loss_pct: 2.0
risk_per_trade_pct: 0.25

sl_rule: "ATR × 1.5"
tp_rule: "ATR × 2.5"
max_hold_bars: 48

news_blackout_minutes: 30
blackout_events:
  - NFP
  - FOMC
  - CPI
  - ECB
```

## 6. MT5 Config

```yaml
broker: RoboForex
account_type: demo
symbol: BTCUSDT
magic_number: 123456
execution: MARKET
deviation: 10
filling: IOC
```

## 7. Launch Checklist

```
[ ] Strategy Passport 2.0 completed
[ ] Research Score >= 60
[ ] All minimum requirements met
[ ] No automatic rejection triggered
[ ] Regime breakdown shows no fatal weakness
[ ] Capital Efficiency >= 1.0
[ ] Risk parameters documented and frozen
[ ] MT5 adapter tested (SSH + order send + order close)
[ ] Emergency close tested
[ ] Logging to Data Lake verified
[ ] Telegram alerts configured
[ ] Daily loss limit configured
[ ] Max position limit configured
[ ] Kill switch accessible
```
