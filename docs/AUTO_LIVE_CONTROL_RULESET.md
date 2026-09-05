# Auto Live Control Ruleset

## Назначение
Полностью автономный режим управления капиталом.
Система сама решает когда увеличивать/уменьшать капитал без человека.

## Принцип

> **Система управляет собой, но человек может остановить в любой момент.**

## Rules

### Rule 1: Auto Scale Up

```python
IF (
    trades_completed >= 100
    AND days_live >= 30
    AND win_rate >= 0.45
    AND sharpe_ratio >= 1.0
    AND governor_orange_count == 0
    AND t6_drift_rate < 0.15
    AND portfolio_diversification > 0.5
)
THEN
    capital = min(capital * 1.5, max_capital)
    log("auto_scale_up", capital)
```

### Rule 2: Auto Scale Down

```python
IF (
    win_rate < 0.35
    OR sharpe_ratio < 0.5
    OR governor_orange_count >= 3
    OR t6_drift_rate > 0.30
    OR consecutive_losses >= 5
    OR portfolio_diversification < 0.3
)
THEN
    capital = max(capital * 0.5, min_capital)
    log("auto_scale_down", capital)
```

### Rule 3: Emergency Stop

```python
IF (
    governor_level == "RED"
    OR system_drawdown > 10%
    OR consecutive_losses >= 10
    OR execution_errors >= 10
    OR circuit_breaker_state == "OPEN"
)
THEN
    flatten_all_positions()
    freeze_trading()
    log("emergency_stop")
    notify_human()
```

### Rule 4: Learning Control

```python
IF live_phase == "MICRO_LIVE":
    learning_enabled = False    # no learning in micro
ELIF live_phase == "SMALL_LIVE":
    learning_enabled = True
    learning_rate = 0.02        # slow learning
ELIF live_phase == "NORMAL":
    learning_enabled = True
    learning_rate = 0.05        # normal learning
```

### Rule 5: Rebalance Check

```python
EVERY 24 hours:
    rebalance_portfolio()
    
    IF portfolio_diversification < 0.4:
        reduce_concentrated_assets()
        log("rebalance_due_to_correlation")
    
    IF regime_distribution shows >70% in single regime:
        reduce_exposure()
        log("rebalance_due_to_regime_concentration")
```

### Rule 6: Health Gate

```python
EVERY 1 hour:
    system_health = check_all_modules()
    
    IF system_health.score < 0.5:
        freeze_trading()
        log("health_gate_freeze")
    
    IF system_health.score < 0.8:
        reduce_position_size(0.5)
        log("health_gate_reduce")
```

### Rule 7: Human Override

```python
# Человек может вмешаться в любой момент

human_command("stop")    → emergency_stop()
human_command("pause")   → freeze_trading()
human_command("resume")  → unfreeze_trading()
human_command("scale")   → manual_scale(target_capital)
human_command("report")  → generate_full_report()
```

## Auto-Control Dashboard

```text
┌─────────────────────────────────────────────────────┐
│              TRADINGOS AUTO-CONTROL                 │
├─────────────────────────────────────────────────────┤
│  Current Phase:     SMALL_LIVE                      │
│  Capital:           250 USDT                        │
│  Max Capital:       1000 USDT                       │
│  Learning Rate:     0.05                            │
│                                                     │
│  Performance:                                        │
│    Win Rate:        52.3%                           │
│    Sharpe Ratio:    1.24                            │
│    Drawdown:        2.1%                            │
│    Trades:          127                             │
│                                                     │
│  Auto-Scale:                                          │
│    Last action:     scale_up (2 days ago)           │
│    Next eligible:   28 days                         │
│    Condition:       trades >= 200, days >= 60       │
│                                                     │
│  Safety:                                             │
│    Governor:        GREEN                           │
│    Circuit Breaker: CLOSED                          │
│    Drift Rate:      8.2%                            │
│    Health Score:    0.94                            │
│                                                     │
│  [PAUSE] [REPORT] [EMERGENCY STOP] [FORCE SCALE]   │
└─────────────────────────────────────────────────────┘
```

## Notification Rules

### Scale events → notify human
- auto_scale_up
- auto_scale_down
- emergency_stop
- health_gate_freeze

### Don't notify (automatic)
- rebalance
- learning_rate_adjustment
- normal health checks

## Safety Limits

```
MAX_AUTO_SCALE_PER_DAY = 1.5x
MAX_AUTO_SCALE_PER_WEEK = 3x
MAX_POSITION_SIZE_RATIO = 0.30   # 30% of capital per position
MAX_LEVERAGE = 1.0               # no leverage in auto mode
```

## Human Kill Switch

```python
# Всегда доступен

kill_switch = True  # human can always disable auto-control

IF kill_switch == False:
    freeze_all()
    flatten_all()
    notify("KILL_SWITCH_ACTIVATED")
```
