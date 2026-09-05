# DN-sweep validation v2 — exit-model correction (2026-08-28)

## CRITICAL FINDING
Validated edge (WR 60.7%, +0.53% med) was computed WITHOUT SL/TP (4h forward close). Paper ran SL=wick-0.2range + TP=2R: replay of SAME signals gives WR 38%, med net -0.78% (negative). Root cause: wick-SL median 107bps vs M15 alt noise MAE median 197bps → 72% stopped before reversal plays out.

## Fix
SL-only bracket: hard stop at max(structural, entry-800bps), NO TP, exit at 4h close. Whitelist re-derived under new exit model: drop BTWUSDT/TUTUSDT/STRKUSDT/ETHUSDT (WR<55% or med<=0).

## Validation (SL-only 800bps, no TP, hold 4h, 10 symbols)
- n=325, WR=62.2%, median net=0.48%, mean=0.55%
- bootstrap median 95% CI: [0.307, 0.612]
- periods: P1 +0.16/52% P2 +0.85/71% P3 +0.65/57% P4 +0.47/65% P5 +0.41/66%
- OOS(30%): WR 67.0%, med +0.43%
- SL sensitivity: 400-1500bps all med >= +0.43%, WR 60.6-62.5%
- 2x fee stress: med +0.38%, WR 59.7%

## Methodology note
v1 validation (README_dn_sweep.md) measured 4h forward close WITHOUT SL/TP execution model. WR 60.7%/+0.53% reproduces exactly (variant A). The TP=2R/SL=wick bracket was added at implementation time WITHOUT re-validation and flips the edge negative. Lesson: exit model must be part of the validated spec.
