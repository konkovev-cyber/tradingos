#!/usr/bin/env python3
"""POST_P3 ECONOMIC ENGINE — read-only conditional simulator.

Не трогает production. Не трогает P3 live-shadow. Не делает live-experiment.
Принимает на вход P3 PASS assumption (conditional) и находит:
  - guard-bottleneck (max_open_risk / max_daily_loss / max_total_loss)
  - marginal NET per unit of each lever
  - ОДИН BEST PATH post-P3-gate с explicit критериями
  - failure-mode: "не выгодно ничего из этого, нужно CAPITAL FIRST"

Все числа — UPPER BOUND (IS, n=4 live + n=34 historical). На live-валидацию — falsification при gate.
"""
import json
import statistics
from itertools import product


# === TradingOS current config (R148) ===
CFG = {
    "equity": 61.0,
    "risk_base": 0.50,
    "MAX_NOTIONAL_PCT": 0.20,
    "max_positions": 4,
    "max_same_side": 1,
    "max_open_risk_pct": 0.03,      # guard: max_open_risk = equity*0.03
    "max_daily_loss_pct": 0.015,    # guard: max_daily_loss = equity*0.015
    "max_total_loss_pct": 0.04,      # guard: max_total_loss = equity*0.04
    "min_available_margin_usd": 5.0, # MARGIN_BUFFER
    "fee_round_trip": 0.0011,        # 0.055%*2
    "slippage_per_side": 0.002,      # 2 bps baseline (actual IS 20bps, but model lower-bound)
    "trades_per_day": 5.5,
    "days_per_month": 22,
    "median_actual_risk_at_cap20": 0.39,  # from forensic, cap-hit 85% @ cap 20%
}

# === P3 assumptions (conditional, not yet proven) ===
# P3 PASS = +$0.07/trade (IS, anti-cherry OK, top-1 90% — to be re-tested at gate)
# P3 + adaptive E1 (giveback 0.5→0.3→0.15) = +$0.10/trade (IS)
# P3 FAIL = $0.00 (no improvement over baseline)
P3_DELTAS = {
    "P3 FAIL": 0.00,
    "P3 PASS static 0.75R": 0.07,
    "P3 PASS adaptive E1 (0.5→0.3→0.15)": 0.10,
    "P3 STRONG (OOS confirmed)": 0.13,   # hypothetical, scenario
    "P3 EXCEPTIONAL": 0.18,              # upper bound
}


def guard_bottleneck(equity: float, risk: float, notional_pct: float) -> dict:
    """Returns max_pos, max_daily_SL, max_total_loss, max_trades_per_day_after_loss,
    per-trade actual risk achievable under cap, guard bottleneck flag."""
    open_risk_budget = equity * CFG["max_open_risk_pct"]
    max_pos = min(int(open_risk_budget / risk), CFG["max_positions"]) if risk > 0 else 0
    daily_loss = equity * CFG["max_daily_loss_pct"]
    max_daily_SL = int(daily_loss / risk) if risk > 0 else 0
    total_loss = equity * CFG["max_total_loss_pct"]
    max_total_loss = int(total_loss / risk) if risk > 0 else 0
    # Если после 1 SL в день упираемся в daily_loss
    trades_per_day = CFG["trades_per_day"]
    if max_daily_SL <= 0 and risk > 0:
        guard_bottleneck = "daily_loss_1.5pct_blocks"
    elif max_pos <= 1 and risk > 0:
        guard_bottleneck = "max_open_risk_3pct_blocks"
    else:
        guard_bottleneck = "ok"
    return {
        "open_risk_budget_usd": round(open_risk_budget, 3),
        "max_pos": max_pos,
        "max_daily_SL": max_daily_SL,
        "max_total_loss": max_total_loss,
        "guard_bottleneck": guard_bottleneck,
    }


