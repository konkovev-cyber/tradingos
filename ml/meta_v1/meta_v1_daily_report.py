"""
meta_v1_daily_report.py — daily meta_v1 health check.

Computes:
- Number of meta_v1-scored candidates in last 24h
- Distribution of meta_v1_prob (passed + rejected)
- Top symbols by avg meta_v1_prob
- OOS uplift estimate (placeholder until trade_results accumulate)

Writes: /root/tradingos/ml/meta_v1/daily_report.json
"""
import json
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path


REJECTED_LOG = Path("/root/tradingos/guardian/rejected_candidates.jsonl")
ACCEPTED_LOG = Path("/root/tradingos/memory/signal_log.jsonl")
OUT = Path("/root/tradingos/ml/meta_v1/daily_report.json")


def collect_last_24h() -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
    scored = []  # (passed, meta_v1_prob, symbol, score, reason)

    # Accepted signals: signal_log entries that passed (no reject_reason)
    for line in ACCEPTED_LOG.open():
        try:
            d = json.loads(line)
            if d.get("reject_reason"): continue
            ts = d.get("timestamp", "")
            if not ts: continue
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            if t < cutoff: continue
            # We don't have meta_v1 in signal_log yet — only in candidate dict.
            # We'll get it from rejected_candidates for passed candidates by looking at the
            # "no_direction" / "max_positions" rejections (which happen AFTER pass).
        except Exception:
            continue

    # Rejected candidates WITH meta_v1_prob (the ones we just started collecting)
    for line in REJECTED_LOG.open():
        try:
            d = json.loads(line)
            ts = d.get("record_time", "")
            if not ts: continue
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            if t < cutoff: continue
            p = d.get("meta_v1_prob")
            if p is None: continue
            scored.append({
                "passed": False,
                "meta_v1_prob": float(p),
                "symbol": d["symbol"],
                "score": d.get("score", 0),
                "reason": d.get("reason", ""),
                "ts": t,
            })
        except Exception:
            continue

    return {"cutoff_ts": cutoff, "scored_rejections": scored}


def summarize(scored: list[dict]) -> dict:
    if not scored:
        return {"note": "no meta_v1-scored rejections in last 24h yet"}

    probs = [s["meta_v1_prob"] for s in scored]
    n = len(scored)
    buckets = {"<0.3": 0, "0.3-0.5": 0, "0.5-0.7": 0, "0.7-0.9": 0, ">0.9": 0}
    for p in probs:
        if p < 0.3: buckets["<0.3"] += 1
        elif p < 0.5: buckets["0.3-0.5"] += 1
        elif p < 0.7: buckets["0.5-0.7"] += 1
        elif p < 0.9: buckets["0.7-0.9"] += 1
        else: buckets[">0.9"] += 1

    by_reason = defaultdict(lambda: {"n": 0, "avg_p": 0.0})
    for s in scored:
        r = s["reason"]
        by_reason[r]["n"] += 1
        by_reason[r]["avg_p"] += s["meta_v1_prob"]
    for r, d in by_reason.items():
        if d["n"] > 0:
            d["avg_p"] /= d["n"]

    by_symbol = defaultdict(list)
    for s in scored:
        by_symbol[s["symbol"]].append(s["meta_v1_prob"])
    top_symbols = sorted(
        [(s, sum(p)/len(p), len(p)) for s, p in by_symbol.items()],
        key=lambda x: -x[1]
    )[:10]

    return {
        "n_scored_rejections_24h": n,
        "mean_meta_v1_prob": sum(probs)/n,
        "median_meta_v1_prob": sorted(probs)[n//2],
        "above_0.7_pct": 100 * buckets["0.7-0.9"] / n,
        "distribution_buckets": buckets,
        "by_reason": dict(by_reason),
        "top_symbols_by_avg_prob": [
            {"symbol": s, "avg_prob": round(avg, 3), "n": nn} for s, avg, nn in top_symbols
        ],
    }


def main():
    data = collect_last_24h()
    summary = summarize(data["scored_rejections"])
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "note": "OOS uplift cannot be computed yet — need ≥30 trade_results on demo "
                "for promotion decision. Currently collecting.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
