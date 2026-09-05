#!/usr/bin/env python3
"""h2_gates.py — gate metrics computed from the order ledger on demand.

Reduces the append-only pilot_ledger.jsonl into per-attempt records and reports:
N filled, fill-rate, AS bps (fill_px -> mid at +5s/+30s/+60s/+5m), realized NET bps/attempt,
NET $/month at $1,000 model, fallback rate (=0 by design), decomposition table.
"""
import os, sys, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from h2_logger import load_ledger, ROOT, SIGNALS
from h2_lib import py, load_jsonl


def assemble(rows):
    """Group ledger facts by attempt_id into one record per attempt."""
    attempts = {}
    for r in rows:
        aid = r.get("attempt_id")
        if not aid:
            continue
        a = attempts.setdefault(aid, {"attempt_id": aid, "evts": []})
        a["evts"].append(r)
    out = []
    for aid, a in attempts.items():
        evts = {e["evt"]: e for e in a["evts"]}
        rec = {"attempt_id": aid}
        base = evts.get("attempt") or {}
        rec.update(base)
        if "placed" in evts:
            rec["placed"] = evts["placed"]
        if "rejected" in evts:
            rec["rejected"] = evts["rejected"]
            rec["outcome"] = "REJECTED"
        if "cancelled" in evts:
            rec["cancelled_ts"] = evts["cancelled"].get("cancelled_ts")
            rec["cancel_ok"] = evts["cancelled"].get("cancel_ok")
        if "filled" in evts:
            rec["filled"] = evts["filled"]
            rec["outcome"] = "PARTIAL" if evts["filled"].get("partial_flag") else "FILLED"
        if "exit" in evts:
            rec.update(evts["exit"])
        if "outcome" in evts and "outcome" not in rec:
            rec["outcome"] = evts["outcome"]["outcome"]
            rec["outcome_reason"] = evts["outcome"].get("reason")
            if evts["outcome"]["outcome"] == "NO_TRADE":
                rec["no_trade_reason"] = evts["outcome"].get("reason")
        if "as_snapshot" in evts:
            pass
        snaps = {}
        for e in a["evts"]:
            if e["evt"] == "as_snapshot":
                snaps[e.get("offset_s")] = e.get("mid_px")
        rec["as_snapshots"] = snaps
        out.append(rec)
    return out


def as_bps(rec):
    """AS bps at +5/30/60/300s: side-adjusted (mid - fill)/fill from fill_px."""
    f = rec.get("filled")
    if not f:
        return {}
    fp = f.get("fill_px")
    sgn = 1.0 if rec.get("side") == "BUY" else -1.0
    out = {}
    for off, m in (rec.get("as_snapshots") or {}).items():
        if fp and m:
            out[int(off)] = (m / fp - 1) * 1e4 * sgn
    return out


def frequency_report(universe_size_scanned=37, frozen_universe=97, signal_hours=None):
    """NORMALIZED signal frequency (addendum gate): signals/symbol/day and the
    extrapolation to the frozen universe. Raw attempt counts are NOT a rate."""
    try:
        rows = load_jsonl(SIGNALS)
    except Exception:
        rows = []
    sigs = [r for r in rows if r.get("evt") == "signal" and r.get("event_ts")]
    if len(sigs) < 2:
        return {"note": "need >=2 signals to form a rate", "signals": len(sigs)}
    t0 = min(int(r["event_ts"]) for r in sigs)
    t1 = max(int(r["event_ts"]) for r in sigs)
    hours = max((t1 - t0) / 3600000.0, 1e-9)
    # prefer explicit service-up hours when the journal window is just warmup history
    if signal_hours and signal_hours > hours:
        hours = signal_hours
    n = len(sigs)
    sig_per_sym_day = n / (hours / 24.0 * universe_size_scanned)
    out = {
        "signals_observed": n,
        "window_hours": round(hours, 2),
        "universe_scanned": universe_size_scanned,
        "universe_note": "symbols actively polled by the service; frozen liquid universe = 97",
        "signals_per_symbol_day": round(sig_per_sym_day, 5),
        "signals_per_day_scanned_universe": round(sig_per_sym_day * universe_size_scanned, 2),
        "signals_per_day_frozen_universe_97": round(sig_per_sym_day * frozen_universe, 2),
        "projected_signals_week_97": round(sig_per_sym_day * frozen_universe * 7, 1),
        "projected_signals_month_97": round(sig_per_sym_day * frozen_universe * 30, 1),
        "confidence_note": "wide band on short windows; the 8-12 week service exists to tighten it",
    }
    return out


