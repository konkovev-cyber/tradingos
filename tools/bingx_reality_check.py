"""
tools/bingx_reality_check.py
BingX Reality Read Layer (v0.1, pre-HMAC).

Periodically fetches open position symbols from BingX (currently using
unauthenticated GET, which works for some endpoints but may rate-limit).
Compares with PIE DB active positions and reports discrepancies.

READ-ONLY. Does NOT touch Guard, PIE, or live trading.
Does NOT require HMAC signing (yet) — relies on the same partial
API path the adapter already uses.

Usage:
    python3 tools/bingx_reality_check.py --once
    python3 tools/bingx_reality_check.py --watch 300
"""
import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))

ENV_PATH = Path("/opt/ubot_bingx/.env")
if ENV_PATH.exists():
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k in ("BINGX_API_KEY", "BINGX_API_SECRET") and not os.getenv(k):
            os.environ[k] = v

from adapters.bingx.client import BingXAdapter

PIE_DB = ROOT / "tradingos_data.db"
REALITY_LOG = ROOT / "logs" / "bingx_reality_diff.jsonl"

logger = logging.getLogger("bingx_reality")


def load_pie_symbols() -> set:
    """Прочитать уникальные символы из последних BAR_UPDATE в PIE."""
    if not PIE_DB.exists():
        return set()
    try:
        uri = f"file:{PIE_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT symbol FROM position_events
            WHERE id IN (SELECT MAX(id) FROM position_events GROUP BY position_id)
            """
        )
        symbols = {row[0] for row in cur.fetchall() if row[0]}
        conn.close()
        return symbols
    except Exception as e:
        logger.error(f"PIE read failed: {e}")
        return set()


def reconcile_once(adapter: BingXAdapter) -> dict:
    """Один цикл: BingX reality vs PIE."""
    bingx_syms = adapter.get_open_position_symbols_sync(timeout=10.0)
    pie_syms = load_pie_symbols()

    in_both = bingx_syms & pie_syms
    only_bingx = bingx_syms - pie_syms
    only_pie = pie_syms - bingx_syms

    result = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "bingx_count": len(bingx_syms),
        "pie_count": len(pie_syms),
        "in_both_count": len(in_both),
        "only_bingx_count": len(only_bingx),
        "only_pie_count": len(only_pie),
        "only_bingx": sorted(only_bingx),
        "only_pie": sorted(only_pie),
    }

    REALITY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with REALITY_LOG.open("a") as f:
        f.write(json.dumps(result) + "\n")
    return result


def render(result: dict) -> str:
    lines = [
        "=" * 60,
        "  BINGX vs PIE — REALITY DIFF",
        "=" * 60,
        f"  Timestamp:    {result['timestamp_utc']}",
        f"  BingX active: {result['bingx_count']} positions",
        f"  PIE active:   {result['pie_count']} positions",
        f"  In both:      {result['in_both_count']}",
        "",
    ]
    if result["only_bingx"]:
        lines.append(f"  Only in BingX (missing in PIE): {result['only_bingx']}")
    if result["only_pie"]:
        lines.append(f"  Only in PIE (likely closed on BingX): {result['only_pie']}")
    if not result["only_bingx"] and not result["only_pie"]:
        lines.append("  No discrepancies.")
    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="BingX vs PIE Reality Diff")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument("--watch", type=int, default=0, help="Run every N seconds")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )

    adapter = BingXAdapter(mode="SHADOW")

    if args.once:
        result = reconcile_once(adapter)
        print(render(result))
        return 0

    if args.watch > 0:
        try:
            while True:
                result = reconcile_once(adapter)
                print(render(result))
                print()
                time.sleep(args.watch)
        except KeyboardInterrupt:
            return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
