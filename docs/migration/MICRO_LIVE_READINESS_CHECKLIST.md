# MICRO LIVE READINESS CHECKLIST

**Условие**: этот документ не активируется, пока Forward Validation не даст PF > 1.3, WR > 45%, N ≥ 30.

## Preconditions

- [ ] Forward Validation N ≥ 30 (target ~38 days)
- [ ] Forward PF > 1.3
- [ ] Forward WR > 45%
- [ ] Все 3 периода имеют > 0 netR
- [ ] Max DD < -3R

---

## 1. Decision Pipeline

- [ ] `decision.json` schema validated against Executor v0
- [ ] Signal → Decision → JSON → Executor chain tested dry-run
- [ ] `source: tradingos_signal_engine` confirmed (no bridge/ubot)
- [ ] decision.json path writable from ObservationRunner

## 2. Guardian Limits

- [ ] `MAX_OPEN_POSITIONS = 1` enforced
- [ ] Max risk per trade ≤ $1 USDT
- [ ] SL mandatory (no entry without SL)
- [ ] TP mandatory (no entry without TP)
- [ ] Kill switch available (Telegram /local)

## 3. Approval Gate

- [ ] Manual approval before first trade (human in loop)
- [ ] Trade Proposal format ready:
  - Symbol, Direction, Entry, SL, TP, Quantity
  - Max loss ($), Expected RR, Confidence
  - Signal Reason, Guardian Verdict
- [ ] No auto-execution before human confirmation

## 4. Emergency Stop

- [ ] `systemctl stop tradingos-observation.service`
- [ ] Bybit API key revocation procedure documented
- [ ] `executor_v0.py --emergency-close` tested
- [ ] Position close via Bybit web UI tested

## 5. Reality Ledger

- [ ] `/root/tradingos/memory/reality_trades.jsonl` schema ready
- [ ] Required fields per trade:
  - `signal_id`, `decision_id`, `symbol`, `direction`
  - `entry`, `exit`, `duration`, `MFE`, `MAE`
  - `max_giveback`, `guardian_actions`
  - `gross_pnl`, `net_pnl`, `fees`, `slippage`
  - `verdict` (TP / SL / Manual)
- [ ] Auto-append on trade closure

## 6. Micro Capital

- [ ] Account funded with ≤ $30 (30 trades × $1 max loss)
- [ ] Leverage: 1x (no leverage for micro validation)
- [ ] No strategy changes before 30 trades completed

## 7. Pre-flight (T-1)

- [ ] Executor dry-run: `python3 executor_v0.py --dry-run decision.json` → Guardian ALLOWED
- [ ] Bybit position check: `python3 executor_v0.py --check-close` → NO OPEN POSITIONS
- [ ] Balance check: > $30 available
- [ ] Network check: API reachable, proxy OK
- [ ] Telegram bot responsive
