#!/usr/bin/env python3
"""gate0_sign_test.py — Gate #0 synthetic PnL sign test (mandatory, post-DLF-bug discipline).

Independent hand-checkable PnL calculator. NO mirror trick: SHORT PnL is the explicit
(entry - exit)/entry formula. Variants: full fill, partial fill, expired (no fill), cancel.
"""
import os, json, sys

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "gate0_sign_test.json")


def pnl_frac(side, entry, exit_px, fill_qty=1.0, requested_qty=1.0):
    """Realized PnL fraction for a maker-fill position closed at exit_px.

    LONG:  (exit - entry) / entry
    SHORT: (entry - exit) / entry      <- the case the DLF engine inverted.
    Partial fills scale linearly by fill_qty/requested_qty.
    Returns dict with pnl_frac (signed) and pnl_bps.
    """
    if fill_qty <= 0:
        return {"pnl_frac": 0.0, "pnl_bps": 0.0, "outcome": "NO_FILL"}
    frac = ((exit_px - entry) if side == "LONG" else (entry - exit_px)) / entry
    frac *= fill_qty / requested_qty
    return {"pnl_frac": round(frac, 6), "pnl_bps": round(frac * 1e4, 2),
            "outcome": "FILLED" if fill_qty >= requested_qty else "PARTIAL"}


def expired_pnl():
    return {"pnl_frac": 0.0, "pnl_bps": 0.0, "outcome": "EXPIRED/CANCELLED"}


def run():
    cases = [
        {"name": "LONG 100->110", "side": "LONG", "entry": 100, "exit": 110,
         "expect_frac": 0.10, "expect_bps": 1000.0},
        {"name": "LONG 100->90", "side": "LONG", "entry": 100, "exit": 90,
         "expect_frac": -0.10, "expect_bps": -1000.0},
        {"name": "SHORT 100->90 (DLF-bug case)", "side": "SHORT", "entry": 100, "exit": 90,
         "expect_frac": 0.10, "expect_bps": 1000.0},
        {"name": "SHORT 100->110", "side": "SHORT", "entry": 100, "exit": 110,
         "expect_frac": -0.10, "expect_bps": -1000.0},
        {"name": "PARTIAL LONG 100->105 fill 50%", "side": "LONG", "entry": 100,
         "exit": 105, "fill_qty": 0.5, "requested_qty": 1.0,
         "expect_frac": 0.025, "expect_bps": 250.0},
        {"name": "EXPIRED no fill", "side": "LONG", "entry": 100, "exit": None,
         "expect_frac": 0.0, "expect_bps": 0.0, "expired": True},
        {"name": "CANCEL no fill", "side": "SHORT", "entry": 100, "exit": None,
         "expect_frac": 0.0, "expect_bps": 0.0, "expired": True},
    ]
    results = []
    all_pass = True
    for c in cases:
        if c.get("expired"):
            got = expired_pnl()
            pass_ = (got["pnl_frac"] == c["expect_frac"] and got["pnl_bps"] == c["expect_bps"])
        else:
            got = pnl_frac(c["side"], c["entry"], c["exit"],
                           fill_qty=c.get("fill_qty", 1.0),
                           requested_qty=c.get("requested_qty", 1.0))
            pass_ = (abs(got["pnl_frac"] - c["expect_frac"]) < 1e-9
                     and abs(got["pnl_bps"] - c["expect_bps"]) < 1e-6)
        all_pass &= pass_
        results.append({**c, "computed": got, "pass": pass_})
    out = {
        "gate0": "PASS" if all_pass else "FAIL",
        "n_cases": len(cases),
        "results": results,
        "handcheck": {
            "LONG 100->110": "+10% = (110-100)/100", "LONG 100->90": "-10% = (90-100)/100",
            "SHORT 100->90": "+10% = (100-90)/100 (explicit, no mirror trick)",
            "SHORT 100->110": "-10% = (100-110)/100",
        },
    }
    json.dump(out, open(OUT, "w"), indent=2)
    print(json.dumps(out, indent=2))
    return all_pass


if __name__ == "__main__":
    ok = run()
    sys.exit(0 if ok else 1)
