"""
Notifier — единый async-интерфейс для отправки уведомлений из TradingOS.

Использует Bot API напрямую через aiohttp. Опционально поддерживает SOCKS proxy.

Методы:
  - send_text(text, chat_id, **kwargs)         — обычное сообщение
  - send_photo(photo, caption, chat_id)         — фото
  - notify_trade_open(...), notify_trade_close(...)
  - notify_error(...), notify_daily_report(...)
  - notify_guardian_event(...)

Дизайн:
  - Очередь + rate limiter (10 msg/sec)
  - Backoff на ошибки
  - Опциональный SOCKS proxy (env TELEGRAM_PROXY)
"""

from __future__ import annotations
import asyncio
import logging
import os
import time
from typing import Optional, Dict, Any

import aiohttp

try:
    from aiohttp_socks import ProxyConnector
    SOCKS_AVAILABLE = True
except ImportError:
    SOCKS_AVAILABLE = False

log = logging.getLogger("TradingOS.Notifier")



def qty_is_valid(entry_price):
    return entry_price is not None and entry_price > 0


def _map_tf_to_interval(tf):
    """Map timeframe string (e.g. '5m', '15min', '1h') to Bybit interval value."""
    if not tf:
        return "15"
    tf = str(tf).lower().strip()
    if "1" in tf and "h" in tf:
        return "60"
    if "5" in tf:
        return "5"
    if "30" in tf:
        return "30"
    return "15"


