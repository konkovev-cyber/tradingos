#!/usr/bin/env python3
"""CLEAN_AUTO_EPOCH_V1 — checkpoint analyzer (READ-ONLY).

Runs after every 5 CLEAN closures: metrics, outlier dependence, cost drag,
Guardian exit split, verdict classification per contract section 8-13, 16-17.
Outputs: checkpoints/<n>.csv + summary printed.
"""
import json
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path("/root/tradingos")
OUT = ROOT / "research" / "clean_epoch_v1"
EPOCH = OUT / "epoch_v1.jsonl"


def load():
    rows = []
    for line in EPOCH.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return sorted(rows, key=lambda r: r["ts"])


def checkpoint(rows):
    n = len(rows)
    nets = [r["net"] for r in rows]
    gross = sum(r.get("gross") or r["net"] for r in rows)
    fees = sum(r.get("fees") or 0 for r in rows)
    fund = sum(r.get("funding") or 0 for r in rows)
    slip = sum(r.get("slippage") or 0 for r in rows)
    net_tot = sum(nets)
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    wr = len(wins) / n if n else 0
    net_r = [r["net_r"] for r in rows if r.get("net_r") is not None]
    risk_tot = sum(r["actual_initial_risk"] for r in rows)
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else (float("inf") if wins else 0)
    top = sorted(nets, reverse=True)
    top1 = top[0] if top else 0
    top2 = top[1] if len(top) > 1 else 0
    no_top1 = net_tot - top1
    no_top2 = net_tot - top1 - top2
    caps = sum(1 for r in rows if r.get("cap_limited"))
    manual = sum(1 for r in rows if r.get("manual_intervention"))
    tech = sum(1 for r in rows if r.get("eligibility") != "CLEAN")
    exits = Counter(r.get("guardian_trigger") or r.get("outcome") or "NONE" for r in rows)
    mfe = [r["mfe_peak_r"] for r in rows if r.get("mfe_peak_r") is not None]
    mae = [r["mae_trough_r"] for r in rows if r.get("mae_trough_r") is not None]
    # capture: realized NET vs theoretical MFE $ (only where both in $ are derivable —
    # MFE is ladder-relative; report as informational, flagged unusable per contract)
    capture = "N/A (ladder-relative MFE)"
    return {
        "n": n, "wr": round(wr, 3),
        "gross": round(gross, 4), "fees": round(fees, 4), "funding": round(fund, 4),
        "slippage": round(slip, 4), "net": round(net_tot, 4),
        "avg_net": round(statistics.mean(nets), 4) if nets else 0,
        "med_net": round(statistics.median(nets), 4) if nets else 0,
        "avg_net_r": round(statistics.mean(net_r), 4) if net_r else None,
        "med_net_r": round(statistics.median(net_r), 4) if net_r else None,
        "expectancy": round(statistics.mean(nets), 4) if nets else 0,
        "pf": round(pf, 3) if pf != float("inf") else None,
        "risk_total": round(risk_tot, 4),
        "top1": round(top1, 4), "top1_pct": round(top1 / net_tot * 100, 1) if net_tot else 0,
        "top2_pct": round((top1 + top2) / net_tot * 100, 1) if net_tot else 0,
        "no_top1": round(no_top1, 4), "no_top2": round(no_top2, 4),
        "max_dd": _max_dd(nets),
        "cap_limited": caps, "manual_contam": manual, "tech_excl": tech,
        "exit_split": dict(exits),
        "mfe_med": round(statistics.median(mfe), 3) if mfe else None,
        "mae_med": round(statistics.median(mae), 3) if mae else None,
        "capture": capture,
        "streaks": _streaks(nets),
    }


def _max_dd(nets):
    peak = cum = 0.0
    mdd = 0.0
    for x in nets:
        cum += x
        peak = max(peak, cum)
        mdd = min(mdd, cum - peak)
    return round(mdd, 4)


