"""
services/position_guard_worker.py
Standalone worker for Position Guard v1.
Reads positions from PIE DB (via PIE Bridge), reconciles with BingX reality,
runs Decision Engine only on VALID snapshots.
Runs in a loop with 60s interval. Robust against single-cycle failures.
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))


def _load_bingx_credentials() -> None:
    """Load BINGX_API_KEY / BINGX_API_SECRET from /opt/ubot_bingx/.env if present."""
    env_path = Path("/opt/ubot_bingx/.env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key in ("BINGX_API_KEY", "BINGX_API_SECRET") and not os.getenv(key):
            os.environ[key] = val


_load_bingx_credentials()

from core.position.models import GuardConfig
from core.position.position_guard import PositionGuard
from core.position.pie_bridge import PIEBridge
from core.position.reconciler import PositionReconciler
from adapters.bingx.client import BingXAdapter


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )


def main() -> None:
    setup_logging()
    log = logging.getLogger("position_guard_worker")

    parser = argparse.ArgumentParser(description="Position Guard Shadow Worker")
    parser.add_argument(
        "--once", action="store_true", help="Single observation cycle then exit"
    )
    parser.add_argument(
        "--interval", type=int, default=60, help="Loop interval in seconds"
    )
    parser.add_argument(
        "--pie-db", default=str(ROOT / "tradingos_data.db"), help="Path to PIE SQLite DB"
    )
    parser.add_argument(
        "--no-reconciler", action="store_true",
        help="Disable BingX reality check (for testing only)"
    )
    args = parser.parse_args()

    log.info("Position Guard v1 starting (SHADOW + Reconciler)...")

    config = GuardConfig(
        enabled=True,
        pnl_trigger=1.0,
        mfe_trigger=1.5,
        health_min=80.0,
        protect_profit_pct=0.5,
    )

    adapter = BingXAdapter(mode=BingXAdapter.get_mode_from_env())
    bridge = PIEBridge(db_path=Path(args.pie_db))

    if args.no_reconciler:
        log.warning("Reconciler DISABLED — running without BingX reality check")
        reconciler = PositionReconciler(bingx_symbols_provider=lambda: set())
    else:
        reconciler = PositionReconciler(
            bingx_symbols_provider=adapter.get_open_position_symbols_sync
        )

    guard = PositionGuard(
        config=config, adapter=adapter, bridge=bridge, reconciler=reconciler
    )

    log.info(
        f"Config: enabled={config.enabled} pnl={config.pnl_trigger}% "
        f"mfe={config.mfe_trigger}% health>={config.health_min} "
        f"protect={config.protect_profit_pct}%"
    )
    log.info(f"Adapter mode: {adapter.mode} | PIE DB: {args.pie_db}")

    if args.once:
        log.info("--- Single cycle (--once) ---")
        try:
            guard.run()
        except Exception:
            log.exception("Single cycle failed")
        return

    cycle = 0
    while True:
        cycle += 1
        try:
            log.info(f"--- Cycle {cycle} ---")
            guard.run()
        except Exception:
            log.exception("Position Guard cycle failed (continuing...)")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
