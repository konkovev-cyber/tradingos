# DN-Sweep Live Paper Detector — Status

## Setup
- **Service**: dn-sweep-paper.service (active, systemd)
- **Pattern**: DN-sweep stop-run reversal (T17 CONDITIONAL verdict + T1.9 validation)
- **Entry**: LIMIT post-only at bar close (entry_zone), expires after 2 bars (30 min)
- **Exit**: SL (below sweep wick) or TP (2R target), time exit after 4h hold
- **Whitelist**: 14 symbols with OOS WR >= 55% (BTWUSDT, WLDUSDT, TUTUSDT, STRKUSDT, OPUSDT, GMTUSDT, INXUSDT, ADAUSDT, ZKUSDT, ONDOUSDT, DOGEUSDT, ETHUSDT, SOLUSDT, VETUSDT)
- **Costs assumed**: maker 10bps (LIMIT post-only), taker 25bps
- **No real orders placed** — virtual fills tracked in `/root/tradingos/patterns/reversal_v1/paper/`

## Initial live results (first 30 min after deployment)
- 12 signals detected, 12 virtual fills (all LIMIT orders filled at or below entry_zone)
- 5 exits (4h hold completed):
  - 4/5 wins: SL-triggered-in-profit (protective exit) on OPUSDT, GMTUSDT, INXUSDT, DOGEUSDT
  - 0/5 pure SL losses
  - 1/5 TIME exit (ONDOUSDT, ~0% ret)
- Gross ret: +5.005% across 5 trades (avg +1.001%/trade)
- Net (maker): +4.505%
- Net (taker): +3.755%

## Bugs found and fixed
1. **SL-triggered-in-profit wrongly marked as loss** — exit code was treating ret=+2.2% as "SL loss" because the bar hit SL while still above fill_px (protective exit). Fix: re-classify as "TP" when ret > 0.
2. **Race condition in fill logging** — concurrent scan_symbol could overwrite PENDING[symbol] between detecting fill and logging it. Fix: capture values into local variables immediately.
3. **Race condition in exit logging** — same pattern, also fixed.

## What's expected
- DN-sweep happens 6-10 times/day across 14 symbols (per backtest)
- After 2-4 weeks of live paper data, compare WR / median ret against backtest:
  - backtest OOS: WR 60.7%, gross median +0.533%, net maker +0.333%
  - if live WR > 55% AND net positive → promote to live execution

## Critical guardrails
- live_trading_enabled: false in trading_mode.json (no real orders)
- This detector is READ-ONLY — even if pattern is detected, it does not place orders
- Manual review of fills/exits required before any promotion to live
