"""
manual_bot.py — ОТДЕЛЬНЫЙ бот ручного контура (SIGNAL_ONLY) на Grizzly.

Изолирован от trading-control (MT4/MT5/auto_safe на старом боте).
Читает токен из manual_bot.env (Grizzly 8330872040).
Только ручные команды: /signals, /positions, /close, /help + кнопки
(ms_*, close_yes_*, positions, status, signals_menu).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, "/root/mt5_trading_bot")

from dotenv import load_dotenv

# Grizzly — только ручной контур
load_dotenv(Path("/root/mt5_trading_bot/manual_bot.env"))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ManualBot] %(levelname)s: %(message)s",
)
logger = logging.getLogger("ManualBot")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler,
    CommandHandler, ContextTypes, MessageHandler, filters,
)
from telegram.request import HTTPXRequest


def build_app() -> Application:
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in manual_bot.env")
        sys.exit(1)
    proxy_url = "socks5://127.0.0.1:1080"
    request = HTTPXRequest(proxy=proxy_url, connect_timeout=15, read_timeout=30)
    get_updates_request = HTTPXRequest(proxy=proxy_url, connect_timeout=15, read_timeout=60)
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )

    from telegram_control.manual_signal import (
        cmd_signals, cmd_manual_help, cmd_pause, cmd_resume, cmd_limits,
        cmd_waitreport, handle_text, callback_handler_manual,
        cmd_bx_start, _bx_handle_callback, _handle_bx_confirm,
        _bx_recommend_action,
    )

    app.add_handler(CommandHandler("signals", cmd_signals))
    app.add_handler(CommandHandler("manual", cmd_manual_help))
    app.add_handler(CommandHandler("start", cmd_manual_help))
    app.add_handler(CommandHandler("help", cmd_manual_help))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("limits", cmd_limits))
    app.add_handler(CommandHandler("waitreport", cmd_waitreport))
    app.add_handler(CommandHandler("bx", cmd_bx_start))
    # Настройки системы (2026-09-01)
    from telegram_control.settings import (
        cmd_settings, settings_callback, settings_val_callback,
    )
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CallbackQueryHandler(settings_callback, pattern=r"^SET:"))
    app.add_handler(CallbackQueryHandler(settings_val_callback, pattern=r"^SETVAL:"))

    # Визард ставок (2026-09-03): /bet SYMBOL — ручная ставка с рекомендацией
    from telegram_control.bet_wizard import (
        cmd_bet, bet_callback, handle_bet_text, bet_cancel,
    )
    app.add_handler(CommandHandler("bet", cmd_bet))
    app.add_handler(CommandHandler("betcancel", bet_cancel))
    app.add_handler(CallbackQueryHandler(bet_callback, pattern=r"^BWS:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(callback_handler_manual, pattern=r"^ms_"))
    app.add_handler(CallbackQueryHandler(_bx_handle_callback, pattern=r"^bx_[a-z]+_"))
    app.add_handler(CallbackQueryHandler(_bx_recommend_action, pattern=r"^BXACT:"))

    # Кнопки-переходы (positions/status/signals_menu) обрабатывает callback_handler
    # из bot.py — здесь нужен лёгкий аналог
    from telegram_control import bot as tc_bot

    async def _mini_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        data = q.data or ""
        # Снимаем спиннер с кнопки ДО работы: если хендлер упадёт, кнопка не «зависнет».
        await q.answer()
        try:
            if data == "bx_menu":
                from telegram_control.manual_signal import cmd_bx_start
                await cmd_bx_start(update, context)
                return
            if data.startswith("BXGO:") or data.startswith("BXNO:"):
                from telegram_control.manual_signal import _handle_bx_confirm
                await _handle_bx_confirm(update, context, data)
                return
            if data in ("positions", "positions_menu"):
                await tc_bot.cmd_positions(update, context)
            elif data in ("status", "main_menu", "help_menu"):
                from telegram_control.manual_signal import cmd_main_menu, cmd_manual_help
                if data == "help_menu":
                    # показать help как ответ на кнопку
                    await q.message.reply_text(
                        "🎛 <b>РУЧНОЙ КОНТУР</b>\n\n"
                        "/signals — отсканировать рынок\n"
                        "/positions — позиции и PnL\n"
                        "SL SYMBOL цена — изменить стоп\n"
                        "TP SYMBOL цена — изменить тейк\n"
                        "«Открой BTC», «Пропусти» — быстрые команды",
                        parse_mode="HTML",
                    )
                else:
                    await cmd_main_menu(update, context)
            elif data == "signals_menu":
                from telegram_control.manual_signal import cmd_signals
                await cmd_signals(update, context)
            elif data == "sltp_menu":
                # Показать SL/TP всех позиций (через positions с деталями)
                await tc_bot.cmd_positions(update, context)
                await q.message.reply_text(
                    "⚙️ Для SL/TP: <code>SL SYMBOL цена</code> / <code>TP SYMBOL цена</code>",
                    parse_mode="HTML",
                )
            elif data == "risk_menu":
                from telegram_control.manual_signal import cmd_risk
                await cmd_risk(update, context)
            elif data == "pause_menu":
                from telegram_control.manual_signal import cmd_pause
                await cmd_pause(update, context)
            elif data == "resume_menu":
                from telegram_control.manual_signal import cmd_resume
                await cmd_resume(update, context)
            elif data.startswith("close_yes_"):
                await tc_bot.callback_handler(update, context)
            elif data == "cancel":
                pass
        except Exception as e:
            logger.error(f"_mini_callback {data} failed: {e}")
            try:
                await q.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
            except Exception:
                pass

    app.add_handler(CallbackQueryHandler(_mini_callback))

    # ─────── SOFT manual-risk confirmation handler ───────
    from telegram_control.manual_signal import (
        _pending_overrides, _execute_confirmed_pending, _is_token_valid,
        _esc, _audit_log, _place_market_order,
    )

    async def _confirm_risk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()  # clear spinner
        data = q.data or ""
        uid = q.from_user.id if q.from_user else None
        if uid is None or not data.startswith("CONFIRMRISK"):
            return
        parts = data.split(":", 1)
        if len(parts) != 2:
            return
        action, tok = parts[0], parts[1]
        # find token entry
        entry = _pending_overrides.get((uid, tok))
        if entry is None:
            await q.message.reply_text(
                "⚠️ Confirmation не найдена (expired или consumed).",
                parse_mode="HTML")
            return
        if action == "CONFIRMRISKNO":
            _pending_overrides.pop((uid, tok), None)
            _audit_log("/root/tradingos/logs/manual_overrides.jsonl", {
                "ts": __import__("datetime").datetime.utcnow().isoformat(),
                "uid": uid, "event": "confirmation_cancelled",
                "token": tok[:6] + "...", "reason": entry.get("reason"),
            })
            await q.message.reply_text(
                "❌ <b>Отменено.</b> Вход не выполнен.",
                parse_mode="HTML")
            return
        # CONFIRMRISK — soft override
        if not _is_token_valid(entry):
            _pending_overrides.pop((uid, tok), None)
            await q.message.reply_text(
                "⏰ <b>CONFIRMATION_EXPIRED</b> (>60 сек). Пересчёт.",
                parse_mode="HTML")
            return
        if entry.get("used"):
            await q.message.reply_text(
                "🚫 <b>Confirmation уже использована</b> (replay protection).",
                parse_mode="HTML")
            return
        # mark used BEFORE execution (replay protection)
        entry["used"] = True
        _pending_overrides[(uid, tok)] = entry
        # revalidate + execute via existing executor
        ok, msg = _execute_confirmed_pending(entry["pending"], entry["amount"], {})
        import datetime as _dt
        _now = _dt.datetime.utcnow()
        if ok:
            _audit_log("/root/tradingos/logs/manual_overrides.jsonl", {
                "ts": _now.isoformat(), "uid": uid, "event": "confirmation_received_executed",
                "token": tok[:6] + "...", "reason": entry.get("reason"),
                "proposed": entry.get("proposed"), "symbol": entry["pending"].get("symbol"),
                "side": entry["pending"].get("side"),
            })
            await q.message.reply_text(
                f"✅ <b>Override принят.</b> Ордер выполнен.\n"
                f"Причина: <i>{_esc(str(entry.get('reason','')))}</i>\n"
                f"Risk: ${entry.get('proposed',0):.2f} "
                f"(обычный ${entry.get('normal_risk',0):.2f})\n"
                f"Audit: <code>manual_overrides.jsonl</code>",
                parse_mode="HTML")
        else:
            _audit_log("/root/tradingos/logs/manual_overrides.jsonl", {
                "ts": _now.isoformat(), "uid": uid, "event": "confirmation_received_blocked",
                "token": tok[:6] + "...", "reason": entry.get("reason"),
                "execution_msg": msg,
            })
            await q.message.reply_text(
                f"🚫 <b>Исполнение отклонено</b>\n"
                f"<i>{_esc(str(msg))}</i>\n"
                f"Soft override НЕ ослабляет hard safety limits.",
                parse_mode="HTML")
        # cleanup
        _pending_overrides.pop((uid, tok), None)

    app.add_handler(CallbackQueryHandler(_confirm_risk_callback, pattern=r"^CONFIRMRISK"))

    # Фоновые задачи ручного контура
    import asyncio
    try:
        from telegram_control.manual_signal import (
            background_scan_loop, background_position_monitor,
            background_hourly_summary, background_equity_sampler,
            background_bingx_digest,
        )
        cfg_ms = json.loads(Path("/root/tradingos/operations/manual_session.json").read_text())
        interval = int(cfg_ms.get("scan_interval_min", 20))
        asyncio.get_event_loop().create_task(
            background_scan_loop(app, interval_min=interval))
        asyncio.get_event_loop().create_task(background_position_monitor())
        asyncio.get_event_loop().create_task(background_hourly_summary(app))
        asyncio.get_event_loop().create_task(background_equity_sampler(interval_sec=300))
        asyncio.get_event_loop().create_task(background_bingx_digest(app, interval_min=15))
    except Exception as e:
        logger.error(f"Фоновые задачи не запущены: {e}")

    logger.info(f"✅ Manual Bot (Grizzly) started, chat={CHAT_ID}")
    return app


def main():
    # Singleton guard: a second manual_bot instance double-polls Telegram
    # getUpdates and sends duplicate position-close notifications.
    sys.path.insert(0, str(ROOT))
    from core.singleton import acquire_singleton_lock
    acquire_singleton_lock("manual_bot")
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
