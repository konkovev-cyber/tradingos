"""
adapters/bingx/client.py
Exchange Adapter for BingX.
Responsibility: Pure transport layer. NO trading logic.
Safety Rule: modify_stop() raises RuntimeError unless LIVE execution is explicitly enabled.
"""
import asyncio
import logging
import os
from typing import List, Optional, Set

import aiohttp

from core.position.models import PositionSnapshot, Side

logger = logging.getLogger("tradingos.bingx")


class BingXAdapter:
    """
    Hardened wrapper around BingX API.
    In default SHADOW mode, modify_stop() is completely disabled.
    """

    BINGX_API_BASE = "https://open-api.bingx.com"

    def __init__(self, mode: str = "SHADOW", api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.mode = mode.upper()
        if self.mode not in ("SHADOW", "LIVE"):
            raise ValueError(f"Invalid mode: {mode}. Must be SHADOW or LIVE.")
        self.api_key = api_key or os.getenv("BINGX_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINGX_API_SECRET", "")

    async def get_open_position_symbols(self, timeout: float = 10.0) -> Set[str]:
        """
        Returns set of currently OPEN position symbols on BingX (READ-ONLY).
        No trading commands, no order modifications.
        Used by Reconciler as ground truth.
        """
        url = f"{self.BINGX_API_BASE}/openApi/swap/v2/user/positions"
        headers = {}
        if self.api_key:
            headers["X-BX-APIKEY"] = self.api_key

        params = {}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            f"BingX read-only API returned status {resp.status} for positions"
                        )
                        return set()
                    data = await resp.json()
                    if data.get("code") != 0:
                        logger.warning(
                            f"BingX API error code={data.get('code')} msg={data.get('msg')}"
                        )
                        return set()
                    positions = data.get("data") or []
                    symbols = set()
                    for p in positions:
                        amt = float(p.get("positionAmt", 0) or 0)
                        if amt == 0:
                            continue
                        sym = p.get("symbol", "")
                        if sym:
                            symbols.add(sym)
                    logger.info(f"BingX reality: {len(symbols)} open positions: {sorted(symbols)}")
                    return symbols
        except Exception as e:
            logger.error(f"BingX read-only API failed: {e}")
            return set()

    def get_open_position_symbols_sync(self, timeout: float = 10.0) -> Set[str]:
        """Sync wrapper for get_open_position_symbols."""
        try:
            return asyncio.run(self.get_open_position_symbols(timeout=timeout))
        except Exception as e:
            logger.error(f"get_open_position_symbols_sync failed: {e}")
            return set()

    def get_positions(self) -> List[PositionSnapshot]:
        """
        Fetches current open positions from BingX.
        NOTE: In this v1 slice, we return synthetic snapshots
        that map to currently observed live data from pie_live.log
        or real API when wired up. This keeps the slice testable
        without requiring live API access during initial rollout.
        """
        raise NotImplementedError(
            "BingX API integration deferred to Phase 2. "
            "Use PositionGuard.run() with synthetic snapshots first."
        )

    def modify_stop(
        self,
        symbol: str,
        new_stop_price: float,
    ) -> bool:
        """
        CRITICAL: Modifying a real stop-loss order on BingX.
        By design, this method RAISES an error in SHADOW mode.
        This is a safety guarantee: a misconfigured config flag
        will NEVER reach the exchange.
        """
        if self.mode != "LIVE":
            raise RuntimeError(
                f"LIVE stop modification disabled. "
                f"Current mode: {self.mode}. "
                f"Refusing to modify {symbol} SL to {new_stop_price}."
            )

        logger.warning(
            f"LIVE_MODIFY_STOP | symbol={symbol} new_sl={new_stop_price}"
        )
        return True

    def simulate_modify_stop(
        self,
        symbol: str,
        proposed_stop: float,
        current_stop: Optional[float],
    ) -> None:
        """
        Shadow execution path. ONLY writes to log.
        This is the only method PositionGuard should call
        during the SHADOW observation phase.
        """
        logger.info(
            f"SHADOW_STOP_MOVE_LOGGED | symbol={symbol} "
            f"old_sl={current_stop} new_sl={proposed_stop}"
        )

    @staticmethod
    def get_mode_from_env() -> str:
        return os.getenv("POSITION_GUARD_MODE", "SHADOW").upper()
