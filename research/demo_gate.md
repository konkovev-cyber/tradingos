# Demo Launch Checklist

## Pre-Demo Gates

### Gate 1: Research Pass
```
[ ] Strategy Passport 2.0 completed
[ ] Research Score >= 60
[ ] Minimum 300 trades in replay
[ ] PF >= 1.25 (full sample)
[ ] Max DD <= 10%
[ ] Walk Forward passed (avg test PF >= 1.15)
[ ] Monte Carlo passed (ruin probability < 5%)
[ ] 6+ consecutive profitable months
[ ] No single month > 50% of total profit
```

### Gate 2: Risk Parameters Frozen
```
[ ] SL/TP rules documented and locked
[ ] Position size formula documented
[ ] Max concurrent positions: 1
[ ] Max daily trades: 5
[ ] Max daily loss: 2% of capital
[ ] Risk per trade: 0.25-0.5%
[ ] News blackout implemented
```

### Gate 3: MT5 Adapter Verified
```
[ ] SSH connection to MT5 host works
[ ] Account info readable
[ ] Symbol info readable
[ ] Test order sent and confirmed
[ ] Test order closed and confirmed
[ ] Position sync works
[ ] Error handling works (connection loss, reject)
```

### Gate 4: Safety Systems
```
[ ] Emergency close command works
[ ] Daily loss limit triggers auto-stop
[ ] Max position limit enforced
[ ] Kill switch accessible
[ ] Logging to Data Lake verified
[ ] Telegram alerts configured
```

## Demo Run (30 days minimum)

### Daily Checks
```
[ ] Positions match between Shadow and MT5
[ ] No unexpected orders
[ ] Daily PnL within limits
[ ] Connection stable
[ ] No errors in logs
```

### Weekly Review
```
[ ] Compare Shadow vs Demo execution
[ ] Slippage report
[ ] Spread report
[ ] Strategy parameter review (no changes without cause)
```

### Exit Criteria
```
Stop demo immediately if:
[ ] Daily loss > 2% on 3 consecutive days
[ ] Total drawdown > 10%
[ ] Connection lost > 1 hour
[ ] Strategy logic error detected
[ ] Manual override required more than once
```

## Post-Demo Decision

```
[ ] ACCEPT: Shadow ≈ Demo, positive expectancy, move to Micro Live
[ ] REJECT: Execution differences too large, return to research
[ ] EXTEND: Need more data, run 30 more days
```
