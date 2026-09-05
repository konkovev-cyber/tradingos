#!/usr/bin/env python3
"""
AutoSafeExecutor — автоматическое исполнение безопасных действий PIE.

Разрешено:
  ✅ MOVE_SL_BE — перенос стопа в безубыток
  ❌ TAKE_PARTIAL — пока не включено
  ❌ EXIT_NOW — только ручное
  ❌ ADD/усреднение — запрещено

Условия для MOVE_SL_BE:
  - PnL > 1%
  - MFE > 1.5%
  - retracement < 20%
  - Health > 80
  - Защита: проверяем что новый SL выше входа + комиссия
"""

import os
import sys
import json
import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from dataclasses import dataclass

logger = logging.getLogger("AutoSafeExecutor")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(Path("/root/mt5_trading_bot/.env"))
from adapters.bingx_client import BingXClient

# PIE
import importlib.util
_pie_spec = importlib.util.spec_from_file_location(
    "position_intelligence", str(ROOT / "core" / "intelligence" / "position_intelligence.py")
)
_pie_mod = importlib.util.module_from_spec(_pie_spec)
_pie_spec.loader.exec_module(_pie_mod)
PIEPositionIntelligence = _pie_mod.PIEPositionIntelligence


@dataclass
class AutoSafeAction:
    """Запись выполненного действия."""
    timestamp: float
    symbol: str
    action: str  # MOVE_SL_BE
    entry_price: float
    old_sl: Optional[float]
    new_sl: float
    pnl_at_action: float
    reason: str


