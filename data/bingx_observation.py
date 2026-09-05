#!/usr/bin/env python3
"""
bingx_observation.py — BingX Reality observation + execution.
Mirrors run_observation.py but uses BingXAdapter instead of BybitAdapter.

Запуск:
  python3 /root/tradingos/data/bingx_observation.py
  systemctl start tradingos-bingx-reality.service
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/trading_brain_v4")

# Load BingX credentials
_ENV_PATH = "/opt/ubot_bingx/.env"
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                k, v = _line.split("=", 1)
                os.environ[k] = v

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("bingx_observation")

from exchange.bingx.adapter import BingXAdapter
from exchange.bingx.client import BingXError

# Config
POLL_INTERVAL = 30  # seconds
TRADING_MODE_PATH = "/root/tradingos/operations/bingx_trading_mode.json"
STATE_PATH = "/root/tradingos/guardian/bingx_state.json"
ALERTS_PATH = "/root/tradingos/guardian/bingx_alerts.jsonl"


def load_config():
    try:
        with open(TRADING_MODE_PATH) as f:
            return json.load(f)
    except Exception:
        return {
            "mode": "AUTO",
            "risk_per_trade": 0.15,
            "max_positions": 3,
            "max_leverage": 3,
            "confidence_threshold": 0.55,
        }


def load_state():
    try:
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, default=str)


async def main():
    logger.info("=== BingX Reality observation starting ===")
    config = load_config()
    logger.info(f"Config: mode={config.get('mode')} risk={config.get('risk_per_trade')} "
                f"max_pos={config.get('max_positions')} max_lev={config.get('max_leverage')}")

    adapter = BingXAdapter()
    try:
        await adapter.initialize()
    except BingXError as e:
        logger.error(f"BingX init failed: {e}")
        return

    # Verify balance
    try:
        bal = await adapter.get_wallet_balance()
        b = bal.get("balance", {})
        equity = float(b.get("equity", 0))
        logger.info(f"Account: {b.get('userId')} equity=${equity:.2f}")
        if equity < 1:
            logger.warning("Low balance — observation only, no execution")
    except Exception as e:
        logger.error(f"Balance check failed: {e}")

    state = load_state()
    logger.info(f"Loaded state: {len(state)} symbols tracked")

    cycle = 0
    while True:
        try:
            cycle += 1
            logger.info(f"--- Cycle {cycle} ---")

            # 1. Get positions
            positions = await adapter.get_positions()
            open_pos = [p for p in positions if float(p.get("positionAmt", 0)) > 0]
            logger.info(f"Open positions: {len(open_pos)}")

            for p in open_pos:
                sym = p.get("symbol", "?")
                amt = float(p.get("positionAmt", 0))
                entry = float(p.get("avgPrice", 0))
                upl = float(p.get("unrealizedProfit", 0))
                side = "Buy" if amt > 0 else "Sell"
                logger.info(f"  {sym} {side} amt={amt} entry={entry} pnl={upl}")

            # 2. Update state
            for p in open_pos:
                sym = p.get("symbol", "?")
                if sym not in state:
                    state[sym] = {
                        "first_seen": time.time(),
                        "entry": float(p.get("avgPrice", 0)),
                        "side": "Buy" if float(p.get("positionAmt", 0)) > 0 else "Sell",
                        "mfe_peak": 0.0,
                        "be_fired": False,
                        "partial_fired": False,
                        "tight_fired": False,
                    }
                state[sym]["last_seen"] = time.time()
                state[sym]["current_price"] = float(p.get("markPrice", 0))
                state[sym]["unrealized_pnl"] = float(p.get("unrealizedProfit", 0))

            # 3. Clean closed positions from state
            open_syms = {p.get("symbol") for p in open_pos}
            for sym in list(state.keys()):
                if sym not in open_syms:
                    logger.info(f"  Position closed: {sym}")
                    del state[sym]

            save_state(state)
            logger.info(f"State saved: {len(state)} symbols")

        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())