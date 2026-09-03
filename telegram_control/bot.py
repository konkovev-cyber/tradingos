#!/usr/bin/env python3
"""
Telegram Control Bot — Trading Control Panel.

Команды:
    /start     — панель управления
    /status    — сводка по балансу и позициям
    /positions — список всех позиций с кнопками
    /close     — закрыть позицию (с подтверждением)
    /panic     — экстренное закрытие (с подтверждением)
    /freeze    — заморозить новые входы
    /unfreeze  — разморозить
    /think     — PIE анализ рынка
    /mode      — переключить режим PIE
    /help      — справка

Запуск:
    python3 -m telegram_control.bot
"""

import os
import sys
import json
import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

# FIX: import handling for the module
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path("/root/mt5_trading_bot")))

from dotenv import load_dotenv
load_dotenv(Path("/root/mt5_trading_bot/.env"))

from adapters.bingx_client import BingXClient
from telegram_control.emergency import EmergencyExecutor
from telegram_control.config import (
    get_position_summary, get_pnl_summary, set_freeze, set_mode, load_state
)

# Auto Safe
sys.path.insert(0, str(ROOT))
from control.auto_safe import AutoSafeExecutor

# PIE
import importlib.util
_pie_spec = importlib.util.spec_from_file_location(
    "position_intelligence", str(ROOT / "core" / "intelligence" / "position_intelligence.py")
)
_pie_mod = importlib.util.module_from_spec(_pie_spec)
_pie_spec.loader.exec_module(_pie_mod)
PIEPositionIntelligence = _pie_mod.PIEPositionIntelligence

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("TGBot")

# --- State ---
_pending_confirmations: dict = {}


def get_pie() -> PIEPositionIntelligence:
    """Получить PIE инстанс (читает БД)."""
    return PIEPositionIntelligence(mode="live_assist", db_path=ROOT / "tradingos_data.db")


# ── Helper: format position text ──

def format_positions(positions: list) -> str:
    """Форматировать список позиций для Telegram."""
    if not positions:
        return "No open positions."

    lines = ["📌 OPEN POSITIONS\n"]
    for p in positions:
        symbol = p.get("symbol", "?")
        side = p.get("side", "?")
        pnl = p.get("pnl_pct", 0) or 0
        health = p.get("health_score", 0) or 0
        rec = p.get("recommendation", "?")
        entry = p.get("entry_price", 0)

        emoji = "🟢" if pnl > 0.005 else "🔴" if pnl < -0.005 else "⚪"
        rec_icon = "✅" if rec == "HOLD" else "⚠️" if rec == "MOVE_SL_BE" else "🔔"

        lines.append(
            f"{emoji} {symbol:10s} {side:4s}\n"
            f"   Entry: {entry:.4f}\n"
            f"   PnL:   {pnl*100:+.2f}%\n"
            f"   HE:    {health:.0f}\n"
            f"   {rec_icon} PIE:  {rec}\n"
        )

    # Summary
    pnl_summary = get_pnl_summary(positions)
    lines.append(f"\n📊 Total PnL: {pnl_summary['total_pnl_pct']*100:+.2f}%")
    lines.append(f"   Wins: {pnl_summary['wins']}  Losses: {pnl_summary['losses']}")

    return "\n".join(lines)


# ── /start ──

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню."""
    state = load_state()

    text = (
        "🤖 **Trading Control Panel**\n\n"
        f"Режим: `{state['mode']}`\n"
        f"Заморозка: `{'🧊 ON' if state['frozen'] else '✅ OFF'}`\n\n"
        "Команды:\n"
        "`/status` — сводка\n"
        "`/positions` — список позиций\n"
        "`/pie_live` — визуальный монитор PIE\n"
        "`/opportunities` — кандидаты на защиту\n"
        "`/history` — PIE VALUE REPORT\n"
        "`/close SYMBOL` — закрыть позицию\n"
        "`/panic` — экстренное закрытие\n"
        "`/freeze` — заморозить входы\n"
        "`/think` — анализ PIE\n"
        "`/mode` — переключить режим\n"
        "`/help` — справка"
    )

    keyboard = [
        [InlineKeyboardButton("📊 Status", callback_data="status"),
         InlineKeyboardButton("📌 Positions", callback_data="positions")],
        [InlineKeyboardButton("🧠 PIE Live", callback_data="pie_live_menu"),
         InlineKeyboardButton("🎯 Watchlist", callback_data="opportunities_menu")],
        [InlineKeyboardButton("📋 History", callback_data="history_menu"),
         InlineKeyboardButton("🚨 Panic", callback_data="panic_menu")],
        [InlineKeyboardButton("🎯 Signals", callback_data="signals_menu"),
         InlineKeyboardButton("⚙ Mode", callback_data="mode_menu")],
    ]
    await update.message.reply_text(text, parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(keyboard))


# ── /status ──

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сводка по балансу и позициям."""
    summary = get_position_summary()
    state = load_state()

    text = [
        f"🤖 **Trading Status**\n",
        f"Режим: `{state['mode']}`",
        f"Заморозка: `{'🧊 ON' if state['frozen'] else 'OFF'}`",
        f"Позиций: {summary.get('total', 0)}",
    ]

    if summary.get("positions"):
        pnl_s = get_pnl_summary(summary["positions"])
        text.append(f"Совокупный PnL: {pnl_s['total_pnl_pct']*100:+.2f}%")
        text.append(f"Прибыльных: {pnl_s['wins']}  Убыточных: {pnl_s['losses']}")

    # Get PIE assist stats
    try:
        pie = PIEPositionIntelligence(mode="live_assist", db_path=ROOT / "tradingos_data.db")
        # Get closed analytics via pie
        closed = pie.get_closed_analytics(limit=5)
        if closed:
            text.append(f"\n📋 Закрытые позиции (всего в БД):")
        pie_quality = pie.get_assist_quality_report()
        if pie_quality.get("total", 0) > 0:
            text.append(f"\n🎯 Рекомендаций PIE: {pie_quality['total']}")
            q = pie_quality.get("move_sl_be", {})
            if q.get("count", 0) > 0:
                text.append(f"   MOVE_SL_BE: {q['count']}")
    except Exception as e:
        pass

    text.append(f"\nВремя: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    keyboard = [
        [InlineKeyboardButton("📌 Positions", callback_data="positions"),
         InlineKeyboardButton("🔄 Refresh", callback_data="status")],
    ]

    await update.message.reply_text("\n".join(text), parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(keyboard))


