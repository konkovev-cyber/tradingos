# Regime Detection + Contextual Bandit (T1.3)

## What
GMM classifier detects 4 market regimes from (log_ret, vol_z, |funding|, OI change).
Thompson Sampling bandit allocates risk-budget per regime based on per-regime WR priors.

## Data
- 89,947 feature rows × 14 whitelist symbols × 93 days (2026-05-26 → 2026-08-27)
- Features: log return, vol_z (16-bar vol z-scored against 64-bar mean), abs funding, OI change

## Regimes (from GMM means)
| Regime | Name        | n   | share | log_ret | vol_z  | |funding| | OI_chg |
|--------|-------------|-----|-------|---------|--------|----------|--------|
| 0      | VOL_SPIKE   | 1,057 | 1.2%  | -0.0024 | +0.43  | 0.00014  | +0.65% |
| 1      | TRENDING_UP | 18,522 | 20.8% | +0.0004 | +1.22  | ~0       | ~0     |
| 2      | QUIET       | 60,296 | 67.8% | ~0     | -0.47  | ~0       | ~0     |
| 3      | BEAR_DROP   | 9,074  | 10.2% | +0.0001 | -0.48  | 0.00007  | -0.07% |

## Per-regime DN-sweep performance (OOS, last 30%)
| Regime | n  | WR    | gross_med | net_maker |
|--------|----|-------|-----------|-----------|
| 0      | 13 | 61.5% | +0.536%   | +0.336%   |
| 1      | 91 | 54.9% | +0.575%   | +0.375%   |
| 2      | 42 | 64.3% | +0.522%   | +0.322%   |
| 3      | 20 | 75.0% | +0.680%   | +0.480%   |

**ALL 4 REGIMES POSITIVE NET (maker cost 10bps).** No regime is "off".

## Contextual Bandit (Thompson Sampling)
- Per regime: Beta(α, β) posterior, initialized from prior WR (n0=20 pseudo-observations)
- On signal: sample P(win) from each regime's Beta, take signal only if predicted regime has highest sample
- After trade: update alpha (win) or beta (loss)
- Tested: 70% acceptance rate from priors (no rejections from extreme Thompson samples)

## Integration
- `regime_bandit.py::RegimeBandit` — predict regime, compute bet size, update posterior
- Wired into `dn_sweep_live_detector.py::scan_symbol()`:
  - After finding DN-sweep signal, predict regime from features at signal_ts
  - Bandit returns 0 bet if regime sample is not best → skip signal
  - Otherwise: signal gets regime_name, bet_size in JSONL log

## Live validation
- Detector restarts every code change via systemctl
- Will collect ~2-4 weeks of live signals with regime + bet_size
- After: compare per-regime WR vs backtest priors; tune prior_n if needed

## What's next
- After 30+ trades: bandit posterior will diverge from priors based on real data
- If regime 3 (BEAR_DROP) continues 75% WR live → bandit will heavily favor it (sample WR ~0.75)
- Could add: per-symbol regime model (different regimes per symbol)

## Files
- `regime_features.parquet` — 90k rows × 11 cols (raw features)
- `regime_features_labeled.parquet` — same + regime column (0..3)
- `gmm_model.pkl` — fitted GMM + scaler
- `regime_bandit.py` — bandit class
- `bandit_state.json` — persists alpha/beta across restarts
