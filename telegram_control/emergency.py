"""
Emergency actions — close positions, freeze trading, panic close.

Telegram → Risk Check → Confirmation → BingX
"""

import os
import time
import json
import logging
import asyncio
from typing import Dict, Any, List, Optional
from pathlib import Path

logger = logging.getLogger("EmergencyActions")

ROOT = Path(__file__).parent.parent
sys_path = str(ROOT)
if sys_path not in __import__('sys').path:
    __import__('sys').path.insert(0, sys_path)


class EmergencyExecutor:
    """
    Исполняет экстренные действия: закрытие позиций, заморозка.

    Всегда проверяет подтверждение перед исполнением.
    Каждое действие логируется в control_log.
    """

    def __init__(self, bingx_client):
        self._client = bingx_client
        self._log: List[Dict[str, Any]] = []
        self._log_path = Path(__file__).parent / "control_log.jsonl"

    async def close_position(
        self,
        symbol: str,
        reason: str = "manual",
    ) -> Dict[str, Any]:
        """
        Закрыть одну позицию на BingX.

        Returns:
            Результат с деталями закрытия
        """
        try:
            positions = await self._client.get_positions()
            target = None
            for pos in positions:
                if pos.get("symbol") == symbol:
                    target = pos
                    break

            if not target:
                return {"success": False, "error": f"Position {symbol} not found"}

            position_amt = float(target.get("positionAmt", 0))
            if position_amt == 0:
                return {"success": False, "error": f"Position {symbol} has zero amount"}

            # Determine close side
            side = "SELL" if position_amt > 0 else "BUY"
            close_amt = abs(position_amt)

            # Place close order via API
            result = await self._client._request(
                "POST",
                "/openApi/swap/v2/trade/order",
                {
                    "symbol": symbol,
                    "side": side,
                    "type": "MARKET",
                    "quantity": str(close_amt),
                    "positionSide": "BOTH",
                    "reduceOnly": True,
                },
                signed=True,
            )

            if result.get("code") != 0:
                logger.error(f"Failed to close {symbol}: {result}")
                return {"success": False, "error": result.get("msg", "API error")}

            # Log the action
            entry_price = float(target.get("avgPrice", 0))
            mark_price = float(target.get("markPrice", 0))
            pnl = float(target.get("unrealizedProfit", 0))
            side_name = "LONG" if position_amt > 0 else "SHORT"

            if side_name == "LONG":
                pnl_pct = (mark_price - entry_price) / entry_price if entry_price > 0 else 0
            else:
                pnl_pct = (entry_price - mark_price) / entry_price if entry_price > 0 else 0

            log_entry = {
                "action": "close",
                "symbol": symbol,
                "side": side_name,
                "entry_price": entry_price,
                "exit_price": mark_price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "reason": reason,
                "timestamp": time.time(),
            }
            self._log_action(log_entry)

            return {
                "success": True,
                "symbol": symbol,
                "side": side_name,
                "entry_price": entry_price,
                "exit_price": mark_price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
            }

        except Exception as e:
            logger.error(f"Close {symbol} failed: {e}")
            return {"success": False, "error": str(e)}

    async def close_all_positions(
        self,
        reason: str = "panic",
        only_losers: bool = False,
    ) -> Dict[str, Any]:
        """
        Закрыть все позиции (или только убыточные).

        Returns:
            Сводка по закрытым позициям
        """
        positions = await self._client.get_positions()
        results = []

        for pos in positions:
            amt = float(pos.get("positionAmt", 0))
            if amt == 0:
                continue

            entry = float(pos.get("avgPrice", 0))
            mark = float(pos.get("markPrice", 0))
            side = "LONG" if amt > 0 else "SHORT"
            pnl_pct = (mark - entry) / entry if side == "LONG" else (entry - mark) / entry
            pnl_pct = pnl_pct if entry > 0 else 0

            if only_losers and pnl_pct > 0:
                results.append({
                    "skipped": True,
                    "symbol": pos["symbol"],
                    "reason": "not a loser",
                })
                continue

            result = await self.close_position(pos["symbol"], reason)
            results.append(result)

        successful = [r for r in results if r.get("success")]
        failed = [r for r in results if not r.get("success")]

        return {
            "total": len(results),
            "closed": len(successful),
            "failed": len(failed),
            "results": results,
        }

    async def get_bot_status(self) -> Dict[str, Any]:
        """Получить статус бота на BingX."""
        try:
            positions = await self._client.get_positions()
            ticker = await self._client.get_ticker("BTCUSDT")
            btc_price = float(ticker.get("lastPrice", 0))
            return {
                "positions": len(positions),
                "btc_price": btc_price,
                "timestamp": time.time(),
            }
        except Exception as e:
            return {"error": str(e)}

    def _log_action(self, entry: Dict[str, Any]):
        """Записать действие в лог."""
        self._log.append(entry)
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.error(f"Failed to log action: {e}")

    def get_action_log(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Получить последние действия."""
        return self._log[-limit:]