class AutoSafeExecutor:
    """
    Исполняет безопасные действия PIE на BingX.
    Только MOVE_SL_BE при выполнении строгих критериев.

    Лестница защиты:
      Level 1 (Early):   PnL > 0.8%  +  MFE > 1.2%  +  Health > 75
      Level 2 (Confirm): PnL > 1%    +  MFE > 1.5%  +  retrace < 20%  +  Health > 80

    Level 1 ловит раннюю прибыль.
    Level 2 подтверждает сильное движение.
    """

    # Level 1 — ранняя защита (BE)
    L1_PNL = 0.008     # 0.8%
    L1_MFE = 0.012     # 1.2%
    L1_HEALTH = 75

    # Level 2 — подтверждённая защита (BE + небольшой фикс)
    L2_PNL = 0.01      # 1%
    L2_MFE = 0.015     # 1.5%
    L2_RETRACE = 0.20  # 20%
    L2_HEALTH = 80

    # Защита: буфер над ценой входа (комиссия + спред)
    SL_BUFFER = 0.003    # 0.3%

    def __init__(self, client: BingXClient):
        self._client = client
        self._log_path = ROOT / "auto_safe_log.jsonl"
        self._actions: List[AutoSafeAction] = []
        self._executed_ids: set = set()  # избежать повторного исполнения
        self._position_level: Dict[str, int] = {}  # track current protection level

    def _check_level_2(self, pnl: float, mfe: float, retrace: float, health: float) -> bool:
        """Level 2 — подтверждённая защита."""
        return (pnl >= self.L2_PNL and mfe >= self.L2_MFE
                and retrace <= self.L2_RETRACE and health >= self.L2_HEALTH)

    def _check_level_1(self, pnl: float, mfe: float, health: float) -> bool:
        """Level 1 — ранняя защита."""
        return pnl >= self.L1_PNL and mfe >= self.L1_MFE and health >= self.L1_HEALTH

    async def evaluate_and_execute(self, position: Dict[str, Any], pie_snapshot: Any) -> Optional[AutoSafeAction]:
        """
        Оценить позицию и исполнить MOVE_SL_BE если условия выполнены.
        """
        if pie_snapshot is None:
            return None

        symbol = position.get("symbol", "")
        position_id = f"LIVE_{symbol}_{int(float(position.get('avgPrice', 0)))}"

        # Проверяем не выполняли ли уже для этой позиции
        if position_id in self._executed_ids:
            return None

        pnl = pie_snapshot.pnl_pct
        mfe = pie_snapshot.max_profit_seen
        retrace = pie_snapshot.profit_retracement
        health = pie_snapshot.health_score

        # Level 2 требует подтверждения PIE
        if pie_snapshot.recommendation.value in ("MOVE_SL_BE", "TAKE_PARTIAL"):
            if self._check_level_2(pnl, mfe, retrace, health):
                return await self._execute_move_sl(position, pnl, mfe, health, level=2)

        # Level 1 — только числовые критерии, без ожидания PIE
        if self._check_level_1(pnl, mfe, health):
            return await self._execute_move_sl(position, pnl, mfe, health, level=1)

        # Логируем только если близко к уровню 1
        if pnl > 0.005 and mfe > 0.008:
            logger.info(f"[AUTO SAFE] {symbol}: pnl={pnl*100:.1f}% mfe={mfe*100:.1f}% — below thresholds")
        return None

    async def _execute_move_sl(self, position: Dict[str, Any], pnl: float,
                                mfe: float, health: float, level: int) -> Optional[AutoSafeAction]:
        """Executar MOVE_SL_BE na exchange."""
        symbol = position.get("symbol", "")
        position_id = f"LIVE_{symbol}_{int(float(position.get('avgPrice', 0)))}"
        side = position.get("positionSide", "LONG")
        entry_price = float(position.get("avgPrice", 0))
        position_amt = float(position.get("positionAmt", 0))

        # F4 hardening: positionSide обязан быть LONG/SHORT явно. MISSING/UNKNOWN
        # → skip (fail-safe), никогда не угадываем LONG по дефолту.
        if side not in ("LONG", "SHORT"):
            logger.warning(f"[AUTO SAFE] {symbol}: invalid positionSide {side!r} — skip")
            return None

        if entry_price == 0 or position_amt == 0:
            logger.warning(f"[AUTO SAFE] {symbol}: invalid entry or amount")
            return None

        if side == "LONG":
            # SL чуть выше входа: если цена упадёт ниже, выходим с прибылью
            new_sl = round(entry_price * (1 + self.SL_BUFFER), 4)
            stop_price = round(entry_price * (1 - self.SL_BUFFER * 0.5), 4)
            close_side = "SELL"
        else:  # SHORT
            # SL чуть ниже входа: если цена вырастет выше, выходим с прибылью
            new_sl = round(entry_price * (1 - self.SL_BUFFER), 4)
            stop_price = round(entry_price * (1 + self.SL_BUFFER * 0.5), 4)
            close_side = "BUY"

        level_name = f"L{level}"
        action_reason = f"{level_name}: pnl={pnl*100:.1f}% mfe={mfe*100:.1f}% h={health:.0f}"

        logger.info(
            f"[AUTO SAFE] ⚡ Level {level} MOVE_SL_BE for {symbol}\n"
            f"  Entry: {entry_price:.4f} → SL: {new_sl:.4f}\n"
            f"  PnL: {pnl*100:+.2f}%  MFE: {mfe*100:+.2f}%  Health: {health:.0f}\n"
            f"  Reason: {action_reason}"
        )

        close_amt = abs(position_amt)

        result = await self._client.place_stop_loss(
            symbol=symbol,
            side=close_side,
            quantity=str(close_amt),
            stop_price=str(stop_price),
        )

        if result.get("code") != 0:
            logger.error(f"[AUTO SAFE] ❌ Failed to set SL for {symbol}: {result}")
            return None

        order_id = result.get("data", {}).get("orderId", "unknown")
        logger.info(f"[AUTO SAFE] ✅ Level {level} MOVE_SL_BE for {symbol} → {new_sl:.4f} (order: {order_id})")

        action = AutoSafeAction(
            timestamp=time.time(),
            symbol=symbol,
            action=f"MOVE_SL_BE_L{level}",
            entry_price=entry_price,
            old_sl=None,
            new_sl=new_sl,
            pnl_at_action=pnl,
            reason=action_reason,
        )
        self._actions.append(action)
        self._executed_ids.add(position_id)
        self._position_level[position_id] = level
        self._log_action(action)

        return action

    def _log_action(self, action: AutoSafeAction):
        """Записать действие в JSONL лог."""
        try:
            with open(self._log_path, "a") as f:
                f.write(json.dumps({
                    "timestamp": action.timestamp,
                    "symbol": action.symbol,
                    "action": action.action,
                    "entry_price": action.entry_price,
                    "new_sl": action.new_sl,
                    "pnl_at_action": action.pnl_at_action,
                    "reason": action.reason,
                    "iso_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(action.timestamp)),
                }) + "\n")
        except Exception as e:
            logger.error(f"Failed to log action: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Статистика выполненных действий."""
        return {
            "total": len(self._actions),
            "move_sl_be": sum(1 for a in self._actions if a.action == "MOVE_SL_BE"),
            "last": [
                {
                    "symbol": a.symbol,
                    "action": a.action,
                    "new_sl": a.new_sl,
                    "pnl": a.pnl_at_action,
                    "time": time.strftime("%m-%d %H:%M", time.gmtime(a.timestamp)),
                }
                for a in self._actions[-5:]
            ],
        }
