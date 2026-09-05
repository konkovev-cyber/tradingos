# Strategy Passport 2.0

## ID: MR-001
## Name: VWAP Mean Reversion
## Market: BTCUSDT → XAUUSD (planned)
## Timeframe: M5

---

## 1. Hypothesis

В режиме RANGE цена временно отклоняется от справедливой стоимости (VWAP/SMA20). Ликвидность возвращает цену.

## 2. Market Conditions

| Condition | Works | Fails |
|-----------|-------|-------|
| RANGE / ADX < 20 | ✅ | ❌ |
| TREND / ADX > 25 | ❌ | ✅ |
| Low volatility (ATR < 70%ile) | ✅ | ❌ |
| High volatility (ATR > 90%ile) | ❌ | ✅ |
| News (NFP/FOMC/CPI) | ❌ | ✅ |
| Asian session | ✅ | ❌ |
| US session | ⚠️ | ⚠️ |

## 3. Edge Source

| Type | Description |
|------|-------------|
| STATISTICAL | Price deviates → mean reversion is a statistical property |
| BEHAVIORAL | Retail chases breakouts, provides liquidity for reversion |
| MICROSTRUCTURE | VWAP algorithms create temporary imbalances |

## 4. Failure Modes

| # | Mode | Detection | Impact |
|---|------|-----------|--------|
| 1 | Trend day | ADX rising > 20 | Consecutive losses |
| 2 | Volatility spike | ATR > 90%ile | Wide SL, large MAE |
| 3 | News event | Calendar | Spread blowout, slippage |
| 4 | Low liquidity | Spread > 2× median | Poor fills |

## 5. Kill Conditions

| Condition | Threshold | Action |
|-----------|-----------|--------|
| PF after 300 trades | < 1.0 | REJECT |
| Max DD | > 15% | REJECT |
| Walk Forward consistency | < 40% windows profitable | REJECT |
| Monte Carlo ruin probability | > 5% | REJECT |
| No improvement after 500 trades | PF < 1.1 | REWORK |

## 6. Minimum Viable Sample

| Metric | Required |
|--------|----------|
| Trades | 300+ |
| History | 12+ months |
| Market regimes | 2+ (bull, bear, range) |
| Walk Forward windows | 4+ |

## 7. Demo Readiness Criteria

```
[ ] PF >= 1.25 (full sample)
[ ] PF >= 1.15 (walk forward validation)
[ ] DD <= 10%
[ ] Monte Carlo pass
[ ] 6+ consecutive profitable months
[ ] No single month > 50% of total profit
[ ] Risk parameters frozen
[ ] MT5 adapter tested
[ ] Emergency close tested
```
