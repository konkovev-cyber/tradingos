# meta_v1 — LightGBM signal filter (shadow mode, 2026-08-27)

## What
Predict P(fwd_1h_ret > 0) for incoming signals. Output a probability score,
log it on BOTH accepted and rejected candidates, and accumulate OOS data
for promotion decision.

## Data
- passed signals: signal_log.jsonl (16k rows, Jul 28 → Aug 27, 227 symbols)
- rejected signals: rejected_candidates.jsonl (9k rows, 6k with RSI)
- derivatives (34 symbols), microstructure (15 symbols) — sparse, not used in v1

## Features (8)
score, rsi, adx, atr, ema20, final_probability, confidence, rejected

## Honest OOF metrics (5-fold walk-forward, sorted by timestamp)
- AUC = **0.556 ± 0.050**
- WR = 26% on the underlying signal population (very noisy)
- Top features: atr, rsi, adx (rule-based signal indicators)

## Live scoring
- `meta_v1_scorer.py::score_signal(features)` → returns P(win) ∈ [0,1]
- Wired into `run_observation.py` on BOTH accepted AND rejected candidates
  (since 2026-08-27 17:06, after T1.2 patch — see commit log below)
- Currently SHADOW mode (logs recommendations, does NOT block)

## Operational
- `meta_v1_retrain.py` — weekly retrain (Sun 03:00 UTC, systemd timer)
- `meta_v1_daily_report.py` — daily report (18:00 UTC, systemd timer)
- Output: `daily_report.json` (summary + distributions + per-symbol stats)

## What this tells us (and doesn't)
- AUC 0.556 means rule-based signals carry SMALL but real signal for 1h forward returns.
- This is consistent with prior "PF 0.75" finding — the existing gates don't capture all edge
  but they don't kill it either.
- meta_v1 is a marginal filter, not a magic bullet. Promotion criterion: WR uplift ≥5pp
  at threshold 0.6 over 30+ days of OOS data on demo $100k.

## Next (T1.3)
HMM regime detection + contextual bandit for risk-budget allocation.