def simulate(equity: float, risk: float, notional_pct: float, p3_delta: float,
            cost_fraction_reduction: float = 0.0,
            trades_per_day_override: float = None) -> dict:
    """Single scenario: 1 month, conditional."""
    base_trades_per_day = trades_per_day_override or CFG["trades_per_day"]
    g = guard_bottleneck(equity, risk, notional_pct)
    # Если guard-bottleneck = 0 max_pos, throughput падает
    if g["max_pos"] == 0:
        effective_trades_per_day = 0
    elif g["max_daily_SL"] == 0:
        # после 1 SL в день trading останавливается
        # при 5.5 trades/day baseline 0.18 SL rate → expected SL = 1 / day
        # reduction примерно пропорциональна guard
        effective_trades_per_day = base_trades_per_day * 0.55
    else:
        effective_trades_per_day = base_trades_per_day
    # Risk per trade = Δ/trade (P3 effect, в $)
    # p3_delta — это приращение NET/trade при baseline (risk=$0.50, cap=20%)
    # Масштабирование линейное: при risk=$0.75 Δ/trade = p3_delta × 0.75/0.50
    # cap может ограничивать (если cap/equity < risk/median_price, max_risk_achievable = median_actual_risk)
    risk_scale = risk / CFG["risk_base"]
    cap_scale = min(1.0, equity * notional_pct / CFG["equity"] / CFG["MAX_NOTIONAL_PCT"])
    effective_risk = risk * cap_scale  # сколько реально используется
    net_per_trade = p3_delta * risk_scale * cap_scale
    # cost reduction: maker saves 30% fees → это аддитивно, не мультипликативно
    cost_savings_per_trade = effective_risk * cost_fraction_reduction
    net_per_trade += cost_savings_per_trade
    # Уже учтено в p3_delta (P3 forensic — net of costs) + cap_savings
    net_per_day = net_per_trade * effective_trades_per_day
    net_per_month = net_per_day * CFG["days_per_month"]
    return {
        "equity": equity,
        "risk": risk,
        "notional_pct": notional_pct,
        "p3_delta_per_trade": p3_delta,
        "guard": g,
        "trades_per_day_baseline": base_trades_per_day,
        "trades_per_day_effective": round(effective_trades_per_day, 2),
        "risk_scale": round(risk_scale, 3),
        "cap_scale": round(cap_scale, 3),
        "net_per_trade": round(net_per_trade, 4),
        "net_per_day": round(net_per_day, 4),
        "net_per_month": round(net_per_month, 2),
    }


