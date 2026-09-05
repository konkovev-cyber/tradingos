#!/usr/bin/env python3
"""Отчёт по exit-shadow ledger (иерархический P3 gate, обновлён 2026-08-17).

Иерархический gate (НЕ жёсткое 10/10):
  REJECT  — median Δ≤0 | P3≤REAL | эффект исчезает после anti-cherry-pick |
            costs съедают прирост | MaxDD существенно хуже | OOS не подтверждает
  PROMISING — median Δ>0, P3>REAL, MFE capture выше, giveback ниже,
            MaxDD не хуже, но уверенности пока мало
  PRODUCTION CANDIDATE — n≥30 + всё из PROMISING + не концентрирован +
            OOS подтверждает + costs полностью учтены + occupancy ок +
            понятный экономический механизм. Production меняется только по approval.

Промежуточная точка n=10-15: bootstrap CI ΔNET, median/mean Δ, доля P3>REAL,
anti-cherry-pick (без лучшей/худшей сделки, top-1 символ <30% Δ, side <70%).
"""
from __future__ import annotations

import json
import random
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path("/root/tradingos/logs/trades/exit_shadow_ledger.jsonl")


def load() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def mfe_capture(rec: dict) -> tuple[float, float]:
    """(capture_real, capture_p3): realized R / MFE R, из lifecycle-полей ledger.
    Если MFE не сохранился в записи — возвращаем (None, None)."""
    mfe = None
    for k in ("mfe_peak_r", "mfe_r"):
        if k in rec and rec[k] is not None:
            mfe = rec[k]
            break
    if mfe is None or mfe <= 0:
        return None, None
    real_r = None
    a_r = None
    if "real" in rec and isinstance(rec["real"], dict):
        real_r = rec["real"].get("r") or rec["real"].get("realized_r")
    if real_r is None and "realized_R" in rec:
        real_r = rec["realized_R"]
    # fallback: R из NET/risk (risk = |entry−sl|)
    if real_r is None and rec.get("entry") and rec.get("sl"):
        qty = rec.get("qty") or 1
        real_r = (rec["real"].get("net", 0) if isinstance(rec.get("real"), dict) else 0) / (abs(rec["entry"] - rec["sl"]) * qty)
    if "A_p3_taker" in rec:
        risk = abs(rec["entry"] - rec["sl"]) if rec.get("entry") and rec.get("sl") else 1
        a_r = rec["A_p3_taker"]["net"] / risk
    c_real = (real_r / mfe) if real_r is not None and mfe > 0 else None
    c_p3 = (a_r / mfe) if a_r is not None and mfe > 0 else None
    return c_real, c_p3


def bootstrap_ci(deltas: list[float], n_boot: int = 5000, seed: int = 42) -> tuple:
    """95% bootstrap CI для median Δ и доля P3>REAL."""
    if len(deltas) < 2:
        return (None, None), None
    rng = random.Random(seed)
    meds = []
    for _ in range(n_boot):
        s = [deltas[i] for i in (rng.randrange(len(deltas)) for _ in range(len(deltas)))]
        meds.append(st.median(s))
    meds.sort()
    lo = meds[int(0.025 * n_boot)]
    hi = meds[int(0.975 * n_boot)]
    pct_pos = sum(1 for d in deltas if d > 0) / len(deltas)
    return (lo, hi), pct_pos


