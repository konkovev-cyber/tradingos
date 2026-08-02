"""
telegram_notifier.py — Telegram notifications for Guardian events.

Hooks into reality_guardian.py to send trade cards and events.

Importable from reality_guardian.py without breaking existing logic.
"""

from __future__ import annotations
import asyncio
import logging
import os
from typing import Optional, Dict, Any

log = logging.getLogger("Guardian.Telegram")

# Global Notifier instance
_notifier: Optional[Any] = None
_exchange: Optional[Any] = None


def _load_cred_check() -> bool:
    """Check if Telegram credentials are available."""
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN")) and bool(os.environ.get("TELEGRAM_CHAT_ID"))


async def init_telegram(exchange: Optional[Any] = None) -> bool:
    """Initialize Telegram notifier. Returns True if successful."""
    global _notifier, _exchange

    if not _load_cred_check():
        log.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — Telegram notifications disabled")
        return False

    from tradingos.notifier.notifier import Notifier

    _notifier = Notifier(
        token=os.environ["TELEGRAM_BOT_TOKEN"],
        chat_id=os.environ["TELEGRAM_CHAT_ID"],
    )
    _exchange = exchange
    await _notifier.start()
    log.info("Telegram notifier initialized")
    return True


async def shutdown_telegram():
    """Shutdown Telegram notifier."""
    global _notifier
    if _notifier:
        await _notifier.stop()
        _notifier = None


def is_enabled() -> bool:
    """Check if notifier is active."""
    return _notifier is not None


async def send_trade_open(symbol: str, side: str, entry_price: float,
                         qty: float, sl: Optional[float] = None,
                         tp: Optional[float] = None,
                         reason: str = "",
                         leverage: int = 1,
                         entry_time: float = 0) -> bool:
    """Send trade open card to Telegram."""
    if not _notifier:
        return False
    try:
        from datetime import datetime, timezone
        et = datetime.fromtimestamp(entry_time, tz=timezone.utc) if entry_time > 0 else None
        await _notifier.notify_trade_open(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            qty=qty,
            sl=sl,
            tp=tp,
            reason=reason,
            leverage=leverage,
            entry_time=et,
            exchange=_exchange,
        )
        return True
    except Exception as e:
        log.error(f"send_trade_open failed: {e}")
        return False


async def send_trade_close(symbol: str, side: str, entry_price: float,
                          exit_price: float, qty: float,
                          pnl: float, fees: float = 0.0,
                          holding_hours: float = 0.0,
                          reason: str = "",
                          sl: float = 0.0, tp: float = 0.0,
                          mfe_r: float = 0.0, mae_r: float = 0.0,
                          entry_time: float = 0, exit_time: float = 0) -> bool:
    """Send trade close card to Telegram."""
    if not _notifier:
        return False
    try:
        from datetime import datetime, timezone
        et = datetime.fromtimestamp(entry_time, tz=timezone.utc) if entry_time > 0 else None
        xt = datetime.fromtimestamp(exit_time, tz=timezone.utc) if exit_time > 0 else None
        await _notifier.notify_trade_close(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            qty=qty,
            pnl=pnl,
            fees=fees,
            holding_hours=holding_hours,
            reason=reason,
            sl=sl,
            tp=tp,
            mfe_r=mfe_r,
            mae_r=mae_r,
            entry_time=et,
            exit_time=xt,
            exchange=_exchange,
        )
        return True
    except Exception as e:
        log.error(f"send_trade_close failed: {e}")
        return False


async def send_guardian_event(symbol: str, event_type: str,
                             current_sl: float, entry_price: float,
                             peak_r: float) -> bool:
    """Send Guardian event (BE/Partial/Tight) to Telegram."""
    if not _notifier:
        return False
    try:
        await _notifier.notify_guardian_event(
            symbol=symbol,
            event_type=event_type,
            current_sl=current_sl,
            entry_price=entry_price,
            peak_r=peak_r,
        )
        return True
    except Exception as e:
        log.error(f"send_guardian_event failed: {e}")
        return False


async def send_error(source: str, error: str) -> bool:
    """Send error notification to Telegram."""
    if not _notifier:
        return False
    try:
        await _notifier.notify_error(source, error)
        return True
    except Exception as e:
        log.error(f"send_error failed: {e}")
        return False


async def send_timeout_alert(symbol: str, hours: float) -> bool:
    """Send timeout alert to Telegram."""
    if not _notifier:
        return False
    try:
        text = (
            f"⏰ <b>{symbol}</b> TIMEOUT\n"
            f"Удержание: <code>{hours:.1f}h</code> > {24}h\n"
            f"Позиция не закрыта SL/TP. Требует внимания."
        )
        await _notifier.send(text)
        return True
    except Exception as e:
        log.error(f"send_timeout_alert failed: {e}")
        return False


async def send_daily_report(report_text: str) -> bool:
    """Send daily report to Telegram."""
    if not _notifier:
        return False
    try:
        await _notifier.notify_daily_report(report_text)
        return True
    except Exception as e:
        log.error(f"send_daily_report failed: {e}")
        return False
