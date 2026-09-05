# Research Intelligence Layer

## Purpose

After each completed experiment, automatically answer 4 questions:
1. Why did the strategy work or fail?
2. Can the edge transfer to other markets?
3. Which existing experiments are most similar?
4. Which next experiment has highest Expected Research Value?

---

## 1. Market Scanner

### Market Passport Template

| Field | Value | Source |
|-------|-------|--------|
| Symbol | BTCUSDT | Bybit |
| Spread avg | 0.01% | Bybit API |
| ATR avg (M5) | 12-50 | Calculate from data |
| Avg trend length | 8-15 bars | Calculate |
| Avg range length | 3-8 bars | Calculate |
| Sessions | 24/7 (crypto) | Hardcoded |
| Liquidity score | 0.95 | Bybit volume |
| Trade frequency potential | 2,898/month (LS) | Backtest |

### Markets to scan (Priority order)

| # | Market | Type | Status |
|---|--------|------|--------|
| 1 | BTCUSDT | Crypto | ✅ Done (LS-001 v2 validated) |
| 2 | ETHUSDT | Crypto | ⬜ Pending |
| 3 | XAUUSD | Metal | ⬜ Pending (original goal) |
| 4 | EURUSD | Forex | ⬜ Pending |
| 5 | SOLUSDT | Crypto | ⬜ Pending |
| 6 | BNBUSDT | Crypto | ⬜ Pending |
| 7 | GBPUSD | Forex | ⬜ Pending |
| 8 | USDJPY | Forex | ⬜ Pending |
| 9 | XAGUSD | Metal | ⬜ Pending |

---

## 2. Edge Density (hypothetical, to be measured)

| Market | Ideas tested | Ideas profitable | Edge density |
|--------|-------------|------------------|--------------|
| BTC | 4 (LS, MR, TF, VOL) | 1 (LS) | 25% |
| XAU | 0 | — | TBD |
| EUR | 0 | — | TBD |
| ETH | 0 | — | TBD |

**Goal:** measure edge density per market. Focus research on highest-density markets.

---

## 3. Complexity Score

| Strategy | Features | Lines | Complexity |
|----------|----------|-------|------------|
| LS-001 | 6 (lookback, threshold, BB, ADX, hour, day) | ~100 | MEDIUM |
| MR-001 | 3 (period, std, deviation) | ~80 | LOW |
| TF-001 | 4 (EMA, ADX, period, pullback) | ~100 | MEDIUM |
| VOL-001 | 3 (ATR, BB, Donchian) | ~90 | LOW |

**Rule:** prefer simpler strategies with equal PF.

---

## 4. Robustness Index (0-100)

| Component | Weight | LS-001 v2 |
|-----------|--------|-----------|
| Walk Forward passed | 25 | 25 |
| Monte Carlo pass | 20 | 20 |
| Bootstrap pass | 15 | 15 |
| Monthly stability | 20 | 16 (11/13 = 85%) |
| Cost survival | 10 | 10 |
| Parameter stability | 10 | TBD |
| **Total** | **100** | **86** |

---

## 5. Economic Score

| Metric | Value | Score |
|--------|-------|-------|
| PF | 2.44 | High |
| Trades/month | ~30 | Medium |
| Capacity | 0.001 BTC × N parallel | Limited |
| Complexity | Medium | Penalty |
| **Economic value** | **Medium** | |

---

## 6. Portfolio Lab

### Allocation Model

| Candidate | PF | DD | Trades/mo | Correlation (est) | Suggested % |
|-----------|----|----|-----------|-------------------|-------------|
| LS-001 v2 (BTC) | 2.44 | ~3% | 30 | 1.0 (alone) | 40% |
| MR-001 (XAU) | TBD | TBD | TBD | TBD | 30% |
| Breakout (EUR) | TBD | TBD | TBD | TBD | 20% |
| Cash | — | — | — | — | 10% |

### Correlation Priority

**Not market correlation. STRATEGY equity curve correlation.**

This requires:
1. At least 2 strategies validated
2. Overlapping time periods
3. Daily equity returns
4. Pearson/Spearman correlation

---

## 7. Time Allocation Rule

| Activity | % | Rationale |
|----------|---|-----------|
| **Candidate validation** (Reality Check, Demo, Live) | **70-80%** | This produces money |
| **New hypothesis search** (scanning, new ideas) | **20-30%** | Pipeline fuel |

**Do NOT spend >30% on new research until a strategy is in Demo.**

---

## 8. Research Intelligence Layer — 4 Questions

### After each completed experiment:

#### Q1: Why did it work or fail?
- Look at conditional report
- Which conditions had PF>1?
- Which had PF<1?
- Edge lives where: [conditions from edge mining]

#### Q2: Can the edge transfer to other markets?
- If edge is based on **market microstructure** (sweeps, stop hunts) → likely universal
- If edge is based on **session timing** (Asia, London, NY) → limited to markets with those sessions
- If edge is based on **volatility regime** → likely universal
- If edge is based on **specific pair behavior** → not transferable

#### Q3: Which existing experiments are most similar?
- LS-001 ≈ Breakout strategies (all detect exhaustion moves)
- MR-001 ≈ Bollinger-based mean reversion
- TF-001 ≈ Trend pullback
- Reuse components: ATR calc, ADX calc, session detection

#### Q4: Next experiment with highest Expected Research Value (ERV)?

```
ERV = P(success) × Expected_value_if_success − Cost

P(success) based on:
- Edge density of the market (TBD for non-BTC)
- Similarity to known working patterns
- Market liquidity (affects slippage)

Expected_value_if_success based on:
- Trade frequency
- PF potential
- Capacity
```

---

## 9. Current State Summary

| Item | Status | Next action |
|------|--------|-------------|
| LS-001 v2 BTC | ✅ Edge validated | Reality Check (Stage 0) |
| MR-001 BTC | ❌ PF 0.71 | Archive |
| TF-001 BTC | ❌ Too rare | Archive |
| VOL-001 BTC | ❌ 0 signals | Archive |
| Multi-market | ⬜ | Scan ETH, XAU, EUR next |
| Portfolio Lab | ⬜ | Need 2+ validated strategies |
| Research Intelligence | ⬜ | Build after next 2 experiments |

---

## 10. Immediate Next Action

**Do NOT start new research yet.** 70% of time goes to:
1. Reality Check for LS-001 v2 (6 checks)
2. MT5 Demo Protocol (after Reality Check passes)
3. Market Scanner for ETH and XAU (parallel, lightweight)

**20-30% for new ideas only when current pipeline has candidates in Demo.**