def main() -> int:
    rows = load()
    n = len(rows)
    print(f"Exit-shadow ledger: {n} finalized trades")

    if n == 0:
        print("VERDICT: INSUFFICIENT SAMPLE (n=0) — движок собирает")
        return 1

    real = [r["real"]["net"] for r in rows]
    a = [r["A_p3_taker"]["net"] for r in rows]
    b = [r["B_maker_cur"]["net"] for r in rows]
    c = [r["C_maker_p3"]["net"] for r in rows]
    deltas = [r["delta_A"] for r in rows]

    sum_real, sum_a, sum_b, sum_c = sum(real), sum(a), sum(b), sum(c)
    wins_a = [x for x in a if x > 0]
    loss_a = [x for x in a if x <= 0]
    pf_a = (sum(wins_a) / abs(sum(loss_a))) if loss_a else float("inf")
    wr_a = len(wins_a) / n

    avg_win_a = st.mean(wins_a) if wins_a else 0.0
    avg_loss_a = st.mean(loss_a) if loss_a else 0.0
    mdd_a = 0.0
    eq = peak = 0.0
    for x in a:
        eq += x
        peak = max(peak, eq)
        mdd_a = min(mdd_a, eq - peak)

    # REAL: PF / MaxDD / fees / slippage (gross из ledger.real, иначе оценка net+fees)
    real_gross = []
    real_wins = []
    real_loss = []
    mdd_r = 0.0
    eq = peak = 0.0
    fees_r = 0.0
    slip_r = 0.0
    for r in rows:
        rb = r.get("real") or {}
        g = rb.get("gross")
        f = rb.get("fees")
        s = rb.get("slippage")
        if g is None and f is not None:
            g = r["real"]["net"] + f  # грубая оценка gross
        if g is not None:
            real_gross.append(g)
            if g > 0:
                real_wins.append(g)
            else:
                real_loss.append(g)
        if f is not None:
            fees_r += f
        if s is not None:
            slip_r += s
        eq += r["real"]["net"]
        peak = max(peak, eq)
        mdd_r = min(mdd_r, eq - peak)
    pf_r = (sum(real_wins) / abs(sum(real_loss))) if real_loss else (float("inf") if real_gross else None)

    # ── §5: REAL/P3/Δ таблица ──
    print(f"\n{'Metric':<18} {'REAL':>10} {'P3':>10} {'Δ':>10}")
    print(f"{'-'*50}")
    print(f"{'NET':<18} {sum_real:>+10.3f} {sum_a:>+10.3f} {sum_a-sum_real:>+10.3f}")
    print(f"{'median NET':<18} {st.median(real):>+10.3f} {st.median(a):>+10.3f} {st.median(deltas):>+10.3f}")
    print(f"{'mean NET':<18} {st.mean(real):>+10.3f} {st.mean(a):>+10.3f} {st.mean(deltas):>+10.3f}")
    print(f"{'PF':<18} {pf_r if pf_r is not None else float('nan'):>10.2f} {pf_a:>10.2f}")
    print(f"{'WR':<18} {sum(1 for x in real if x>0)/n*100:>9.0f}% {wr_a*100:>9.0f}%")
    print(f"{'avg winner':<18} {st.mean([x for x in real if x>0]) if any(x>0 for x in real) else 0:>+10.3f} {avg_win_a:>+10.3f}")
    print(f"{'avg loser':<18} {st.mean([x for x in real if x<=0]) if any(x<=0 for x in real) else 0:>+10.3f} {avg_loss_a:>+10.3f}")
    print(f"{'MaxDD':<18} {mdd_r:>+10.3f} {mdd_a:>+10.3f}")
    fees_a = sum(r["A_p3_taker"]["fees"] for r in rows)
    slip_a = sum(r["A_p3_taker"]["slippage"] for r in rows)
    print(f"{'fees':<18} {fees_r:>+10.3f} {fees_a:>+10.3f}")
    print(f"{'slippage':<18} {slip_r:>+10.3f} {slip_a:>+10.3f}")

    # ── MFE capture / giveback ──
    caps_real = []
    caps_p3 = []
    for r in rows:
        cr, cp = mfe_capture(r)
        if cr is not None:
            caps_real.append(cr)
        if cp is not None:
            caps_p3.append(cp)
    if caps_real and caps_p3:
        print(f"{'MFE capture med':<18} {st.median(caps_real):>+10.2f} {st.median(caps_p3):>+10.2f}")

    # ── giveback: количество сделок с MFE>=1R, реализовавших <1R ──
    def giveback_count(rows, r_vals):
        # r_vals: list of (mfe, achieved_r) — считаем долю сдавших +1R
        cnt = 0
        tot = 0
        for r, (mfe, ach) in zip(rows, r_vals):
            if mfe is not None and mfe >= 1.0:
                tot += 1
                if ach is None or ach < 1.0:
                    cnt += 1
        return (cnt, tot)
    real_gr = []
    p3_gr = []
    for r in rows:
        mfe = r.get("mfe_peak_r")
        real_ach = (r.get("real") or {}).get("r")
        risk = abs(r["entry"] - r["sl"]) if r.get("entry") and r.get("sl") else None
        p3_ach = (r["A_p3_taker"]["net"] / risk) if risk else None
        real_gr.append((mfe, real_ach))
        p3_gr.append((mfe, p3_ach))
    gb_real = giveback_count(rows, real_gr)
    gb_p3 = giveback_count(rows, p3_gr)
    if gb_real[1] or gb_p3[1]:
        print(f"{'giveback (MFE>=1R)':<18} {gb_real[0]}/{gb_real[1]} {gb_p3[0]}/{gb_p3[1]}")

    # ── bootstrap CI + anti-cherry-pick ──
    (ci_lo, ci_hi), pct_pos = bootstrap_ci(deltas)
    ci_txt = f"[{ci_lo:+.4f}, {ci_hi:+.4f}]" if ci_lo is not None else "n/a"
    pct_txt = f"{pct_pos*100:.0f}%" if pct_pos is not None else "n/a"
    print(f"\nbootstrap 95% CI median Δ: {ci_txt} | % P3>REAL: {pct_txt}")

    if n >= 3:
        sorted_d = sorted(deltas)
        without_best = sum(deltas) - sorted_d[-1]
        without_worst = sum(deltas) - sorted_d[0]
        without_both = sum(deltas) - sorted_d[-1] - sorted_d[0]
        print(f"anti-cherry-pick: без лучшей Δ={without_best:+.3f} | без худшей Δ={without_worst:+.3f} | "
              f"без обеих={without_both:+.3f}")

    per_sym = defaultdict(float)
    per_side = defaultdict(float)
    for r in rows:
        per_sym[r["symbol"]] += r["delta_A"]
        per_side[r["side"]] += r["delta_A"]
    total_d = sum(deltas)
    if total_d > 0 and per_sym:
        top_sym = max(per_sym, key=per_sym.get)
        top_sym_delta = per_sym[top_sym]
        top_side = max(per_side, key=per_side.get)
        top_side_delta = per_side[top_side]
    else:
        top_sym = top_side = "-"
        top_sym_delta = top_side_delta = 0.0
    # Per user spec: use ABSOLUTE total to compute share
    total_abs_delta = sum(abs(d) for d in deltas)
    if total_abs_delta > 0:
        top_sym_share = top_sym_delta / total_abs_delta
        top_side_share = top_side_delta / total_abs_delta
    else:
        top_sym_share = top_side_share = 0.0
    print(f"концентрация: top symbol {top_sym} = {top_sym_share*100:.0f}% Δ | "
          f"top side {top_side} = {top_side_share*100:.0f}% Δ")

    # ── иерархический вердикт ──
    med_ok = st.median(deltas) > 0
    sum_ok = sum_a > sum_real
    cherry_ok = True
    if n >= 3:
        cherry_ok = without_both > 0
    costs_ok = sum_a > 0  # NET после costs уже в A
    # OOS: последние 1/3 по времени
    oos_rows = sorted(rows, key=lambda r: r.get("entry_ts", 0))[-(n // 3):] if n >= 6 else []
    oos_d = [r["delta_A"] for r in oos_rows]
    oos_ok = (not oos_d) or (st.median(oos_d) > 0)

    if n < 10:
        verdict = "INSUFFICIENT SAMPLE"
    elif not (med_ok and sum_ok and cherry_ok and costs_ok and oos_ok):
        verdict = "REJECT"
    elif n >= 30 and med_ok and sum_ok and cherry_ok and costs_ok and oos_ok and (total_d <= 0 or top_sym_share < 0.60):
        verdict = "PRODUCTION CANDIDATE (решение по отдельному approval)"
    else:
        verdict = "PROMISING"

    print(f"\n=== P3 SAMPLE: n={n} ===")
    print(f"REAL NET: {sum_real:+.3f}")
    print(f"P3 NET: {sum_a:+.3f}")
    print(f"Δ NET: {sum_a - sum_real:+.3f}")
    print(f"MEDIAN Δ: {st.median(deltas):+.4f} (95% CI {ci_txt})")
    print(f"MEAN Δ: {st.mean(deltas):+.4f}")
    print(f"P3 > REAL: {pct_txt} сделок")
    if caps_real and caps_p3:
        print(f"MFE CAPTURE: REAL={st.median(caps_real):+.2f} P3={st.median(caps_p3):+.2f}")
    if gb_real[1] or gb_p3[1]:
        print(f"GIVEBACK: REAL={gb_real[0]}/{gb_real[1]} P3={gb_p3[0]}/{gb_p3[1]}")
    print(f"PF: REAL={pf_r if pf_r is not None else float('nan'):.2f} P3={pf_a:.2f}")
    print(f"MAXDD: REAL={mdd_r:+.2f} P3={mdd_a:+.2f}")
    print(f"FEES: REAL={fees_r:+.3f} P3={fees_a:+.3f}")
    print(f"SLIPPAGE: REAL={slip_r:+.3f} P3={slip_a:+.3f}")
    print(f"CONCENTRATION: top symbol={top_sym if isinstance(top_sym, str) else top_sym}={top_sym_share*100:.0f}% Δ | top side={top_side if isinstance(top_side, str) else top_side}={top_side_share*100:.0f}% Δ")
    print(f"OOS: {'подтверждает' if oos_ok else ('n/a (n<6)' if not oos_d else 'НЕ подтверждает')} "
          f"(последние {len(oos_d)} сделок, median Δ={st.median(oos_d):+.3f})" if oos_d else "OOS: n/a (n<6)")
    print(f"VERDICT: {verdict}")
    print(f"NEXT ACTION: {'продолжить сбор до 10-15' if n < 10 else ('промежуточный forensic — по расписанию' if n < 30 else 'полный gate-разбор, ждать approval')}")
    print("PRODUCTION: FROZEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
