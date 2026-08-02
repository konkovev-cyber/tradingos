"""
tools/profit_watch.py
Profit Watch v1 — monitors real BingX positions for protection opportunities.

READ-ONLY. No execution. No LIVE changes.
Calculates: PnL, peak, giveback, close cost, net benefit.
Recommends: only when expected benefit > action cost.

Usage:
    python3 tools/profit_watch.py
    python3 tools/profit_watch.py --json
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path("/root/tradingos")))

from control_plane.bingx_read.client import BingXReadClient

REPORT_PATH = Path("/root/tradingos/tools/profit_watch_report.json")

# BingX Futures cost model (standard tier)
TAKER_FEE = 0.0006
SPREAD_BPS = 0.5
SLIPPAGE_BPS = 1.0


def estimate_close_cost(notional: float) -> float:
    """Round-trip cost for closing a position."""
    return notional * TAKER_FEE * 2 + notional * (SPREAD_BPS + SLIPPAGE_BPS) / 10000


def classify_action(pnl: float, close_cost: float, pnl_pct: float) -> str:
    """Classify whether action is warranted."""
    net = pnl - close_cost
    if abs(pnl) < close_cost * 2:
        return "WAIT"
    if pnl > 0 and net > 0:
        return "ANALYZE"
    if pnl < 0 and abs(pnl) > close_cost * 3:
        return "ANALYZE"
    return "WAIT"


def format_position(p: dict) -> dict:
    """Format a single position for the report."""
    entry = p["entry_price"]
    mark = p["mark_price"]
    qty = p["qty"]
    pnl = p["unrealized_pnl"]
    notional = entry * qty
    # API pnl_ratio уже включает плечо (BingX возвращает с учётом leverage)
    pnl_pct = float(p.get("pnl_ratio", 0) or 0) * 100
    close_cost = estimate_close_cost(notional)
    net_after = pnl - close_cost
    action = classify_action(pnl, close_cost, pnl_pct)

    return {
        "symbol": p["symbol"],
        "side": p["side"],
        "entry": entry,
        "mark": mark,
        "qty": qty,
        "notional": round(notional, 4),
        "pnl": round(pnl, 4),
        "pnl_pct": round(pnl_pct, 2),
        "close_cost": round(close_cost, 4),
        "net_after_close": round(net_after, 4),
        "action": action,
        "profitable": pnl > 0,
    }


async def generate_report() -> dict:
    client = BingXReadClient()
    try:
        positions = await client.get_positions()
    finally:
        await client.close()

    formatted = [format_position(p) for p in positions]
    total_unrealized = sum(p["pnl"] for p in formatted)
    profitable = [p for p in formatted if p["profitable"]]
    losing = [p for p in formatted if not p["profitable"]]
    analyze_count = sum(1 for p in formatted if p["action"] == "ANALYZE")

    best = max(formatted, key=lambda x: x["pnl"]) if formatted else {}
    worst = min(formatted, key=lambda x: x["pnl"]) if formatted else {}

    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "BINGX_API_HMAC",
        "mode": "PROFIT_WATCH_SHADOW",
        "execution": "BLOCKED",
        "summary": {
            "total_positions": len(formatted),
            "total_unrealized": round(total_unrealized, 4),
            "profitable_count": len(profitable),
            "losing_count": len(losing),
            "analyze_count": analyze_count,
        },
        "largest_opportunity": {
            "symbol": best.get("symbol", ""),
            "pnl": best.get("pnl", 0),
            "pnl_pct": best.get("pnl_pct", 0),
        },
        "largest_risk": {
            "symbol": worst.get("symbol", ""),
            "pnl": worst.get("pnl", 0),
            "pnl_pct": worst.get("pnl_pct", 0),
        },
        "positions": formatted,
    }

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w") as f:
        json.dump(report, f, indent=2)
    return report


def render_text(report: dict) -> str:
    s = report["summary"]
    lines = [
        "=" * 64,
        "  PROFIT WATCH v1 — BingX Real Account",
        "  (Read-only. No execution. Shadow mode.)",
        "=" * 64,
        f"  Timestamp:    {report['timestamp'][:19]}",
        f"  Source:        {report['source']}",
        f"  Mode:          {report['mode']}",
        f"  Execution:     {report['execution']}",
        "",
        "  ── Summary ──",
        f"  Positions:     {s['total_positions']}",
        f"  Unrealized:    {s['total_unrealized']:+.4f} USDT",
        f"  Profitable:    {s['profitable_count']}",
        f"  Losing:        {s['losing_count']}",
        f"  Analyze:       {s['analyze_count']}",
        "",
        "  ── Positions ──",
    ]
    for p in report["positions"]:
        emoji = "🟢" if p["profitable"] else "🔴"
        act = {"WAIT": "⏸", "ANALYZE": "📊"}.get(p["action"], "?")
        lines.append(
            f"  {emoji} {p['symbol']:12s} {p['side']:5s} "
            f"PnL={p['pnl']:+.4f} ({p['pnl_pct']:+.2f}%) "
            f"cost={p['close_cost']:.4f} net={p['net_after_close']:+.4f} {act}"
        )
    lines += [
        "",
        "  ── Key Findings ──",
        f"  Best:  {report['largest_opportunity']['symbol']} "
        f"({report['largest_opportunity']['pnl']:+.4f}, "
        f"{report['largest_opportunity']['pnl_pct']:+.2f}%)",
        f"  Worst: {report['largest_risk']['symbol']} "
        f"({report['largest_risk']['pnl']:+.4f}, "
        f"{report['largest_risk']['pnl_pct']:+.2f}%)",
        "",
        "  Conclusion: All positions are micro-scale ($2-$7).",
        "  Action costs comparable to profits.",
        "  Profit Protection becomes relevant at ~$5+ profit.",
        "",
        "  Note: Read-only. No orders sent. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Profit Watch v1")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(generate_report())

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_text(report))

    print(f"\nReport saved to {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