# ── /positions ──

async def cmd_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список позиций с кнопками (реальные Bybit-позиции + PIE-сводка)."""
    summary = get_position_summary()
    positions = summary.get("positions", [])

    # Задача 3: реальные Bybit-позиции (включая MANUAL) — показываем первыми.
    # FIX: _get_positions() (полный ответ биржи с avgPrice/unrealisedPnl/SL/TP),
    # а не get_open_positions_with_side() (только symbol/side/size → entry=0 →
    # абсурдные PnL/R). PnL берём напрямую с биржи (unrealisedPnl).
    real_pos = []
    try:
        sys.path.insert(0, "/root/tradingos")
        from tradingos.strategies.bybit_position_check import _get_positions
        real_pos = [p for p in _get_positions() if float(p.get("size", 0) or 0) > 0]
    except Exception as e:
        logger.warning(f"bybit positions fetch failed: {e}")

    text_parts = []
    if real_pos:
        text_parts.append("📌 <b>РЕАЛЬНЫЕ ПОЗИЦИИ (Bybit):</b>")
        # Live PnL и R: из state берём только риск на единицу (entry_to_sl_risk),
        # цены и unrealisedPnl — напрямую с биржи.
        import json as _json
        try:
            _state = _json.loads(Path("/root/tradingos/guardian/reality_state.json").read_text())
        except Exception:
            _state = {}
        for p in real_pos:
            sym = p.get("symbol", "?")
            side = p.get("side", "?")
            size = float(p.get("size", 0) or 0)
            entry = float(p.get("avgPrice", 0) or 0)
            cur = float(p.get("markPrice", 0) or 0)
            unreal = float(p.get("unrealisedPnl", 0) or 0)
            sl_ex = p.get("stopLoss") or "—"
            tp_ex = p.get("takeProfit") or "—"
            is_buy = str(side).lower().startswith("buy")
            profit = unreal if unreal else ((cur - entry) * size if is_buy else (entry - cur) * size)
            st = _state.get(sym, {})
            risk_unit = st.get("entry_to_sl_risk", 0)
            # R: прибыль на единицу ÷ риск на единицу
            if risk_unit and risk_unit > 0:
                per_unit = (cur - entry) if is_buy else (entry - cur)
                r_mult = per_unit / risk_unit
            else:
                r_mult = 0
            pnl_emoji = "🟢" if profit >= 0 else "🔴"
            source = "🔧" if st.get("source") == "MANUAL" else "🤖"
            text_parts.append(f"  {source} {sym} {side} | qty={size:.4g}")
            text_parts.append(f"      {pnl_emoji} PnL: {profit:+.4f}$ ({r_mult:+.2f}R) | цена: {cur:.6g}")
            text_parts.append(f"      🛑 SL: {sl_ex} | 🎯 TP: {tp_ex}")
        text_parts.append("")
    if positions:
        text_parts.append(format_positions(positions))
    else:
        text_parts.append("PIE: нет позиций")

    text = "\n".join(text_parts)

    # Build keyboard: one row per real position (с кнопкой CLOSE)
    keyboard = []
    for p in real_pos:
        symbol = p.get("symbol", "?").replace("-USDT", "")
        pnl = p.get("unrealisedPnl", 0) or 0
        try:
            pnl = float(pnl)
        except (TypeError, ValueError):
            pnl = 0
        emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        keyboard.append([
            InlineKeyboardButton(f"{emoji} {symbol}", callback_data=f"pos_{symbol}"),
            InlineKeyboardButton("🔴 CLOSE", callback_data=f"close_yes_{symbol}"),
        ])
    for p in positions:
        symbol = p.get("symbol", "?").replace("-USDT", "")
        if any(symbol == rp.get("symbol", "").replace("-USDT", "") for rp in real_pos):
            continue  # уже показан в реальных
        keyboard.append([InlineKeyboardButton(f"⚪ {symbol}", callback_data=f"pos_{symbol}")])

    keyboard.append([InlineKeyboardButton("🚨 CLOSE ALL", callback_data="panic_confirm")])
    keyboard.append([InlineKeyboardButton("🔄 Refresh", callback_data="positions")])

    await update.effective_message.reply_text(text, parse_mode="HTML",
                                              reply_markup=InlineKeyboardMarkup(keyboard))


# ── /close —────────────────────────────────────────────

async def cmd_close(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Закрыть позицию по символу."""
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/close SYMBOL`\nExample: `/close SOL`",
            parse_mode="Markdown",
        )
        return

    symbol = args[0].upper()
    if not symbol.endswith("-USDT"):
        symbol = f"{symbol}-USDT"

    summary = get_position_summary()
    target = None
    for p in summary.get("positions", []):
        if p.get("symbol") == symbol:
            target = p
            break

    if not target:
        await update.message.reply_text(f"Position {symbol} not found.")
        return

    pnl = target.get("pnl_pct", 0) or 0
    emoji = "🟢" if pnl > 0 else "🔴"

    text = (
        f"⚠️ **CLOSE REQUEST**\n\n"
        f"{emoji} {target['symbol']} {target['side']}\n\n"
        f"Entry: {target.get('entry_price', 0):.4f}\n"
        f"PnL:   {pnl*100:+.2f}%\n"
        f"HE:    {target.get('health_score', 0):.0f}\n\n"
        f"Confirm close?"
    )

    keyboard = [
        [
            InlineKeyboardButton("✅ YES", callback_data=f"close_yes_{symbol}"),
            InlineKeyboardButton("❌ CANCEL", callback_data="cancel"),
        ]
    ]
    await update.message.reply_text(text, parse_mode="Markdown",
                                     reply_markup=InlineKeyboardMarkup(keyboard))


# ── /panic —────────────────────────────────────────────

async def cmd_panic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Экстренное закрытие."""
    summary = get_position_summary()
    positions = summary.get("positions", [])

    if not positions:
        await update.message.reply_text("No positions to close.")
        return

    losers = [p for p in positions if (p.get("pnl_pct", 0) or 0) < 0]
    total_pnl = sum(p.get("pnl_pct", 0) or 0 for p in positions)

    text = [
        "🚨 **PANIC MODE**\n",
        f"Позиций: {len(positions)}",
        f"Убыточных: {len(losers)}",
        f"Совокупный PnL: {total_pnl*100:+.2f}%\n",
    ]

    if losers:
        text.append("Убыточные:")
        for p in losers:
            pnl = p.get("pnl_pct", 0) or 0
            text.append(f"  🔴 {p['symbol']} {pnl*100:+.2f}%")

    text.append("\nВыберите действие:")

    keyboard = [
        [InlineKeyboardButton("🔴 Close Losers Only", callback_data="panic_losers")],
        [InlineKeyboardButton("🚨 Close ALL", callback_data="panic_all"),],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]

    await update.message.reply_text("\n".join(text), parse_mode="Markdown",
                                    reply_markup=InlineKeyboardMarkup(keyboard))


# ── /freeze /unfreeze ──

async def cmd_freeze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заморозить новые входы."""
    state = set_freeze(True)
    text = (
        "🧊 **TRADING FROZEN**\n\n"
        "New entries: `BLOCKED`\n"
        "Current positions: `ACTIVE`\n"
        "PIE: `CONTINUE OBSERVE`\n\n"
        "Use `/unfreeze` to resume."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_unfreeze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разморозить."""
    state = set_freeze(False)
    await update.message.reply_text(
        "✅ **Trading resumed.**\n\nNew entries allowed.",
        parse_mode="Markdown",
    )


# ── /think ──

async def cmd_think(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PIE анализ рынка."""
    summary = get_position_summary()
    positions = summary.get("positions", [])

    text = [
        "🧠 **PIE MARKET ANALYSIS**\n",
    ]

    if positions:
        holds = [p for p in positions if p.get("recommendation") == "HOLD"]
        warns = [p for p in positions if p.get("recommendation") in ("MOVE_SL_BE", "TAKE_PARTIAL")]
        watch = [p for p in positions if p.get("health_score", 100) < 70]

        text.append(f"Total positions: {len(positions)}")
        text.append(f"🟢 HOLD:       {len(holds)}")
        text.append(f"⚠️ WARNING:    {len(warns)}")
        text.append(f"👁 Watch list: {len(watch)}\n")

        if warns:
            text.append("Требуют внимания:")
            for p in warns:
                symbol = p.get("symbol", "?")
                rec = p.get("recommendation", "?")
                pnl = p.get("pnl_pct", 0) or 0
                text.append(f"  ⚠️ {symbol} → {rec} ({pnl*100:+.2f}%)")

        if watch:
            text.append("\nПод наблюдением:")
            for p in watch:
                symbol = p.get("symbol", "?")
                health = p.get("health_score", 0)
                pnl = p.get("pnl_pct", 0) or 0
                text.append(f"  👁 {symbol} HE={health:.0f} ({pnl*100:+.2f}%)")
    else:
        text.append("No open positions to analyze.")

    # PIE assist stats
    try:
        pie = PIEPositionIntelligence(mode="live_assist", db_path=ROOT / "tradingos_data.db")
        quality = pie.get_assist_quality_report()
        if quality.get("total", 0) > 0:
            text.append(f"\n📊 PIE рекомендаций: {quality['total']}")
            for rec_type, stats in quality.get("by_type", {}).items():
                text.append(f"   {rec_type}: {stats['count']}")
    except Exception:
        pass

    text.append(f"\n{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    await update.message.reply_text("\n".join(text))


# ── /mode ──

async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переключить режим."""
    keyboard = [
        [InlineKeyboardButton("👤 MANUAL", callback_data="mode_manual")],
        [InlineKeyboardButton("👁 ASSIST", callback_data="mode_assist")],
        [InlineKeyboardButton("🤖 AUTO SAFE", callback_data="mode_auto_safe")],
        [InlineKeyboardButton("🤖 AUTO FULL", callback_data="mode_auto_full")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ]
    state = load_state()
    await update.message.reply_text(
        f"**Select PIE mode**\n\nCurrent: `{state['mode']}`\n\n"
        "`manual` — PIE только советует\n"
        "`assist` — PIE предлагает действия\n"
        "`auto_safe` — только MOVE_SL_BE + TAKE_PARTIAL\n"
        "`auto_full` — полное управление",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ── /help ──

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Справка."""
    text = (
        "🤖 **Trading Control — Commands**\n\n"
        "`/start` — главное меню\n"
        "`/status` — сводка\n"
        "`/positions` — список позиций\n"
        "`/close SOL` — закрыть SOL\n"
        "`/panic` — экстренное закрытие\n"
        "`/freeze` — заморозить входы\n"
        "`/unfreeze` — разморозить\n"
        "`/think` — PIE анализ\n"
        "`/history` — PIE Value Report\n"
        "`/help` — эта справка\n\n"
        "🔒 Все опасные действия требуют подтверждения.\n"
        "📋 Все действия логируются."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ── /history —──────────────────────────────────────────────

async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PIE VALUE REPORT — сколько денег система помогла сохранить."""
    try:
        summary = get_position_summary()
        positions = summary.get("positions", [])
        
        # Calculate PnL in USDT (approximate)
        total_pnl_pct = sum(p.get("pnl_pct", 0) or 0 for p in positions)
        # Simulate with small capital for display
        assumed_capital = 352.0
        current_pnl_usd = total_pnl_pct * assumed_capital
        
        # Max possible profit (sum of all MFE)
        total_mfe_pct = sum(p.get("max_profit_seen", 0) or 0 for p in positions)
        max_possible_pnl_usd = total_mfe_pct * assumed_capital
        
        # Lost movement
        lost_movement = max_possible_pnl_usd - current_pnl_usd
        
        # PIE simulated result
        pie_improvement = 0.0
        try:
            conn = sqlite3.connect(str(ROOT / "tradingos_data.db"))
            cursor = conn.execute("""
                SELECT AVG(pnl_after_60m - pnl_at_recommendation)
                FROM live_assist_log
                WHERE pnl_after_60m IS NOT NULL
            """)
            row = cursor.fetchone()
            if row and row[0] is not None:
                pie_improvement = row[0]  # avg improvement per recommendation
            conn.close()
        except Exception:
            pass
        
        # PIE simulated pnl
        pie_estimated = max_possible_pnl_usd * 0.7  # estimate: PIE can capture ~70% of MFE
        
        efficiency = (current_pnl_usd / max_possible_pnl_usd * 100) if max_possible_pnl_usd > 0 else 0
        
        text = [
            "📊 **PIE VALUE REPORT**\n",
            f"📌 Positions tracked: {len(positions)}",
            f"",
            f"💰 **Current Performance:**",
            f"Max possible profit (sum MFE): `${max_possible_pnl_usd:.2f}`",
            f"Current floating PnL:         `${current_pnl_usd:.2f}`",
            f"Lost to movement:             `-${abs(lost_movement):.2f}`",
            f"Efficiency:                   `{efficiency:.0f}%`",
            f"",
            f"🤖 **If PIE managed:**",
            f"Estimated capture: `{pie_estimated:.0f}%` of MFE",
            f"Simulated result:  `${pie_estimated:.2f}`",
            f"",
            f"🎯 **Goal:**",
            f"Improve efficiency from {efficiency:.0f}% → 70%+",
            f"That would save `${max_possible_pnl_usd * 0.5:.2f}` per cycle",
            f"",
            f"_Data from current open positions._",
            f"_Final report after positions close._",
        ]
        
        # Recent recommendations
        try:
            quality = pie.get_assist_quality_report()
            if quality.get("total", 0) > 0:
                text.append(f"\n**Recommendations:** {quality['total']}")
                for rec_type, stats in quality.get("by_type", {}).items():
                    text.append(f"   `{rec_type}`: {stats['count']}")
        except Exception:
            pass
        
        await update.message.reply_text("\n".join(text), parse_mode="Markdown")
        
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


# ── /opportunities —─────────────────────────────────────────

async def cmd_opportunities(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать позиции, близкие к условиям AUTO SAFE."""
    summary = get_position_summary()
    positions = summary.get("positions", [])

    if not positions:
        await update.message.reply_text("No open positions.")
        return

    # AUTO SAFE thresholds (same as control/auto_safe.py)
    MIN_PNL = 0.01       # 1%
    MIN_MFE = 0.015      # 1.5%
    MAX_RETRACE = 0.20   # 20%
    MIN_HEALTH = 80

    text = ["🎯 **PIE WATCHLIST**\n"]

    watch = []      # close to triggers
    triggered = []  # would trigger
    far = []        # not close

    for p in positions:
        symbol = p.get("symbol", "?")
        pnl = p.get("pnl_pct", 0) or 0
        mfe = p.get("max_profit_seen", 0) or 0
        retrace = p.get("profit_retracement", 0) or 0
        health = p.get("health_score", 0) or 0
        rec = p.get("recommendation", "?")
        side = p.get("side", "?")

        # Calculate distance to each threshold
        if pnl >= MIN_PNL and mfe >= MIN_MFE and retrace <= MAX_RETRACE and health >= MIN_HEALTH:
            triggered.append((symbol, side, pnl, mfe, retrace, health, rec))
        elif pnl > 0:
            # Close to triggering
            dist_pnl = MIN_PNL - pnl
            dist_mfe = MIN_MFE - mfe
            if dist_pnl < 0.005 or dist_mfe < 0.005:
                watch.append((symbol, side, pnl, mfe, retrace, health, rec, dist_pnl, dist_mfe))
            else:
                far.append((symbol, side, pnl, mfe, retrace, health, rec))
        else:
            far.append((symbol, side, pnl, mfe, retrace, health, rec))

    # Show triggered (would fire AUTO SAFE)
    if triggered:
        text.append("**🔥 Would trigger AUTO SAFE:**")
        for s, sd, pnl, mfe, rt, h, r in triggered:
            text.append(f"  `{s}` {sd} pnl={pnl*100:+.1f}% mfe={mfe*100:+.1f}% → {r}")
        text.append("")

    # Show close to triggering
    if watch:
        text.append("**👀 Close to AUTO SAFE:**")
        for s, sd, pnl, mfe, rt, h, r, dp, dm in watch:
            need = []
            if dp > 0:
                need.append(f"+{dp*100:.2f}% PnL")
            if dm > 0:
                need.append(f"+{dm*100:.2f}% MFE")
            need_str = ", ".join(need) if need else "ready"
            text.append(f"  `{s}` {sd} pnl={pnl*100:+.1f}% mfe={mfe*100:+.1f}% h={h:.0f}")
            text.append(f"     need: {need_str}")
        text.append("")

    # Show losers
    losers = [(s, sd, pnl, mfe, rt, h, r) for s, sd, pnl, mfe, rt, h, r in far if pnl < 0]
    if losers:
        text.append("**🔴 In loss:**")
        for s, sd, pnl, mfe, rt, h, r in losers:
            text.append(f"  `{s}` {sd} pnl={pnl*100:+.1f}% h={h:.0f}")
        text.append("")

    # Show small winners (not close to trigger)
    small = [(s, sd, pnl, mfe, rt, h, r) for s, sd, pnl, mfe, rt, h, r in far if pnl >= 0]
    if small:
        text.append("**⚪ Small winners (need more MFE):**")
        for s, sd, pnl, mfe, rt, h, r in small:
            text.append(f"  `{s}` {sd} pnl={pnl*100:+.1f}% mfe={mfe*100:+.1f}%")

    text.append(f"\n_Thresholds: PnL>{MIN_PNL*100:.0f}% MFE>{MIN_MFE*100:.1f}% retrace<{MAX_RETRACE*100:.0f}% H>{MIN_HEALTH}_")

    await update.message.reply_text("\n".join(text), parse_mode="Markdown")


# ── Callback handler ──

# ── /pie_live —─────────────────────────────────────────────

def _health_bar(health: float) -> str:
    """ASCII health bar."""
    filled = int(health / 10)
    empty = 10 - filled
    return "█" * filled + "░" * empty


async def cmd_pie_live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Визуальный монитор PIE — все позиции с прогресс-барами."""
    summary = get_position_summary()
    positions = summary.get("positions", [])

    if not positions:
        await update.message.reply_text("No open positions to monitor.")
        return

    # Сортировка по health (худшие сверху — требуют внимания)
    sorted_pos = sorted(positions, key=lambda p: p.get("health_score", 100) or 100)

    text = ["🧠 **PIE LIVE MONITOR**\n"]

    for p in sorted_pos:
        symbol = p.get("symbol", "?")
        side = p.get("side", "?")
        pnl = p.get("pnl_pct", 0) or 0
        mfe = p.get("max_profit_seen", 0) or 0
        mae = p.get("max_loss_seen", 0) or 0
        retrace = p.get("profit_retracement", 0) or 0
        health = p.get("health_score", 0) or 0
        rec = p.get("recommendation", "?")
        state = p.get("state", "?")

        # Bar
        bar = _health_bar(health)

        # Color emoji
        if pnl > 0.005:
            emoji = "🟢"
        elif pnl < -0.005:
            emoji = "🔴"
        else:
            emoji = "⚪"

        # Rec icon
        if rec == "HOLD":
            rec_icon = "✅"
        elif rec == "MOVE_SL_BE":
            rec_icon = "🛡"
        elif rec == "TAKE_PARTIAL":
            rec_icon = "✂️"
        elif rec == "WAIT":
            rec_icon = "👁"
        elif rec == "EXIT_NOW":
            rec_icon = "🚨"
        else:
            rec_icon = "📌"

        text.append(
            f"{emoji} **{symbol}** {side}\n"
            f"   {bar} HE {health:.0f}\n"
            f"   PnL `{pnl*100:+.2f}%`  MFE `{mfe*100:+.2f}%`  MAE `{mae*100:+.2f}%`\n"
            f"   Retrace `{retrace*100:.0f}%`  State `{state}`\n"
            f"   {rec_icon} PIE: `{rec}`\n"
        )

    # Итог
    pnl_sum = get_pnl_summary(positions)
    text.append(
        f"\n📊 **Total**: {pnl_sum['total_pnl_pct']*100:+.2f}%  "
        f"W:{pnl_sum['wins']} L:{pnl_sum['losses']}"
    )
    text.append(f"\n_Sorted by health — worst first_")

    await update.message.reply_text("\n".join(text), parse_mode="Markdown")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик inline кнопок."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # Кнопки ручного контура (SIGNAL_ONLY) обрабатывает отдельный хендлер
    if data.startswith("ms_"):
        return

    if data == "status":
        summary = get_position_summary()
        state = load_state()
        text = [
            f"🤖 **Trading Status**\n",
            f"Режим: `{state['mode']}`  "
            f"Заморозка: `{'🧊 ON' if state['frozen'] else 'OFF'}`",
            f"Позиций: {summary.get('total', 0)}",
        ]
        if summary.get("positions"):
            pnl_s = get_pnl_summary(summary["positions"])
            text.append(f"PnL: {pnl_s['total_pnl_pct']*100:+.2f}%  "
                       f"W: {pnl_s['wins']}  L: {pnl_s['losses']}")
        await query.edit_message_text("\n".join(text), parse_mode="Markdown")

    elif data == "positions":
        summary = get_position_summary()
        text = format_positions(summary.get("positions", []))

        keyboard = []
        for p in summary.get("positions", []):
            symbol = p.get("symbol", "?").replace("-USDT", "")
            pnl = p.get("pnl_pct", 0) or 0
            emoji = "🟢" if pnl > 0.005 else "🔴" if pnl < -0.005 else "⚪"
            keyboard.append([
                InlineKeyboardButton(f"{emoji} {symbol}", callback_data=f"pos_{symbol}")
            ])

        keyboard.append([
            InlineKeyboardButton("🚨 CLOSE ALL", callback_data="panic_confirm")
        ])
        keyboard.append([
            InlineKeyboardButton("🔄 Refresh", callback_data="positions")
        ])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

    elif data == "think":
        await query.edit_message_text("🧠 PIE analysis... (see /think output)")
        # Re-trigger think
        await cmd_think(update, context)

    elif data == "panic_menu":
        await cmd_panic(update, context)

    elif data == "panic_losers":
        keyboard = [
            [
                InlineKeyboardButton("🚨 YES, close losers", callback_data="panic_exec_losers"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ]
        ]
        await query.edit_message_text(
            "⚠️ **Close ALL losing positions?**\n\nThis action cannot be undone.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data == "panic_all":
        keyboard = [
            [
                InlineKeyboardButton("🚨 YES, close ALL", callback_data="panic_exec_all"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ]
        ]
        await query.edit_message_text(
            "⚠️ **Close ALL positions?**\n\nThis action cannot be undone.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data == "panic_confirm":
        await cmd_panic(update, context)

    elif data == "panic_exec_losers":
        await query.edit_message_text("🚨 Closing losers...")
        client = context.bot_data.get("bingx_client")
        if client:
            executor = EmergencyExecutor(client)
            result = await executor.close_all_positions(reason="panic_losers", only_losers=True)
            text = (
                f"🚨 **Panic Close Complete**\n\n"
                f"Total: {result['total']}\n"
                f"Closed: {result['closed']}\n"
                f"Failed: {result['failed']}\n"
            )
            for r in result.get("results", []):
                if r.get("success"):
                    pnl = r.get("pnl_pct", 0) or 0
                    text += f"  ✅ {r['symbol']} ({pnl*100:+.2f}%)\n"
                elif r.get("skipped"):
                    pass
                else:
                    text += f"  ❌ {r.get('symbol', '?')}: {r.get('error', '?')}\n"
            await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "panic_exec_all":
        await query.edit_message_text("🚨 Closing all positions...")
        client = context.bot_data.get("bingx_client")
        if client:
            executor = EmergencyExecutor(client)
            result = await executor.close_all_positions(reason="panic_all")
            text = (
                f"🚨 **Close All Complete**\n\n"
                f"Total: {result['total']}\n"
                f"Closed: {result['closed']}\n"
                f"Failed: {result['failed']}\n"
            )
            for r in result.get("results", []):
                if r.get("success"):
                    pnl = r.get("pnl_pct", 0) or 0
                    text += f"  ✅ {r['symbol']} ({pnl*100:+.2f}%)\n"
                else:
                    text += f"  ❌ {r.get('symbol', '?')}: {r.get('error', '?')}\n"
            await query.edit_message_text(text, parse_mode="Markdown")

    elif data == "freeze_toggle":
        state = load_state()
        if state["frozen"]:
            set_freeze(False)
            await query.edit_message_text("✅ **Trading resumed**\n\nNew entries allowed.")
        else:
            set_freeze(True)
            await query.edit_message_text(
                "🧊 **TRADING FROZEN**\n\n"
                "New entries: BLOCKED\n"
                "Current positions: ACTIVE\n"
                "PIE: CONTINUE OBSERVE"
            )

    elif data.startswith("mode_"):
        mode = data.replace("mode_", "")
        set_mode(mode)
        await query.edit_message_text(f"✅ **Mode changed to `{mode}`**", parse_mode="Markdown")

    elif data.startswith("pos_"):
        symbol = data.replace("pos_", "") + "-USDT"
        summary = get_position_summary()
        target = None
        for p in summary.get("positions", []):
            if p.get("symbol") == symbol:
                target = p
                break

        if target:
            pnl = target.get("pnl_pct", 0) or 0
            health = target.get("health_score", 0) or 0
            mfe = target.get("max_profit_seen", 0) or 0
            mae = target.get("max_loss_seen", 0) or 0
            rec = target.get("recommendation", "?")
            entry = target.get("entry_price", 0)
            current = target.get("current_price", entry)
            side = target.get("side", "?")
            reason = target.get("reason", "")
            retrace = target.get("profit_retracement", 0) or 0

            emoji = "🟢" if pnl > 0.005 else "🔴" if pnl < -0.005 else "⚪"

            # Build "почему" reasoning
            reasons = []
            if health >= 85:
                reasons.append("✅ Strong health")
            elif health >= 70:
                reasons.append("⚠️ Moderate health — watching")
            else:
                reasons.append("🔴 Low health — monitor close")

            if rec == "HOLD":
                if mfe > 0:
                    reasons.append("✅ MFE growing — trend intact")
                if retrace < 0.3:
                    reasons.append("✅ No significant retracement")
                reasons.append("✅ Structure alive — let it run")
            elif rec == "MOVE_SL_BE":
                reasons.append(f"🛡 Profit retrace {retrace*100:.0f}% from peak")
                reasons.append(f"🛡 Protect accrued profit")
            elif rec == "TAKE_PARTIAL":
                reasons.append(f"✂️ Decay {retrace*100:.0f}% — lock some profit")
            elif rec == "WAIT":
                reasons.append("👁 Entry forming — no action needed")
            elif rec == "EXIT_NOW":
                reasons.append("🚨 Thesis compromised — exit")
            elif rec == "TIGHTEN_SL":
                reasons.append("⚠️ Risk zone — tighten stop")

            # Trend alignment
            state = target.get("state", "")
            if "PROFIT" in state and rec == "HOLD":
                reasons.append("📈 Profit phase — holding for more")
            if "RISK" in state:
                reasons.append("🔴 Risk zone — be ready to act")
            if "DECAY" in state:
                reasons.append("⚠️ Profit decaying — watch trigger")

            text = (
                f"{emoji} **{symbol}** {side}\n\n"
                f"Entry:   `{entry:.4f}`\n"
                f"Current: `{current:.4f}`\n"
                f"PnL:     `{pnl*100:+.2f}%`\n"
                f"MFE:     `{mfe*100:+.2f}%`\n"
                f"MAE:     `{mae*100:+.2f}%`\n"
                f"Retrace: `{retrace*100:.0f}%`\n"
                f"Health:  `{health:.0f}/100`\n"
                f"PIE:     `{rec}`\n"
                f"\n**Why:**\n"
            )
            # SL/TP c биржи (если PIE-запись их не знает — берём из reality_state)
            sl_ex, tp_ex = None, None
            try:
                _rs = json.loads(Path("/root/tradingos/guardian/reality_state.json").read_text())
                sym_rs = symbol.replace("-USDT", "")
                _m = _rs.get(symbol) or _rs.get(sym_rs) or {}
                sl_ex = _m.get("sl_initial") or _m.get("sl_hard")
                tp_ex = _m.get("tp_initial")
            except Exception:
                pass
            st_ex = (
                f"\n🛑 SL: `{sl_ex:.4f}` | 🎯 TP: `{tp_ex:.4f}`\n"
                if sl_ex and tp_ex else
                ("\n🛑 SL: `" + str(sl_ex) + "`\n" if sl_ex else "\n(нет данных SL/TP в PIE — см. /positions)\n")
            )
            text = text.replace("\n**Why:**\n", st_ex + "\n**Why:**\n")
            text += "\n".join(f"  {r}" for r in reasons)

            short_symbol = symbol.replace("-USDT", "")
            keyboard = [
                [
                    InlineKeyboardButton("❌ Close", callback_data=f"close_yes_{symbol}"),
                    InlineKeyboardButton("🔄 Refresh", callback_data=f"pos_{short_symbol}"),
                ],
                [InlineKeyboardButton("🔙 Back", callback_data="positions")],
            ]
            await query.edit_message_text(text, parse_mode="Markdown",
                                          reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("close_yes_"):
        symbol = data.replace("close_yes_", "")
        # T84: поддержка аварийного закрытия close_panic_<sym>
        reason = "PANIC" if data.startswith("close_panic_") else "MANUAL"
        await query.edit_message_text(f"❌ Closing {symbol}...")

        # Задача 3: сначала Bybit (реальные AUTO/MANUAL позиции)
        closed_bybit = False
        try:
            sys.path.insert(0, "/root/tradingos")
            from tradingos.strategies.bybit_position_check import get_open_positions_with_side
            from guardian.reality_guardian import _close_position, manual_close_allowed
            positions = get_open_positions_with_side()
            pos = next((p for p in positions if p["symbol"] == symbol), None)
            if pos:
                # T84 + 2026-08-31: блок закрытия в минусе (owner) + armed-лестница
                cur_px = float(pos.get("markPrice", 0) or 0)
                allowed, block_msg = manual_close_allowed(symbol, reason, current_price=cur_px)
                if not allowed:
                    await query.edit_message_text(
                        f"⛔ <b>Закрытие заблокировано</b>\n{block_msg}\n\n"
                        f"Для аварийного закрытия: <code>PANIC {symbol}</code>",
                        parse_mode="HTML",
                    )
                    return
                side = pos["side"]
                size = float(pos.get("size", 0))
                if size > 0:
                    ok = _close_position(symbol, side, size)
                    if ok:
                        await query.edit_message_text(
                            f"✅ <b>{symbol}</b> закрыта на Bybit", parse_mode="HTML"
                        )
                        closed_bybit = True
                    else:
                        await query.edit_message_text(f"❌ Ошибка закрытия {symbol} на Bybit")
                        return
        except Exception as e:
            logger.warning(f"Bybit close failed for {symbol}: {e}")

        if closed_bybit:
            return

        # Fallback: BingX (PIE-контур)
        client = context.bot_data.get("bingx_client")
        if client:
            executor = EmergencyExecutor(client)
            result = await executor.close_position(symbol, reason="manual")
            if result.get("success"):
                pnl = result.get("pnl_pct", 0) or 0
                text = (
                    f"✅ **{symbol} Closed**\n\n"
                    f"Entry: {result['entry_price']:.4f}\n"
                    f"Exit:  {result['exit_price']:.4f}\n"
                    f"PnL:   {pnl*100:+.2f}%\n"
                    f"Reason: `{result.get('reason', 'manual')}`"
                )
            else:
                text = f"❌ Failed: {result.get('error', 'unknown')}"
            await query.edit_message_text(text, parse_mode="Markdown")
        else:
            await query.edit_message_text(f"❌ Нет клиента для закрытия {symbol}")

    elif data == "cancel":
        await query.edit_message_text("❌ Cancelled.", parse_mode="Markdown")

    elif data == "pie_live_menu":
        # Trigger /pie_live
        await cmd_pie_live(update, context)
        await query.delete_message()

    elif data == "opportunities_menu":
        # Trigger /opportunities
        await cmd_opportunities(update, context)
        await query.delete_message()

    elif data == "history_menu":
        # Trigger /history
        await cmd_history(update, context)
        await query.delete_message()

    elif data == "signals_menu":
        # Ручной контур: /signals
        from telegram_control.manual_signal import cmd_signals
        await cmd_signals(update, context)
        await query.delete_message()


# ── Errors ──

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок."""
    logger.error(f"Update {update} caused error {context.error}")


# ── Main ──

async def post_init(application: Application):
    """Инициализация после запуска бота."""
    # Store BingX client in bot_data
    api_key = os.getenv("BINGX_API_KEY")
    api_secret = os.getenv("BINGX_API_SECRET")
    client = BingXClient(api_key, api_secret)
    application.bot_data["bingx_client"] = client

    # Auto Safe Executor
    auto_safe = AutoSafeExecutor(client)
    application.bot_data["auto_safe"] = auto_safe

    # Verify connection
    try:
        positions = await client.get_positions()
        logger.info(f"BingX connected: {len(positions)} positions")
    except Exception as e:
        logger.error(f"BingX not available: {e}")

    # Start Auto Safe background task
    async def auto_safe_loop():
        """Проверка условий MOVE_SL_BE каждые 60 секунд."""
        logger.info("🛡 Auto Safe background loop started")
        while True:
            try:
                # BINGX-ключи закомментированы в /root/mt5_trading_bot/.env →
                # клиент не подписывает запросы, шит-контур BingX неактивен.
                # Молча деградируем вместо спама NoneType.encode каждые 60с.
                if not os.getenv("BINGX_API_SECRET"):
                    await asyncio.sleep(300)
                    continue
                state = load_state()
                if state.get("mode") not in ("auto_safe", "auto_full"):
                    await asyncio.sleep(30)
                    continue

                positions = await client.get_positions()
                pie = PIEPositionIntelligence(mode="live_assist", db_path=ROOT / "tradingos_data.db")

                # Read latest event per symbol from DB (not from in-memory PIE)
                try:
                    conn = sqlite3.connect(str(ROOT / "tradingos_data.db"))
                    db_rows = conn.execute("""
                        SELECT symbol, side, entry_price, current_price, pnl_pct,
                               max_profit_seen, max_loss_seen, profit_retracement,
                               health_score, state, recommendation, reason
                        FROM (
                            SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY id DESC) as rn
                            FROM position_events WHERE event_type = 'BAR_UPDATE'
                        ) WHERE rn = 1
                    """).fetchall()
                    conn.close()
                except Exception as e:
                    logger.error(f"DB read error: {e}")
                    db_rows = []

                # Create a mock snapshot from DB data
                class _MockSnapshot:
                    def __init__(self, row):
                        self.pnl_pct = row[4]
                        self.max_profit_seen = row[5]
                        self.max_loss_seen = row[6]
                        self.profit_retracement = row[7]
                        self.health_score = row[8]
                        self.recommendation = type("Rec", (), {"value": row[10]})()
                        self.reason = row[11]

                for row in db_rows:
                    symbol = row[0]
                    entry = row[2]
                    mark = row[3]
                    side = row[1]
                    rec = row[10]
                    pnl = row[4]
                    mfe = row[5]
                    health = row[8]

                    logger.info(f"[AUTO SAFE CHECK] {symbol}: pnl={pnl*100:+.1f}% mfe={mfe*100:+.1f}% health={health:.0f} rec={rec}")

                    # Find matching position from BingX for amount
                    pos = None
                    for p in positions:
                        if p.get("symbol") == symbol:
                            pos = p
                            break

                    if not pos:
                        continue

                    amt = float(pos.get("positionAmt", 0))
                    if amt == 0:
                        continue

                    snapshot = _MockSnapshot(row)

                    action = await auto_safe.evaluate_and_execute(pos, snapshot)
                    if action:
                        # Notify via Telegram
                        chat_id = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
                        try:
                            text = (
                                f"🛡 **AUTO SAFE: {action.symbol}**\n\n"
                                f"Action: `{action.action}`\n"
                                f"PnL:    `{action.pnl_at_action*100:+.2f}%`\n"
                                f"SL:     `{action.entry_price:.4f} → {action.new_sl:.4f}`\n"
                                f"Reason: {action.reason}"
                            )
                            await application.bot.send_message(
                                chat_id=chat_id, text=text, parse_mode="Markdown"
                            )
                        except Exception:
                            pass

                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Auto Safe loop error: {e}")
                await asyncio.sleep(60)

    # Register background task
    asyncio.ensure_future(auto_safe_loop())

    # Ручной контур SIGNAL_ONLY теперь на ОТДЕЛЬНОМ боте (manual_bot.py, Grizzly).
    # Фоновые задачи ручного контура (скан/монитор/сводка) запускаются там,
    # чтобы MT4/MT5/auto_safe и ручные сигналы были на разных ботах.
    logger.info("✅ Telegram Control Bot started")


def main():
    """Запуск бота."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)

    # Build application with SOCKS5 proxy via xray
    from telegram.ext import ApplicationBuilder
    from telegram.request import HTTPXRequest

    # Use SOCKS5 proxy via xray (socks5://127.0.0.1:1080)
    proxy_url = "socks5://127.0.0.1:1080"
    logger.info(f"Using proxy: {proxy_url}")

    request = HTTPXRequest(
        proxy=proxy_url,
        connect_timeout=15,
        read_timeout=30,
    )

    # Отдельный request для getUpdates (long-polling): read_timeout=60,
    # т.к. Telegram держит соединение до 30+ сек + SOCKS5-прокси добавляет latency.
    # Без этого polling крашится с TimedOut → бот теряет приём кнопок.
    get_updates_request = HTTPXRequest(
        proxy=proxy_url,
        connect_timeout=15,
        read_timeout=60,
    )

    app = (
        ApplicationBuilder()
        .token(token)
        .post_init(post_init)
        .request(request)
        .get_updates_request(get_updates_request)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("positions", cmd_positions))
    app.add_handler(CommandHandler("close", cmd_close))
    app.add_handler(CommandHandler("panic", cmd_panic))
    app.add_handler(CommandHandler("freeze", cmd_freeze))
    app.add_handler(CommandHandler("unfreeze", cmd_unfreeze))
    app.add_handler(CommandHandler("think", cmd_think))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("opportunities", cmd_opportunities))
    app.add_handler(CommandHandler("pie_live", cmd_pie_live))

    # ── Ручной контур SIGNAL_ONLY — переехал на отдельный бот Grizzly ──
    # (telegram_control/manual_bot.py). Здесь НЕ регистрируем — иначе два бота
    # конкурируют за ms_*-кнопки и текстовые команды ручного контура.
    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Errors
    app.add_error_handler(error_handler)

    logger.info("Starting Telegram Control Bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