def monthly_net_usd(recs, model_capital=1000.0, universe_size_scanned=37, frozen_universe=97):
    """Dollar-reality projection at $1,000 (addendum gate): projected fills/month
    x realized NET $/filled event, compared to the T12 ~$77/mo maker forecast."""
    filled = [r for r in recs if r.get("outcome") in ("FILLED", "PARTIAL")]
    attempts = [r for r in recs if r.get("outcome") in
                ("FILLED", "PARTIAL", "EXPIRED/CANCELLED", "REJECTED")]
    fr = frequency_report(universe_size_scanned=universe_size_scanned,
                          frozen_universe=frozen_universe)
    if not attempts:
        return {"note": "no attempts", "net_usd_month": None,
                "fill_rate": None, "projected_fills_month": None, **fr}
    fill_rate = len(filled) / len(attempts)
    rpnl = [r.get("realized_pnl_usd") for r in filled
            if r.get("realized_pnl_usd") is not None]
    net_per_fill = (sum(rpnl) / len(rpnl)) if rpnl else None
    spd_97 = fr.get("signals_per_day_frozen_universe_97")
    proj_fills_month = (spd_97 * fill_rate * 30.0) if (spd_97 is not None) else None
    proj_net_month = (proj_fills_month * net_per_fill) if (
        proj_fills_month is not None and net_per_fill is not None) else None
    return {
        **fr,
        "fill_rate": fill_rate,
        "postonly_accept_rate": round(len(attempts) / (len(attempts) + 0), 3),
        "projected_fills_week_97": (proj_fills_month / 30.0 * 7.0) if proj_fills_month else None,
        "projected_fills_month_97": proj_fills_month,
        "realized_net_usd_per_fill": net_per_fill,
        "projected_net_usd_month_97": proj_net_month,
        "t12_forecast_maker_usd_month": 77.0,
        "verdict_vs_t12": ("COLLAPSED" if (proj_net_month is not None and proj_net_month < 30.0)
                           else ("CREDIBLE" if proj_net_month is not None else "PENDING_FILLS")),
        "note": "T12 forecast ~$77/mo maker at $1,000; projection needs >=30 filled events to bind",
    }



def report(rows=None):
    rows = rows if rows is not None else load_ledger()
    recs = assemble(rows)
    attempts = [r for r in recs if r.get("outcome") in
                ("FILLED", "PARTIAL", "EXPIRED/CANCELLED", "REJECTED")]
    filled = [r for r in recs if r.get("outcome") == "FILLED"]
    partial = [r for r in recs if r.get("outcome") == "PARTIAL"]
    expired = [r for r in recs if r.get("outcome") == "EXPIRED/CANCELLED"]
    rejected = [r for r in recs if r.get("outcome") == "REJECTED"]
    no_trade = [r for r in recs if r.get("outcome") == "NO_TRADE"]
    dry_run = [r for r in recs if r.get("outcome") == "DRY_RUN"]

    out = {
        "as_of": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "ledger_events": len(rows),
        "attempts_total": len(recs),
        "decomposition": {
            "all_attempts": len(recs),
            "filled": len(filled),
            "partial": len(partial),
            "expired_cancelled": len(expired),
            "rejected": len(rejected),
            "no_trade": len(no_trade),
            "dry_run_observations": len(dry_run),
            "fallback_taker": 0,
            "fallback_rate": 0.0,
        },
    }
    denom = len(attempts)
    out["fill_rate"] = (len(filled) / denom) if denom else None
    out["filled_rate_pct"] = (100.0 * len(filled) / denom) if denom else None

    # AS from fills
    as_all = {"5": [], "30": [], "60": [], "300": []}
    for r in filled + partial:
        ab = as_bps(r)
        for k in as_all:
            if k in ab and ab[k] is not None:
                as_all[k].append(ab[k])
    out["as_bps"] = {k: (round(sum(v) / len(v), 2) if v else None)
                     for k, v in as_all.items()}

    # realized economics on filled
    rpnl = [r.get("realized_pnl_usd") for r in filled + partial
            if r.get("realized_pnl_usd") is not None]
    gross = [r.get("gross_bps") for r in filled + partial if r.get("gross_bps") is not None]
    out["realized"] = {
        "n_filled_with_pnl": len(rpnl),
        "realized_pnl_usd_sum": round(sum(rpnl), 4) if rpnl else None,
        "realized_pnl_usd_med": round(sorted(rpnl)[len(rpnl) // 2], 4) if rpnl else None,
        "gross_bps_mean": round(sum(gross) / len(gross), 2) if gross else None,
        "gross_bps_med": round(sorted(gross)[len(gross) // 2], 2) if gross else None,
    }

    # no-trade reason histogram
    ntc = {}
    for r in no_trade:
        k = r.get("no_trade_reason") or r.get("outcome_reason") or "?"
        ntc[k] = ntc.get(k, 0) + 1
    out["no_trade_histogram"] = ntc

    out["net_usd_month_1k"] = monthly_net_usd(recs)
    out["verdict_terms"] = {
        "execution_ready": len(filled) >= 3,
        "note": "EXECUTION-READY verdict is infra-correctness based (OAT); fill-rate/AS/NET/frequency from the live pilot",
    }
    return out


def main():
    r = report()
    print(json.dumps(py(r), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