class Notifier:
    """Async уведомления в Telegram.

    Использует Bot API напрямую через aiohttp.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        proxy_url: Optional[str] = None,
        rate_limit: float = 0.1,  # 10 msg/sec
    ):
        self.token = token
        self.chat_id = chat_id
        self.proxy_url = proxy_url or os.environ.get("TELEGRAM_PROXY")
        self.rate_limit = rate_limit
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_send = 0.0
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task] = None
        self._running = False
        # Circuit breaker
        self._cb_fail = 0
        self._cb_block_until = 0.0

    async def start(self):
        """Запустить worker."""
        if self._running:
            return
        await self._init_session()
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        log.info("Notifier started")

    async def stop(self):
        """Остановить worker."""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        if self._session:
            await self._session.close()
            self._session = None
        log.info("Notifier stopped")

    async def _init_session(self):
        if self._session:
            return
        if self.proxy_url and SOCKS_AVAILABLE:
            connector = ProxyConnector.from_url(self.proxy_url)
            self._session = aiohttp.ClientSession(connector=connector)
        else:
            self._session = aiohttp.ClientSession()

    async def _worker(self):
        """Worker для отправки из очереди."""
        while self._running:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            try:
                await self._dispatch(item)
            except Exception as e:
                log.error(f"Notifier dispatch error: {e}")
            await asyncio.sleep(self.rate_limit)

    async def _dispatch(self, item: Dict[str, Any]):
        """Отправить одно сообщение."""
        kind = item.get("kind", "text")
        if kind == "text":
            await self._send_message(item["text"], **item.get("kwargs", {}))
        elif kind == "photo":
            await self._send_photo(item["photo"], item.get("caption"), **item.get("kwargs", {}))

    async def _send_message(self, text: str, chat_id: Optional[str] = None, **kwargs) -> bool:
        """Отправить текстовое сообщение."""
        if time.time() < self._cb_block_until:
            return False
        if not self._session:
            await self._init_session()
        chat = chat_id or self.chat_id
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": chat,
            "text": text,
            "parse_mode": "HTML",
        }
        payload.update(kwargs)
        try:
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    self._cb_fail = 0
                    return True
                else:
                    body = await resp.text()
                    log.warning(f"Telegram send failed ({resp.status}): {body[:200]}")
                    self._cb_fail += 1
                    if self._cb_fail >= 5:
                        self._cb_block_until = time.time() + 60
                        log.error("Notifier circuit breaker open 60s")
                    return False
        except Exception as e:
            log.error(f"Telegram send exception: {e}")
            self._cb_fail += 1
            return False

    async def _send_photo(self, photo: bytes, caption: Optional[str] = None, chat_id: Optional[str] = None) -> bool:
        """Отправить фото."""
        if time.time() < self._cb_block_until:
            return False
        if not self._session:
            await self._init_session()
        chat = chat_id or self.chat_id
        url = f"https://api.telegram.org/bot{self.token}/sendPhoto"
        form = aiohttp.FormData()
        form.add_field("chat_id", chat)
        if caption:
            form.add_field("caption", caption[:1024])
            form.add_field("parse_mode", "HTML")
        form.add_field("photo", photo, filename="chart.png", content_type="image/png")
        try:
            async with self._session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    self._cb_fail = 0
                    return True
                else:
                    body = await resp.text()
                    log.warning(f"Telegram photo failed ({resp.status}): {body[:200]}")
                    return False
        except Exception as e:
            log.error(f"Telegram photo exception: {e}")
            return False

    # ============== Public API ==============

    async def send(self, text: str, chat_id: Optional[str] = None, **kwargs):
        """Поставить в очередь и отправить. Async-safe."""
        await self._queue.put({
            "kind": "text",
            "text": text,
            "kwargs": {"chat_id": chat_id, **kwargs} if chat_id else kwargs,
        })

    async def send_photo(self, photo: bytes, caption: Optional[str] = None, chat_id: Optional[str] = None):
        """Поставить фото в очередь."""
        await self._queue.put({
            "kind": "photo",
            "photo": photo,
            "caption": caption,
            "kwargs": {"chat_id": chat_id} if chat_id else {},
        })

    # ============== Helpers for TradingOS events ==============

    def _fmt_price(self, p):
        """Format price: 2 decimals if >1000, 4 if <1, 5 if <0.01."""
        if p is None or p == 0:
            return "—"
        if p >= 1000:
            return f"{p:.2f}"
        if p >= 1:
            return f"{p:.4f}"
        return f"{p:.5f}"

    def _format_open_text(self, symbol, side, entry_price, sl, tp,
                           leverage, score, reason, qty):
        """v10: rich Russian HTML with bold, emojis, explanations."""
        is_long = side.upper() in ("BUY", "LONG")
        direction_arrow = "🟢" if is_long else "🔴"
        direction_text = "LONG (покупка)" if is_long else "SHORT (продажа)"
        ep = self._fmt_price(entry_price) if entry_price else "—"
        sl_text = self._fmt_price(sl) if sl else "—"
        tp_text = self._fmt_price(tp) if tp else "—"

        lines = [
            f"{direction_arrow} <b>ОТКРЫТА ПОЗИЦИЯ</b>",
            f"<b>{symbol}</b> — {direction_text}",
            "",
            f"💵 <b>Вход:</b> <code>{ep}</code>",
            f"🛑 <b>Стоп-лосс:</b> <code>{sl_text}</code>",
            f"🎯 <b>Тейк-профит:</b> <code>{tp_text}</code>",
        ]
        if qty and qty > 0:
            lines.append(f"📦 <b>Объём:</b> {qty}")
        if leverage and leverage > 1:
            lines.append(f"⚡ <b>Плечо:</b> {leverage}×")
        if reason:
            lines.append(f"📝 <b>Причина:</b> {reason}")
        return "\n".join(lines)

    def _format_close_text(self, symbol, side, entry_price, exit_price,
                            pnl, fees, holding_seconds, reason, trade_id,
                            leverage, score, sl, tp, mfe_r, mae_r, balance, qty=0):
        """v20: clean Russian HTML with correct margin-based pnl_pct."""
        is_long = side.upper() in ("BUY", "LONG")
        direction_text = "LONG (покупка)" if is_long else "SHORT (продажа)"
        is_profit = (pnl is not None and pnl > 0)
        emoji = "✅" if is_profit else "❌"
        result_word = "ПРИБЫЛЬ" if is_profit else "УБЫТОК"

        reason_map = {
            "TP": "🎯 Take Profit",
            "SL": "🛑 Stop Loss",
            "BE": "⚖️ Безубыток",
            "GUARDIAN_BE": "🛡️ Guardian Lock",
            "GUARDIAN_PARTIAL": "🔒 Guardian Partial",
            "MANUAL": "👤 Manual",
            "TIMEOUT": "⏰ Timeout",
        }
        reason_text = reason_map.get(reason or "", reason or "—")
        pnl_abs = abs(pnl) if pnl is not None else 0.0
        pnl_pct = 0.0
        # FIX: pnl_pct = price move % × leverage (matches chart and user expectation)
        # For LONG: ((exit/entry) - 1) × 100 × leverage
        # For SHORT: ((entry/exit) - 1) × 100 × leverage
        if pnl is not None and entry_price and entry_price > 0 and exit_price and exit_price > 0 and leverage and leverage > 0:
            if is_long:
                pnl_pct = ((exit_price / entry_price) - 1.0) * 100 * leverage
            else:
                pnl_pct = ((entry_price / exit_price) - 1.0) * 100 * leverage
            # Ensure sign matches PnL direction
            if (pnl > 0 and pnl_pct < 0) or (pnl < 0 and pnl_pct > 0):
                pnl_pct = -pnl_pct
        sign = "+" if is_profit else "-"
        ep = self._fmt_price(entry_price) if entry_price else "—"
        xp = self._fmt_price(exit_price) if exit_price else "—"

        lines = [
            f"{emoji} <b>ПОЗИЦИЯ ЗАКРЫТА — {result_word}</b>",
            f"<b>{symbol}</b> — {direction_text}",
            f"📌 {reason_text}",
            "",
            f"💵 <b>Вход:</b> <code>{ep}</code>",
            f"💵 <b>Выход:</b> <code>{xp}</code>",
        ]
        if pnl is not None:
            lines.append(f"💰 <b>Результат:</b> <code>${sign}{pnl_abs:.2f} ({pnl_pct:+.2f}%)</code>")
        # R:R line — based on actual SL distance
        if sl and sl > 0 and abs(entry_price - sl) > 0:
            r_dist = abs(entry_price - sl)
            if is_long:
                r_mult = (exit_price - entry_price) / r_dist
            else:
                r_mult = (entry_price - exit_price) / r_dist
            lines.append(f"📊 <b>R-multiple:</b> <code>{r_mult:+.2f}R (R={r_dist:.5f})</code>")
        if mfe_r is not None:
            # Reject absurd mfe_r values
            if abs(mfe_r) > 10:
                log.warning(f"rejected mfe_r={mfe_r} in text too")
            else:
                # Use max of mfe_r and exit-based R (chart uses candle data)
                if sl and sl > 0 and abs(entry_price - sl) > 0:
                    r_dist = abs(entry_price - sl)
                    exit_r = (exit_price - entry_price) / r_dist if is_long else (entry_price - exit_price) / r_dist
                    display_r = max(mfe_r, exit_r) if mfe_r > 0 else mfe_r
                else:
                    display_r = mfe_r
                r_text = f"+{display_r:.2f}R" if display_r >= 0 else f"{display_r:.2f}R"
                lines.append(f"📈 <b>Максимум:</b> <code>{r_text}</code>")
        if mae_r is not None:
            if abs(mae_r) > 10:
                log.warning(f"rejected mae_r={mae_r} in text too")
            else:
                # Use min of mae_r and exit-based R
                if sl and sl > 0 and abs(entry_price - sl) > 0:
                    r_dist = abs(entry_price - sl)
                    exit_r = (exit_price - entry_price) / r_dist if is_long else (entry_price - exit_price) / r_dist
                    display_r = min(mae_r, exit_r) if mae_r < 0 else mae_r
                else:
                    display_r = mae_r
                r_text = f"+{display_r:.2f}R" if display_r >= 0 else f"{display_r:.2f}R"
                lines.append(f"📉 <b>Просадка:</b> <code>{r_text}</code>")
        if fees and fees > 0:
            lines.append(f"💸 <b>Комиссия:</b> <code>${fees:.2f}</code>")
        if holding_seconds and holding_seconds > 0:
            td = int(holding_seconds)
            h = td // 3600
            m = (td % 3600) // 60
            if h > 0 and m > 0:
                lines.append(f"⏱ <b>Длительность:</b> {h}ч {m}мин")
            elif h > 0:
                lines.append(f"⏱ <b>Длительность:</b> {h} час")
            else:
                lines.append(f"⏱ <b>Длительность:</b> {m}мин")
        if trade_id:
            lines.append(f"🔢 <b>Сделка</b> #{trade_id}")
        if balance is not None:
            lines.append(f"💰 <b>Баланс:</b> <code>${balance:.2f}</code>")
        return "\n".join(lines)

    async def notify_trade_open(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        qty: float,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
        reason: str = "",
        exchange=None,
        trade_id: Optional[int] = None,
        mode: str = "AUTO",
        probability: Optional[float] = None,
        quality: Optional[str] = None,
        score: Optional[float] = None,
        leverage: int = 3,
        entry_time=None,
        strategy: Optional[str] = None,
        timeframe: Optional[str] = None,
    ):
        """v18: отправляет фото (synthetic chart) + текстовое сообщение."""
        try:
            from tradingos.notifier.chart_gen import generate_trade_chart
            interval = "15"
            if timeframe:
                tf = str(timeframe).lower().strip()
                if "1h" in tf or "60" in tf:
                    interval = "60"
                elif "5" in tf and "min" not in tf:
                    interval = "5"
                elif "3" in tf:
                    interval = "3"
                elif "30" in tf:
                    interval = "30"
            chart_bytes = await generate_trade_chart(
                exchange=None, symbol=symbol,
                entry_price=entry_price, entry_time=entry_time,
                side=side, sl=sl, tp=tp,
                interval=interval, leverage=leverage, pnl=None,
            )
            text = self._format_open_text(
                symbol, side, entry_price, sl, tp, leverage, score, reason, qty,
            )
            if chart_bytes:
                # CRITICAL: send photo WITH caption so user sees graph + text in ONE message
                await self.send_photo(chart_bytes, caption=text)
                return
        except Exception as e:
            log.warning(f"Chart gen failed: {e}")
        # Fallback: text only if chart generation failed
        text = self._format_open_text(
            symbol, side, entry_price, sl, tp, leverage, score, reason, qty,
        )
        await self.send(text)

    async def notify_trade_close(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        pnl: float,
        exchange=None,
        trade_id: Optional[int] = None,
        mode: str = "AUTO",
        strategy: Optional[str] = None,
        timeframe: Optional[str] = None,
        leverage: int = 3,
        entry_time=None,
        exit_time=None,
        holding_hours: float = 0.0,
        sl: float = 0.0,
        tp: float = 0.0,
        fees: float = 0.0,
        reason: str = "",
        probability: Optional[float] = None,
        quality: Optional[str] = None,
        score: Optional[float] = None,
        guardian_events: Optional[list] = None,
        balance: Optional[float] = None,
        mfe_r: Optional[float] = None,
        mae_r: Optional[float] = None,
    ):
        """v18: отправляет фото (synthetic chart) + текстовое сообщение."""
        try:
            from tradingos.notifier.chart_gen import generate_trade_chart
            # Adaptive timeframe based on holding duration
            if holding_hours <= 0.5:
                interval = "1"
            elif holding_hours <= 2:
                interval = "5"
            elif holding_hours <= 6:
                interval = "15"
            elif holding_hours <= 24:
                interval = "30"
            else:
                interval = "60"
            if timeframe:
                tf = str(timeframe).lower().strip()
                if "1h" in tf or "60" in tf:
                    interval = "60"
                elif "30" in tf:
                    interval = "30"
                elif "5" in tf and "min" not in tf:
                    interval = "5"
                elif "3" in tf:
                    interval = "3"
            chart_bytes = await generate_trade_chart(
                exchange=None, symbol=symbol,
                entry_price=entry_price, entry_time=entry_time,
                exit_price=exit_price, exit_time=exit_time,
                side=side, sl=sl, tp=tp,
                interval=interval, leverage=leverage, pnl=pnl, reason=reason,
                mfe_r=mfe_r, mae_r=mae_r, holding_hours=holding_hours,
            )
            holding_seconds = holding_hours * 3600 if holding_hours else 0
            text = self._format_close_text(
                symbol=symbol, side=side, entry_price=entry_price,
                exit_price=exit_price, pnl=pnl, fees=fees,
                holding_seconds=holding_seconds, reason=reason,
                trade_id=trade_id, leverage=leverage, score=score,
                sl=sl, tp=tp, mfe_r=mfe_r, mae_r=mae_r, balance=balance,
                qty=qty,
            )
            # CRITICAL: Send photo WITH caption so user sees graph + text in ONE message
            if chart_bytes:
                log.info(
                    f"TRADE_CLOSE_NOTIFICATION "
                    f"symbol={symbol} side={side} "
                    f"chart_bytes={len(chart_bytes)} "
                    f"caption_chars={len(text)} "
                    f"status=GENERATED"
                )
                await self.send_photo(chart_bytes, caption=text)
                return  # do NOT send text separately
        except Exception as e:
            log.warning(f"Chart gen failed: {e}")
            log.warning(
                f"TRADE_CLOSE_NOTIFICATION "
                f"symbol={symbol} status=CHART_FAILED reason={type(e).__name__}:{e}"
            )
        # Fallback: send text only if chart generation failed or chart_bytes is empty
        holding_seconds = holding_hours * 3600 if holding_hours else 0
        text = self._format_close_text(
            symbol=symbol, side=side, entry_price=entry_price,
            exit_price=exit_price, pnl=pnl, fees=fees,
            holding_seconds=holding_seconds, reason=reason,
            trade_id=trade_id, leverage=leverage, score=score,
            sl=sl, tp=tp, mfe_r=mfe_r, mae_r=mae_r, balance=balance,
            qty=qty,
        )
        await self.send(text)

    async def notify_guardian_event(
        self,
        symbol: str,
        event_type: str,  # BE, PARTIAL, TIGHT, TIMEOUT
        current_sl: float,
        entry_price: float,
        peak_r: float,
    ):
        """Уведомление о Guardian событии (BE / Partial / Tight / Timeout).

        Терминология:
          BE      — Стоп перенесён в безубыток (Entry). Худший исход = 0.
          PARTIAL — Зафиксирована часть прибыли (Entry + 0.5R).
          TIGHT   — Усиленная защита (Entry + 1.0R).
          TIMEOUT — Позиция держится > 48h без движения.
        """
        # Локализация
        events = {
            "BE":      ("🛡️", "Безубыток (БУ)",
                       "Стоп перенесён на цену входа. Худший исход теперь = 0$. "
                       "Прибыль зафиксирована на нуле. Дальнейшее движение — чистая прибыль."),
            "PARTIAL": ("🔒", "Частичная фиксация",
                       "Стоп поднят выше входа. Зафиксирована часть прибыли. "
                       "Сделка частично защищена от разворота."),
            "TIGHT":   ("🔐", "Жёсткая защита",
                       "Стоп значительно поднят. Большая часть прибыли зафиксирована. "
                       "Сделка максимально защищена."),
            "TIMEOUT": ("⏰", "Таймаут",
                       "Позиция держится дольше 48 часов. Рекомендуется ручная проверка."),
        }
        emoji, name, desc = events.get(event_type, ("📌", event_type, ""))

        text = (
            f"{emoji} <b>{symbol}</b> — {name}\n\n"
            f"<b>Что это:</b>\n{desc}\n\n"
            f"<b>Метрики:</b>\n"
            f"├ Цена входа: <code>{entry_price:.4f}</code>\n"
            f"├ Пик R: <code>{peak_r:+.2f}R</code>\n"
            f"└ Новый SL: <code>{current_sl:.4f}</code>"
        )
        await self.send(text)

    async def notify_error(self, source: str, error: str):
        """Уведомление об ошибке."""
        text = (
            f"🚨 ОШИБКА · `{source}`\n\n"
            f"<code>{error[:800]}</code>\n"
        )
        await self.send(text)

    async def notify_daily_report(self, report_text: str):
        """Дневной отчёт."""
        text = "📅 ДНЕВНОЙ ОТЧЁТ\n\n" + report_text
        await self.send(text)


# ═══════════════════════════════════════════════════════════════
# Convenience factory
# ═══════════════════════════════════════════════════════════════

def make_notifier_from_env() -> Optional[Notifier]:
    """Создать Notifier из env vars."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return None
    return Notifier(token=token, chat_id=chat_id)
