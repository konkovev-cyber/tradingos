# Market Discovery — подготовка к Multi-Market Sprint

## Цель

Собрать профиль ликвидности и доступности данных для 5 инструментов,
чтобы Multi-Market Sprint начался с готовой базой, а не с поиска API.

## Инструменты (по приоритету)

| # | Market | Type | API Source | Status |
|---|--------|------|------------|--------|
| 1 | BTCUSDT | Crypto | Bybit testnet ✅ | Done (LS-001 v2 validated) |
| 2 | ETHUSDT | Crypto | Bybit testnet ✅ | Data available, not tested |
| 3 | XAUUSD | Metal | MT5 RoboForex ✅ | Infrastructure ready, not tested |
| 4 | EURUSD | Forex | MT5 RoboForex ✅ | Infrastructure ready, not tested |
| 5 | SOLUSDT | Crypto | Bybit testnet ✅ | Data available, not tested |

## Что нужно собрать для каждого

| Поле | Источник | Метод |
|------|----------|-------|
| Avg spread | Bybit API / MT5 | taker/maker fee |
| Avg ATR (M5) | Historical data | Calculate from 12-month cache |
| Avg volume | Bybit API | 24h volume |
| Liquidity score | Bybit API | volume × frequency |
| Session hours | Hardcoded | Crypto: 24/7, Forex: 24/5 |
| Typical commission | Exchange docs | taker fee rate |

## Data Sources Status

| Source | Status | Auth |
|--------|--------|------|
| Bybit testnet | ✅ Working | API key in .env |
| Bybit mainnet | ✅ Working | Public data, no auth needed |
| MT5 RoboForex demo | ✅ SSH ready | SSH to 192.168.1.77 |
| MT5 MetaQuotes demo | ⬜ Not set up | Needs demo account |
| Dukascopy | ⬜ Not integrated | Free tick data |

## Next Action

1. Fetch 12-month history for ETHUSDT, SOLUSDT from Bybit (5 min each)
2. Replay LS-001 on ETHUSDT and SOLUSDT (same logic, no code changes)
3. If edge exists → add to candidate list
4. If no edge → archive with reason, try different hypothesis

## Key Principle

Same research process, same conditional framework, different market.
Do NOT change strategy parameters per market.
If filter works on BTC but not ETH → the filter is overfitted, not the market wrong.