def main():
    # === A. Baseline (no P3) at current config ===
    print("=" * 80)
    print("POST-P3 ECONOMIC ENGINE — Conditional Simulator")
    print("=" * 80)

    # === A. baseline vs P3 at current equity ===
    print("\n=== A. CURRENT EQUITY $61 ===")
    print("  Net/день как функция (P3 scenario, risk, cap, equity)")
    print()
    rows = []
    for p3_label, p3_delta in P3_DELTAS.items():
        for risk in (0.50, 0.60, 0.75, 1.00):
            for cap in (0.20, 0.30):
                sim = simulate(equity=61, risk=risk, notional_pct=cap, p3_delta=p3_delta)
                sim["p3_label"] = p3_label
                rows.append(sim)
    # Compact table
    print(f"  {'P3 scenario':<35} {'risk':<6} {'cap%':<6} {'/day':<8} {'/month':<8} {'pos':<4} {'guard':<25}")
    for sim in rows:
        p3_label_short = {
            "P3 FAIL": "P3 FAIL",
            "P3 PASS static 0.75R": "P3 static 0.75R",
            "P3 PASS adaptive E1 (0.5→0.3→0.15)": "P3+E1",
            "P3 STRONG (OOS confirmed)": "P3 STRONG",
            "P3 EXCEPTIONAL": "P3 EXCEPTIONAL",
        }[sim["p3_label"]]
        print(f"  {p3_label_short:<35} ${sim['risk']:<5.2f} {sim['notional_pct']*100:<5.0f}% "
              f"${sim['net_per_day']:<7.2f} ${sim['net_per_month']:<7.2f} "
              f"{sim['guard']['max_pos']:<4d} {sim['guard']['guard_bottleneck']:<25}")

    # === B. P3+E1+cap30%+risk0.75 at multiple equity levels (capital scaling) ===
    print("\n=== B. P3+E1+cap30%+risk0.75 ACROSS EQUITY (capital scaling) ===")
    print(f"  {'equity':<8} {'risk':<6} {'cap%':<6} {'/day':<8} {'/month':<8} {'pos':<4} {'guard':<25}")
    for equity in (61, 80, 100, 150, 200, 500):
        sim = simulate(equity=equity, risk=0.75, notional_pct=0.30,
                      p3_delta=P3_DELTAS["P3 PASS adaptive E1 (0.5→0.3→0.15)"])
        print(f"  ${equity:<6.0f} ${sim['risk']:<5.2f} {sim['notional_pct']*100:<5.0f}% "
              f"${sim['net_per_day']:<7.2f} ${sim['net_per_month']:<7.2f} "
              f"{sim['guard']['max_pos']:<4d} {sim['guard']['guard_bottleneck']:<25}")

    # === C. Marginal NET per unit of lever ===
    print("\n=== C. MARGINAL NET per unit of each lever (P3+E1 baseline) ===")
    base = simulate(equity=61, risk=0.50, notional_pct=0.20,
                    p3_delta=P3_DELTAS["P3 PASS adaptive E1 (0.5→0.3→0.15)"])
    print(f"  Baseline: equity=$61, risk=$0.50, cap=20%, P3+E1 → "
          f"${base['net_per_day']:.2f}/day, ${base['net_per_month']:.2f}/month")
    print()
    # marginal / risk (linear)
    for risk in (0.60, 0.75, 1.00):
        sim = simulate(equity=61, risk=risk, notional_pct=0.20,
                      p3_delta=P3_DELTAS["P3 PASS adaptive E1 (0.5→0.3→0.15)"])
        if sim["net_per_day"] > 0:
            marginal_per_dollar = (sim["net_per_day"] - base["net_per_day"]) / (risk - 0.50)
            print(f"  +risk $0.50→${risk}: ΔNET/день ${sim['net_per_day']-base['net_per_day']:+.3f}  "
                  f"marginal / Δrisk = ${marginal_per_dollar:.3f}/Δ$1 risk "
                  f"(guard: {sim['guard']['guard_bottleneck']})")
        else:
            print(f"  +risk $0.50→${risk}: NET<=0 (guarding blocks), Δ=${sim['net_per_day']-base['net_per_day']:+.3f}")
    # marginal / cap
    for cap in (0.25, 0.30, 0.35, 0.40):
        sim = simulate(equity=61, risk=0.50, notional_pct=cap,
                      p3_delta=P3_DELTAS["P3 PASS adaptive E1 (0.5→0.3→0.15)"])
        if sim["net_per_day"] > 0:
            marginal_per_pct = (sim["net_per_day"] - base["net_per_day"]) / ((cap - 0.20) * 100)
            print(f"  +cap 20%→{cap*100:.0f}%: ΔNET/день ${sim['net_per_day']-base['net_per_day']:+.3f}  "
                  f"marginal / Δcap = ${marginal_per_pct:.3f}/Δ1% cap")
    # marginal / equity
    print()
    for equity in (80, 100, 150, 200, 500):
        sim = simulate(equity=equity, risk=0.50, notional_pct=0.20,
                      p3_delta=P3_DELTAS["P3 PASS adaptive E1 (0.5→0.3→0.15)"])
        marginal_per_capital = (sim["net_per_day"] - base["net_per_day"]) / (equity - 61)
        print(f"  +equity $61→${equity}: ΔNET/день ${sim['net_per_day']-base['net_per_day']:+.3f}  "
              f"marginal / Δcapital = ${marginal_per_capital:.3f}/Δ$1 equity")
    # marginal / cost reduction (maker)
    print()
    for cost_reduction in (0.05, 0.10, 0.15, 0.20, 0.30):
        sim = simulate(equity=61, risk=0.50, notional_pct=0.20,
                      p3_delta=P3_DELTAS["P3 PASS adaptive E1 (0.5→0.3→0.15)"],
                      cost_fraction_reduction=cost_reduction)
        marginal_per_cost = (sim["net_per_day"] - base["net_per_day"]) / (cost_reduction * 100)
        print(f"  +cost -{int(cost_reduction*100)}%: ΔNET/день ${sim['net_per_day']-base['net_per_day']:+.3f}  "
                  f"marginal / Δcost = ${marginal_per_cost:.3f}/Δ1% cost")
    # marginal / Δ/trade
    print()
    print("  marginal / Δ-trade (если P3 +0.10 → +0.13):")
    for delta in (0.13, 0.15, 0.20):
        sim = simulate(equity=61, risk=0.50, notional_pct=0.20, p3_delta=delta)
        marginal_per_dtrade = (sim["net_per_day"] - base["net_per_day"]) / (delta - 0.10)
        print(f"  +ΔP3 0.10→{delta}: ΔNET/день ${sim['net_per_day']-base['net_per_day']:+.3f}  "
              f"marginal / ΔΔtrd = ${marginal_per_dtrade:.3f}/Δ$0.01/trade")

    # === D. RANKING по marginal NET (sorted) ===
    print("\n=== D. RANKING рычагов по marginal NET (best first) ===")
    marginals = []
    # risk 0.50 → 0.75 (linear, no guard bottleneck)
    sim_r = simulate(61, 0.75, 0.20, 0.10)
    if sim_r["net_per_day"] > 0:
        marginals.append(("risk $0.50→$0.75", sim_r["net_per_day"] - base["net_per_day"],
                          (sim_r["net_per_day"] - base["net_per_day"]) / 0.25,
                          sim_r["guard"]["guard_bottleneck"]))
    sim_cap = simulate(61, 0.50, 0.30, 0.10)
    if sim_cap["net_per_day"] > 0:
        marginals.append(("cap 20%→30%", sim_cap["net_per_day"] - base["net_per_day"],
                          (sim_cap["net_per_day"] - base["net_per_day"]) / 0.10,
                          sim_cap["guard"]["guard_bottleneck"]))
    sim_eq = simulate(150, 0.50, 0.20, 0.10)
    if sim_eq["net_per_day"] > 0:
        marginals.append(("equity $61→$150", sim_eq["net_per_day"] - base["net_per_day"],
                          (sim_eq["net_per_day"] - base["net_per_day"]) / 89,
                          sim_eq["guard"]["guard_bottleneck"]))
    sim_cost = simulate(61, 0.50, 0.20, 0.10, cost_fraction_reduction=0.20)
    marginals.append(("cost -20% (maker)", sim_cost["net_per_day"] - base["net_per_day"],
                      (sim_cost["net_per_day"] - base["net_per_day"]) / 0.20,
                      sim_cost["guard"]["guard_bottleneck"]))
    sim_dt = simulate(61, 0.50, 0.20, 0.15)
    marginals.append(("ΔP3 0.10→0.15 (better edge)", sim_dt["net_per_day"] - base["net_per_day"],
                      (sim_dt["net_per_day"] - base["net_per_day"]) / 0.05,
                      sim_dt["guard"]["guard_bottleneck"]))
    marginals.sort(key=lambda x: x[1], reverse=True)
    print(f"  {'Lever':<28} {'ΔNET/день':>10} {'marginal':>10} {'guard':<25}")
    for name, delta_net, marginal, guard in marginals:
        print(f"  {name:<28} ${delta_net:>+9.3f} {marginal:>10.3f} {guard:<25}")

    # === E. ONE BEST PATH (P3 PASS scenario) ===
    print("\n=== E. ONE BEST PATH (P3 PASS assumption) ===")
    print()
    print("  Stage 1: P3 production controlled test")
    print("    - condition: P3 gate 10/10 PASS at n=30 live-shadow")
    print("    - action: deploy P3 as production exit, controlled test 1 stage")
    print("    - measure: 30 trades, NET/trade, MaxDD, OOS stability")
    print("    - PASS: median ΔNET/trade ≥ +$0.05 sustained; MaxDD ≤ 1.5× baseline")
    print("    - FAIL: median ΔNET/trade < $0.05 OR MaxDD > 1.5× baseline OR ООS degrades")
    print()
    print("  Stage 2: P3+adaptive E1 (giveback 0.5→0.3→0.15) controlled test")
    print("    - condition: stage 1 PASS, n≥30 live validated")
    print("    - action: parallel profile в shadow, paired analysis")
    print("    - measure: P3+E1 vs P3, median ΔNET, anti-cherry, OOS")
    print("    - PASS: ΔNET(P3+E1 vs P3) > $0.02 sustained, anti-cherry > 0")
    print("    - FAIL: не улучшает P3, ООS нестабильно")
    print()
    print("  Stage 3: cap 20%→30% controlled test")
    print("    - condition: stages 1-2 PASS, n≥10 controlled")
    print("    - action: bump notional cap to 30%, measure NET и concentration")
    print("    - measure: median actual risk → $0.50 (полное), MaxDD vs Stage 1")
    print("    - PASS: median risk $0.50 достигнута, concentration не выросла, MaxDD ок")
    print("    - FAIL: cap насыщает side concentration, MaxDD растёт, risk-effect saturates")
    print()
    print("  Stage 4: risk $0.50→$0.60 controlled test (NOT $0.75 — guard bottleneck)")
    print("    - condition: stages 1-3 PASS, n≥10 controlled")
    print("    - action: bump risk to $0.60 (max_pos 3, max_daily_SL 1)")
    print("    - measure: NET/день, MaxDD, throughput (trade/day снижается с daily_loss block)")
    print("    - PASS: NET/день × 1.18 (linear), MaxDD ≤ 1.3×")
    print("    - FAIL: risk scale не даёт пропорционального NET, MaxDD растёт, daily_loss 1.5% блокирует")
    print()
    print("  Stage 5: capital scaling $61→$100-150")
    print("    - condition: stages 1-4 PASS, 30+ controlled trades validated")
    print("    - action: capital injection / trade-history-driven, gradual")
    print("    - measure: NET/день, MaxDD, frequency effect (more capital → more slots)")
    print("    - PASS: NET scales linearly or superlinearly, throughput increases")
    print("    - FAIL: liquidity / slippage degradation dominates, NET doesn't scale")
    print()
    print("  Stage 6: maker pass-readiness")
    print("    - condition: stages 1-5 PASS, n≥10 with cap 30% + risk 0.60")
    print("    - action: maker fill-model validation (micro-live, separate approval)")
    print("    - measure: NET savings (~$0.005/trade on $61), adverse selection")
    print("    - PASS: fill-rate ≥60%, no adverse selection, NET/день +$0.03")
    print("    - FAIL: fill-rate low or adverse selection detected → maker отложен")

    # === F. FAILURE CASE: even best P3 не делает material NET ===
    print("\n=== F. CRITICAL FAILURE CASE (anti-cherry on the BEST CASE) ===")
    sim_best = simulate(equity=500, risk=0.75, notional_pct=0.30,
                        p3_delta=P3_DELTAS["P3 EXCEPTIONAL"])
    print(f"  Best case: equity $500 + risk $0.75 + cap 30% + P3 EXCEPTIONAL")
    print(f"  → NET/день: ${sim_best['net_per_day']:.2f}, NET/month: ${sim_best['net_per_month']:.2f}")
    if sim_best["net_per_day"] < 1.0:
        print(f"  → Даже в ЛУЧШЕМ случае P3+composition даёт <$1/day")
    sim_capital_first = simulate(equity=200, risk=0.50, notional_pct=0.20,
                                p3_delta=P3_DELTAS["P3 STRONG (OOS confirmed)"])
    print(f"  Capital-first: $200 + risk $0.50 + cap 20% + P3 STRONG")
    print(f"  → NET/день: ${sim_capital_first['net_per_day']:.2f}, NET/month: ${sim_capital_first['net_per_month']:.2f}")
    print()
    print("  'Capital first' hypothesis: scaling capital by 3.3x ($61→$200) provides")
    print("  more absolute NET than most risk/cap/maker combinations at $61.")
    print("  BUT: requires (a) P3 PASS at n=30, (b) capital source available, (c) risk accepted.")

    # === G. CRITICAL QUESTION answer (falsification mode) ===
    print("\n=== G. CRITICAL QUESTION — Mathematically ===")
    print("  Что выгоднее после доказанного P3: risk, cap, capital, costs, или ΔNET/trade?")
    print("  Ответ: ranked by marginal NET / marginal effort")
    for name, delta_net, marginal, guard in marginals[:5]:
        print(f"    {name}: marginal = {marginal:.3f} per unit (guard: {guard})")
    print()
    print("  'BEST LEVER' depends on guard-bottleneck:")
    print("  - If max_pos ≥ 3 AND max_daily_SL ≥ 1: risk-scaling works")
    print("  - If max_pos ≤ 1 OR max_daily_SL = 0: capital-scaling dominates")
    print("  - If cap=20% blocking median risk: cap-scaling works")
    print("  - If fill-rate/quality low: maker is secondary, capital/ΔNET-trd priority")

    # === H. WHAT CAN BE DONE TODAY ===
    print("\n=== H. WHAT CAN BE DONE TODAY (read-only preparation) ===")
    print("  1. research/post_p3_economic_engine.py — THIS simulator (NOW)")
    print("  2. research/capacity_visualizer.py — interactive guard-table")
    print("  3. research/maker_pass_readiness_checklist.md — pre-deploy checks")
    print("  4. research/p3_gate_criteria.md — explicit 10/10 conditions (R150)")
    print("  5. research/risk_ladder_test_design.md — Stage 4 protocol")
    print()
    print("  НЕ ДЕЛАТЬ: deploy, restart, config, live experiment, change P3/risk/cap/entry/ADX/SL/TP")

    # === I. JSON output for downstream use ===
    out = {
        "config": CFG,
        "P3_DELTAS": P3_DELTAS,
        "rows_equity61": [
            {k: v for k, v in r.items() if k != "guard" or True}
            for r in rows
        ],
        "ranking_marginal": [
            {"lever": name, "delta_net_per_day": d, "marginal": m, "guard": g}
            for name, d, m, g in marginals
        ],
    }
    with open("/tmp/post_p3_economic_engine_output.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("\n  → JSON output: /tmp/post_p3_economic_engine_output.json")


if __name__ == "__main__":
    main()
