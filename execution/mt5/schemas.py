"""
execution/mt5/schemas.py
Unified data schemas for MT5 adapter.
Maps MT5 terminal data to TradingOS Position format.
"""
from dataclasses import dataclass
from typing import Optional, List

@dataclass
class MT5Account:
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    free_margin: float = 0.0
    server_time: str = ""
    connected: bool = False

@dataclass
class MT5Position:
    ticket: int = 0
    symbol: str = ""
    side: str = ""  # BUY / SELL
    volume: float = 0.0
    open_price: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None
    profit: float = 0.0
    swap: float = 0.0
    comment: str = ""

@dataclass
class MT5Order:
    ticket: int = 0
    symbol: str = ""
    side: str = ""
    type: str = ""  # MARKET, LIMIT, STOP
    volume: float = 0.0
    price: float = 0.0
    sl: Optional[float] = None
    tp: Optional[float] = None
    status: str = ""

@dataclass  
class MT5Snapshot:
    account: MT5Account = None
    positions: List[MT5Position] = None
    orders: List[MT5Order] = None
    heartbeat: str = ""

def protection_status(sl: Optional[float], tp: Optional[float]) -> str:
    if sl and tp: return "FULLY_PROTECTED"
    if sl: return "SL_ONLY"
    if tp: return "TP_ONLY"
    return "UNPROTECTED"

def to_tradingos_position(mt5_pos: MT5Position) -> dict:
    return {
        "symbol": mt5_pos.symbol,
        "side": "LONG" if mt5_pos.side == "BUY" else "SHORT",
        "qty": mt5_pos.volume,
        "entry_price": mt5_pos.open_price,
        "stop_loss": mt5_pos.sl,
        "take_profit": mt5_pos.tp,
        "unrealized_pnl": mt5_pos.profit,
        "source": "MT5",
        "position_id": str(mt5_pos.ticket),
    }
