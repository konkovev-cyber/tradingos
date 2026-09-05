"""
services/action_shadow_worker.py
Position Action Shadow v0.1 — worker.
Reads PIE recommendations, evaluates against policy, logs Shadow Actions.

Does NOT touch Guard, PIE, or any live system. No HMAC, no execution.
"""
import argparse
import logging
import sys
import time
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))

from core.position.actions.models import ActionPolicy
from core.position.actions.action_shadow import ActionShadow
from core.position.actions.pie_source import PIESource


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )


def main() -> int:
    setup_logging()
    log = logging.getLogger("action_shadow_worker")

    parser = argparse.ArgumentParser(description="Position Action Shadow v0.1")
    parser.add_argument("--once", action="store_true", help="Single cycle then exit")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval sec")
    parser.add_argument("--window", type=int, default=60,
                        help="PIE rec window in minutes")
    parser.add_argument("--pie-db", default=str(ROOT / "tradingos_data.db"))
    args = parser.parse_args()

    policy = ActionPolicy(
        enabled=True,
        max_recommendation_age_hours=4.0,
        min_health=60.0,
        min_pnl_pct_to_act=0.5,
        partial_close_pct=25.0,
        breakeven_buffer_pct=0.05,
    )
    pie = PIESource(db_path=Path(args.pie_db))
    shadow = ActionShadow(policy=policy, pie_source=pie)

    log.info(f"Action Shadow v0.1: policy={policy}, window={args.window}min")

    if args.once:
        log.info("--- Single cycle (--once) ---")
        shadow.run(since_minutes=args.window)
        return 0

    cycle = 0
    while True:
        cycle += 1
        try:
            log.info(f"--- Cycle {cycle} ---")
            shadow.run(since_minutes=args.window)
        except Exception:
            log.exception("Action Shadow cycle failed (continuing...)")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
