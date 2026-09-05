"""
MT5 Demo Adapter — ExecutionAdapter для RoboForex Demo через SSH.

Архитектура:
  TradingOS → MT5DemoAdapter → SSH → MT5 Terminal (192.168.1.77)

Использует существующий mt5_connector.py из mt5_trading_bot.
Не требует установки MetaTrader5 на этой машине — работает через SSH.
"""

import asyncio
import json
import logging
import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("MT5DemoAdapter")

# ── Data models ─────────────────────────────────────────────

class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

class PositionStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_MANUAL = "CLOSED_MANUAL"

@dataclass
class PositionState:
    position_id: str
    symbol: str
    side: PositionSide
    entry_price: float
    quantity: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: PositionStatus = PositionStatus.PENDING
    opened_at: Optional[float] = None
    closed_at: Optional[float] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    mfe: float = 0.0
    mae: float = 0.0
    bars_held: int = 0
    signal_metadata: dict = field(default_factory=dict)

@dataclass
class CloseResult:
    position_id: str
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    reason: str
    mfe: float
    mae: float
    bars_held: int


class MT5DemoAdapter:
    """Адаптер для MT5 Demo через SSH.

    Использует SSH-доступ к MT5-терминалу на 192.168.1.77.
    Все ордера отправляются на демо-счёт RoboForex.
    """

    def __init__(self):
        self._host = os.environ.get("MT5_HOST", "192.168.1.77")
        self._user = os.environ.get("MT5_SSH_USER", "user")
        self._password = os.environ.get("MT5_SSH_PASSWORD", "")
        self._positions: dict[str, PositionState] = {}
        self._closed: list[CloseResult] = []
        self._stats = {
            "total_signals": 0, "positions_opened": 0, "positions_closed": 0,
            "wins": 0, "losses": 0, "total_pnl": 0.0,
        }

    # ── SSH helpers ────────────────────────────────────────

    def _ssh(self, command: str) -> str:
        """Execute command on MT5 host via SSH."""
        ssh_cmd = [
            "sshpass", "-p", self._password,
            "ssh", "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            f"{self._user}@{self._host}",
            command,
        ]
        try:
            result = subprocess.run(
                ssh_cmd, capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                logger.warning(f"SSH error: {result.stderr.strip()}")
                return ""
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            logger.error("SSH timeout")
            return ""
        except Exception as e:
            logger.error(f"SSH failed: {e}")
            return ""

    def _ssh_python(self, code: str) -> str:
        """Run Python code on MT5 host via SSH."""
        # Escape for SSH
        escaped = code.replace("'", "'\\''")
        return self._ssh(f"cd /root/mt5_trading_bot && venv/bin/python3 -c '{escaped}'")

    # ── Connection ─────────────────────────────────────────

    async def health(self) -> bool:
        """Check MT5 connection via SSH."""
        result = self._ssh("echo 'alive'")
        return result == "alive"

    async def get_account_info(self) -> Optional[dict]:
        """Get MT5 account info."""
        code = """
import sys
sys.path.insert(0, '.')
from core.mt5_connector import MT5Connector
from core.secrets import load_env
load_env()
conn = MT5Connector({})
if conn.connect():
    info = conn.get_account_info()
    conn.disconnect()
    print(info)
else:
    print('{}')
"""
        result = self._ssh_python(code)
        if result and result != "{}":
            try:
                return eval(result)
            except:
                pass
        return None

    async def get_symbol_info(self, symbol: str) -> Optional[dict]:
        """Get symbol info from MT5."""
        code = f"""
import sys
sys.path.insert(0, '.')
from core.mt5_connector import MT5Connector
from core.secrets import load_env
load_env()
conn = MT5Connector({{}})
if conn.connect():
    info = conn.get_symbol_info('{symbol}')
    conn.disconnect()
    print(info)
else:
    print('{{}}')
"""
        result = self._ssh_python(code)
        if result and result != "{}":
            try:
                return eval(result)
            except:
                pass
        return None

    # ── Position management ────────────────────────────────

    async def open_position(
        self,
        symbol: str,
        side: PositionSide,
        entry_price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        quantity: float = 0.01,
        metadata: Optional[dict] = None,
    ) -> str:
        """Open position on MT5 Demo."""
        pos_id = f"MT5_{symbol}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        mt5_side = "buy" if side == PositionSide.LONG else "sell"

        # Build order command for MT5
        sl_str = f"sl={stop_loss}" if stop_loss else ""
        tp_str = f"tp={take_profit}" if take_profit else ""

        code = f"""
import sys
sys.path.insert(0, '.')
from core.mt5_connector import MT5Connector
from core.secrets import load_env
load_env()
conn = MT5Connector({{}})
if conn.connect():
    import MetaTrader5 as mt5
    request = {{
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": "{symbol}",
        "volume": {quantity},
        "type": mt5.ORDER_TYPE_BUY if "{mt5_side}" == "buy" else mt5.ORDER_TYPE_SELL,
        "price": {entry_price},
        "deviation": 10,
        "magic": 123456,
        "comment": "tradingos_{pos_id[:8]}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }}
    {"request['sl'] = " + str(stop_loss) if stop_loss else ""}
    {"request['tp'] = " + str(take_profit) if take_profit else ""}
    result = mt5.order_send(request)
    conn.disconnect()
    if result and result.retcode == 10009:
        print('FILLED')
    else:
        print(f'FAIL: {{result}}')
else:
    print('CONNECT_FAIL')
"""
        result = self._ssh_python(code)

        now = time.time()
        pos = PositionState(
            position_id=pos_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            stop_loss=stop_loss,
            take_profit=take_profit,
            status=PositionStatus.OPEN if "FILLED" in result else PositionStatus.PENDING,
            opened_at=now,
            signal_metadata=metadata or {},
        )
        self._positions[pos_id] = pos
        self._stats["positions_opened"] += 1

        logger.info(f"[MT5] {'OPEN' if pos.status == PositionStatus.OPEN else 'PENDING'} "
                     f"{side.value} {symbol} @ {entry_price} qty={quantity} "
                     f"result={result[:50]}")
        return pos_id

    async def close_position(self, position_id: str) -> Optional[CloseResult]:
        """Close position on MT5 Demo."""
        pos = self._positions.get(position_id)
        if not pos or pos.status != PositionStatus.OPEN:
            return None

        mt5_side = "sell" if pos.side == PositionSide.LONG else "buy"

        code = f"""
import sys
sys.path.insert(0, '.')
from core.mt5_connector import MT5Connector
from core.secrets import load_env
load_env()
conn = MT5Connector({{}})
if conn.connect():
    import MetaTrader5 as mt5
    positions = mt5.positions_get(symbol="{pos.symbol}")
    for p in positions:
        if abs(p.price_open - {pos.entry_price}) / {pos.entry_price} < 0.001:
            request = {{
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": "{pos.symbol}",
                "volume": p.volume,
                "type": mt5.ORDER_TYPE_SELL if p.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                "position": p.ticket,
                "price": mt5.symbol_info_tick("{pos.symbol}").bid if p.type == mt5.ORDER_TYPE_BUY else mt5.symbol_info_tick("{pos.symbol}").ask,
                "deviation": 10,
                "magic": 123456,
                "comment": "close_tradingos",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": mt5.ORDER_FILLING_IOC,
            }}
            result = mt5.order_send(request)
            if result and result.retcode == 10009:
                print(f'CLOSED price={{result.price}} profit={{result.profit}}')
            else:
                print(f'FAIL: {{result}}')
            break
    conn.disconnect()
else:
    print('CONNECT_FAIL')
"""
        result = self._ssh_python(code)

        exit_price = pos.entry_price
        pnl = 0.0
        if "CLOSED" in result:
            try:
                parts = result.split()
                for p in parts:
                    if p.startswith("price="):
                        exit_price = float(p.split("=")[1])
                    if p.startswith("profit="):
                        pnl = float(p.split("=")[1])
            except:
                pass

        close_result = CloseResult(
            position_id=position_id,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl / (pos.entry_price * pos.quantity) if pos.entry_price * pos.quantity > 0 else 0,
            reason="MANUAL",
            mfe=pos.mfe,
            mae=pos.mae,
            bars_held=pos.bars_held,
        )

        pos.status = PositionStatus.CLOSED_MANUAL
        pos.exit_price = exit_price
        pos.pnl = pnl
        pos.closed_at = time.time()

        self._closed.append(close_result)
        self._stats["positions_closed"] += 1
        self._stats["total_pnl"] += pnl
        if pnl > 0:
            self._stats["wins"] += 1
        else:
            self._stats["losses"] += 1

        logger.info(f"[MT5] CLOSE {pos.side.value} {pos.symbol} "
                     f"entry={pos.entry_price} exit={exit_price} pnl={pnl:.2f}")
        return close_result

    async def get_positions(self) -> list[PositionState]:
        """Get open positions from MT5."""
        code = """
import sys
sys.path.insert(0, '.')
from core.mt5_connector import MT5Connector
from core.secrets import load_env
load_env()
conn = MT5Connector({})
if conn.connect():
    positions = conn.get_positions()
    conn.disconnect()
    for p in positions:
        print(f'{p.ticket},{p.symbol},{p.type},{p.price_open},{p.volume},{p.profit},{p.sl},{p.tp}')
else:
    print('CONNECT_FAIL')
"""
        result = self._ssh_python(code)
        if not result or result == "CONNECT_FAIL":
            return []

        mt5_positions = []
        for line in result.split("\n"):
            parts = line.split(",")
            if len(parts) >= 7:
                try:
                    mt5_positions.append(PositionState(
                        position_id=f"MT5_{parts[1]}_{parts[0]}",
                        symbol=parts[1],
                        side=PositionSide.LONG if parts[2] == "0" else PositionSide.SHORT,
                        entry_price=float(parts[3]),
                        quantity=float(parts[4]),
                        stop_loss=float(parts[6]) if parts[6] != "0.0" else None,
                        take_profit=float(parts[7]) if len(parts) > 7 and parts[7] != "0.0" else None,
                        status=PositionStatus.OPEN,
                    ))
                except:
                    pass
        return mt5_positions

    async def sync_state(self) -> dict:
        """Sync internal state with MT5."""
        mt5_positions = await self.get_positions()
        return {
            "mt5_open": len(mt5_positions),
            "internal_open": sum(1 for p in self._positions.values() if p.status == PositionStatus.OPEN),
            "closed_count": len(self._closed),
            "stats": dict(self._stats),
        }

    def get_stats(self) -> dict:
        s = self._stats
        total_closed = s["positions_closed"]
        return {
            "total_signals": s["total_signals"],
            "positions_opened": s["positions_opened"],
            "positions_closed": s["positions_closed"],
            "wins": s["wins"],
            "losses": s["losses"],
            "win_rate": round(s["wins"] / total_closed, 4) if total_closed else 0,
            "total_pnl": round(s["total_pnl"], 2),
        }