def _streaks(nets):
    best = cur = 0
    for x in nets:
        if x <= 0:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def class_split(rows):
    """T97: market-class split (CRYPTO vs STOCK) — allocation-relevant observation."""
    split = {}
    for cls in ("CRYPTO", "STOCK"):
        r = [x for x in rows if x.get("market_class") == cls]
        if not r:
            split[cls] = None
            continue
        nets = [x["net"] for x in r]
        net_r = [x["net_r"] for x in r if x.get("net_r") is not None]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        split[cls] = {
            "n": len(r),
            "net": round(sum(nets), 4),
            "avg_net_r": round(statistics.mean(net_r), 4) if net_r else None,
            "med_net_r": round(statistics.median(net_r), 4) if net_r else None,
            "wr": round(len(wins) / len(r), 3),
            "pf": round(sum(wins) / abs(sum(losses)), 3) if losses and sum(losses) != 0 else None,
            "fees": round(sum(x.get("fees") or 0 for x in r), 4),
            "mfe_med": round(statistics.median([x["mfe_peak_r"] for x in r if x.get("mfe_peak_r") is not None]), 3)
                if any(x.get("mfe_peak_r") is not None for x in r) else None,
            "mae_med": round(statistics.median([x["mae_trough_r"] for x in r if x.get("mae_trough_r") is not None]), 3)
                if any(x.get("mae_trough_r") is not None for x in r) else None,
            "cap_limited": sum(1 for x in r if x.get("cap_limited")),
        }
    return split


def verdict(c):
    n = c["n"]
    if n < 20:
        return "INSUFFICIENT (n<20 — PASS forbidden)"
    if n < 30:
        if c["net"] <= 0:
            return "EARLY_NEGATIVE" if n >= 20 else "INSUFFICIENT"
        return "EARLY_POSITIVE (n<30 — not final)"
    # n >= 30
    if c["net"] > 0 and c["pf"] and c["pf"] > 1 and c["avg_net_r"] and c["avg_net_r"] > 0:
        if c["no_top2"] <= 0:
            return "OUTLIER_DEPENDENT (top-2 carry the result)"
        return "POSITIVE DISTRIBUTION"
    if c["net"] <= 0:
        return "NEGATIVE DISTRIBUTION"
    return "INCONCLUSIVE"


def main():
    rows = load()
    n = len(rows)
    print(f"=== CLEAN_AUTO_EPOCH_V1 | CLEAN trades: {n} ===")
    if n == 0:
        print("no clean trades yet")
        return
    c = checkpoint(rows)
    print(f"N={c['n']} WR={c['wr']:.0%} Gross={c['gross']:+.2f} Fees={c['fees']:.3f} "
          f"Funding={c['funding']:.3f} Slip={c['slippage']:.3f} NET={c['net']:+.3f}")
    print(f"NET/trade avg={c['avg_net']:+.4f} med={c['med_net']:+.4f} | NET/R avg={c['avg_net_r']} "
          f"med={c['med_net_r']} | Expectancy={c['expectancy']:+.4f} | PF={c['pf']}")
    print(f"1R$ median (risk): {statistics.median([r['actual_initial_risk'] for r in rows]):.3f} | "
          f"CAP_LIMITED={c['cap_limited']}/{c['n']} | manual={c['manual_contam']} tech_excl={c['tech_excl']}")
    print(f"Top1={c['top1']:+.3f} ({c['top1_pct']}% NET) | Top2={c['top2_pct']}% | "
          f"no_top1={c['no_top1']:+.3f} no_top2={c['no_top2']:+.3f} | maxDD={c['max_dd']}")
    print(f"Exit split: {c['exit_split']} | MFE med={c['mfe_med']} MAE med={c['mae_med']} "
          f"| capture={c['capture']} | max losing streak={c['streaks']}")
    print(f"VERDICT: {verdict(c)}")

    # T97: market-class split (CRYPTO vs STOCK)
    print("\nMARKET-CLASS SPLIT (allocation-relevant observation only):")
    for cls, s in class_split(rows).items():
        if s is None:
            print(f"  {cls}: n=0 (no clean samples yet)")
        else:
            print(f"  {cls}: n={s['n']} NET={s['net']:+.4f} NET/R avg={s['avg_net_r']} med={s['med_net_r']} "
                  f"WR={s['wr']:.0%} PF={s['pf']} fees={s['fees']:.4f} MFEmed={s['mfe_med']} "
                  f"MAEmed={s['mae_med']} CAP={s['cap_limited']}")

    (OUT / "checkpoints").mkdir(exist_ok=True)
    with (OUT / "checkpoints" / f"checkpoint_{n}.json").open("w") as f:
        payload = dict(c)
        payload["class_split"] = class_split(rows)
        json.dump(payload, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
