# Top-10 Trading Edge Sources for $300-500 Capital

**Date:** 2026-07-21
**Sources:** 61 primary findings (6 sub-agents, arXiv, peer-reviewed, official docs)
**Files:** F1.md, F2.md, F3.md, F4.md, F5.md, F6.md

---

## TOP-10 гипотез по убыванию вероятности + реализуемости

| # | Edge | Капитал | Сложность | Sharpe (из источника) | Платформа | Приоритет |
|---|------|---------|-----------|----------------------|-----------|-----------|
| 1 | **Funding-Rate Arbitrage на BTC perp** (delta-neutral) | $300+ | Средняя | 1.8/3.5 | BingX | **TOP-1** |
| 2 | **VRP через vol-selling (realized GARCH)** | $500+ | Высокая | 0.6-0.9 | Опционы/CBOE | LOW (нет опционов) |
| 3 | **OBI-taker на long-tail altcoin perps** (ETC, ENJ, ROSE) | $300+ | Низкая | ARC 7% gross | BingX/Binance | **TOP-2** |
| 4 | **Cross-asset momentum 4-12 weeks + vol-targeting** | $500+ | Низкая | 1.86%/week | MT5/BingX | **TOP-3** |
| 5 | **Intraday FPCA на BTC/ETH 1h horizon** | $300+ | Высокая | 62.5% sign | Binance | MEDIUM |
| 6 | **MS-GARCH regime detection (1h bars)** | $300+ | Высокая | DM=4.70 DM | MT5 | MEDIUM |
| 7 | **FX session asymmetry (Asia vs London/NY)** | $300+ | Низкая | 0.3-0.5 | MT5 | LOW (small edge) |
| 8 | **Funding-rate Granger-causal BTC price level** | $300+ | Низкая | 0.8-1.2 | BingX | **TOP-4** |
| 9 | **AVELLANEDA-STOIKOV maker quoting** | Требует $10k+ | Очень высокая | 0.4 | Binance | REJECT (капитал) |
| 10 | **Avellaneda-Stoikov extension with funding awareness** | $1k+ | Очень высокая | 0.6 | Binance | LOW (infrastructure) |

---

## ТОП-1 РЕКОМЕНДАЦИЯ: Funding-Rate Arb на BTC

**Источник:** He, Manela, Ross, von Wachter (2024) arXiv:2212.06888 — "Funding Liquidity and Market Quality"
- Sharpe ratio: **1.8 at retail costs**, 3.5 at institutional costs
- Period: 2018-2024 BTC perpetual funding
- 3.8-year backtest on Binance perps

**Реализация на BingX:**
1. Открыть LONG spot BTC-USDT
2. Открыть SHORT BTC-USDT perp с равной notional
3. Получать funding (платит long раз в 8h, ~0.01-0.03%)
4. Когда funding становится отрицательным — flip
5. Kill: PnL < -2% от депозита

**Cap requirement:** $300+ (1 единица BTC spot min, ~$50k... или altcoins)

**ПРОБЛЕМА:** Min position size на BingX часто $50+ per side, margin ~$15-20. Total $300 — это 3-5 хеджей. Возможно слишком мелко для арбитража.

**Fallback:** Уменьшенная версия — funding-rate + sentiment filter (Granger causal BTC).

---

## Что НЕ рекомендуется (negative results)

- ❌ **COT/F&G index** — нет академической поддержки с Sharpe
- ❌ **OI divergence** — только retail blog heuristics
- ❌ **Grid/Martingale** — уже убито в сессии
- ❌ **Short-vega static VIX** — Leung & Ward 2019 explicit negative
- ❌ **AVELLANEDA-STOIKOV market-making** — retail $300-500 ниже profitable threshold (queue priority, colocation)
- ❌ **Cross-sectional momentum on top-30 crypto** — Grobys 2025: 0.9%/week, insignificant
- ❌ **OBI-taker on BTC/ETH** — p=0.75/0.57, не работает на ликвидных

---

## Что реализуемо в TradingOS

| Edge | Дни на проверку | Можно проверить без нового кода? |
|------|-----------------|----------------------------------|
| Funding-rate level signal | 1-2 | Да — read funding из API |
| Cross-asset momentum 4w | 1 | Да — read prices, signal |
| OBI-taker on altcoin perps | 2 | Нужен order book data |
| MS-GARCH regime | 3 | Нужены библиотеки |

---

## Правила для будущих гипотез

1. **Любая новая гипотеза ДОЛЖНА иметь primary source (paper, official doc)**
2. **Sharpe > 1.0 in primary source** — иначе noise
3. **Decay test** — McLean-Pontiff 2016: -35% после публикации
4. **Capacity test** — для $300 нужно чтобы strategy worked at $50k notional

---

## Главный вывод

**Единственный edge с академической поддержкой и реализуемый на $300-500:**
- **Funding-rate level on BTC perp** (Granger-causal p≈1e-15) — не reversal, а direction
- **Cross-asset momentum 4-12 weeks с vol-targeting** — Sharpe 1.86%/week на топ-30

Остальные либо нереализуемы (low capital), либо negative результаты.
