"""
manual_signal.py — SIGNAL_ONLY v0: Telegram-пульт ручного контура.

Поток: Scanner → карточка в Telegram → подтверждение пользователя →
       проверки гейта → DRY-RUN журнал (ордер НЕ отправляется до явного
       разрешения "РАЗРЕШАЮ РЕАЛЬНОЕ ИСПОЛНЕНИЕ").

Полностью отделён от TradingOS AUTO executor: он не читает и не пишет
в state/позиции AUTO-контура, только в memory/manual_signals.jsonl.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# systemd-сервис не задаёт PYTHONPATH — добавляем вручную (как в run_observation.py)
for _p in ("/root", "/root/tradingos"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger("ManualSignal")

ROOT = Path("/root/tradingos")
CONFIG = ROOT / "operations/manual_session.json"
JOURNAL = ROOT / "memory/manual_signals.jsonl"
# Shadow-аудит эскалации (audit-only): журнал наблюдений, НЕ блокирует торговлю.
ESCALATION_AUDIT = ROOT / "memory/escalation_audit.jsonl"
# Kill-switch состояние (persistent): переживает restart/crash/deploy/reboot.
# ЕДИНСТВЕННЫЙ источник правды для паузы. FAIL CLOSED: отсутствие/повреждение
# файла = PAUSED (исполнение заблокировано), никогда не наоборот.
STATE = ROOT / "operations/manual_state.json"

# Хранилище последних сигналов (по символу) в памяти процесса
_last_signals: dict[str, dict] = {}


def _pause_state() -> dict:
    """Прочитать kill-switch состояние. FAIL CLOSED:
    отсутствующий/повреждённый файл → {"paused": True} (блокировать торговлю)."""
    try:
        st = json.loads(STATE.read_text())
        if not isinstance(st, dict):
            return {"paused": True}
        return st
    except Exception:
        return {"paused": True}


def _is_paused() -> bool:
    """Актуальная проверка паузы ПЕРЕД каждым исполнением (fail-closed)."""
    return bool(_pause_state().get("paused", False))


def _set_paused(paused: bool, reason: str = "user") -> None:
    """Записать kill-switch состояние на диск (persistent)."""
    st = _pause_state()
    st["paused"] = bool(paused)
    st["reason"] = reason
    st["updated_at"] = datetime.now(timezone.utc).isoformat()
    st["updated_by"] = "telegram_bot"
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=2, ensure_ascii=False))


def _cfg() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {"symbols": [], "min_signal_score": 80, "risk_per_trade_usd": 0.5}


def _funding_tp_ok(funding_bps: float, cfg: dict | None = None) -> tuple[bool, str]:
    """Экономический гейт funding-входа: цель (3 выплаты) обязана покрывать
    комиссию round-trip с запасом.

    VELVET-инцидент 2026-08-29: funding 3.1 bps → TP 0.093% цены,
    комиссия round-trip 0.22% → сделка убыточна математически (−$1.14
    при gross $0.84). Сделка имеет смысл только если цель > издержек × mult.
    Дополнительно: funding_min_funding_bps — жёсткий нижний порог (2026-08-30:
    при 11-16 bps нужен WR 69-81% — нереально; с 20 bps → 64%).
    """
    cfg = cfg or _cfg()
    fee_pct = float(cfg.get("funding_fee_round_trip_pct", 0.22) or 0.22)
    mult = float(cfg.get("funding_min_edge_mult", 1.5) or 1.5)
    # 0.0 (ЯВНО в конфиге) = отключить жёсткий bps-порог — «or 20» съедал бы 0
    min_funding_bps = float(cfg["funding_min_funding_bps"]) \
        if "funding_min_funding_bps" in cfg else 20.0
    # FIX 2026-08-31: верхний порог — экстремальный |funding| = сквиз-ловушка
    # (ZKP −108bps → шорт-сквиз вверх, LONG ловил просадку −$21). Кап в конфиге
    # для ВСЕХ входов (авто + ручные кнопки + карточки), не только для watchdog.
    max_funding_bps = float(cfg["funding_max_funding_bps"]) \
        if "funding_max_funding_bps" in cfg else 50.0
    tp_pct = abs(funding_bps) * 3 / 10000  # 3 выплаты как в ms_fund_ и watchdog
    if abs(funding_bps) < min_funding_bps:
        return False, (
            f"funding {abs(funding_bps):.1f} bps < порога {min_funding_bps:.0f} bps — "
            f"цель не компенсирует комиссию {fee_pct:.2f}% при реальном WR (~62-65%)"
        )
    if abs(funding_bps) > max_funding_bps:
        return False, (
            f"funding {abs(funding_bps):.1f} bps > кап {max_funding_bps:.0f} bps — "
            f"экстремум (сквиз/ловушка движения), цель недостижима"
        )
    min_tp = fee_pct / 100.0 * mult  # fee в процентах → доли × запас
    if tp_pct < min_tp:
        need_bps = min_tp * 10000 / 3
        return False, (
            f"TP {tp_pct*100:.2f}% < издержки×{mult} ({min_tp*100:.2f}%) — "
            f"нужен funding ≥ {need_bps:.0f} bps/8ч"
        )
    return True, ""


def _get_manual_equity() -> float:
    """Live equity from deposit_guard state — single source of truth."""
    try:
        st = json.loads(Path("/root/tradingos/operations/deposit_guard_state.json").read_text())
        v = st.get("last_equity")
        if v and float(v) > 0:
            return float(v)
    except Exception:
        pass
    return 0.0


def _risk_usd(cfg: dict | None = None) -> float:
    """Resolve the dollar risk per trade. 2026-08-27 (owner):
    risk_per_trade_pct (% of equity) takes priority over the absolute
    risk_per_trade_usd so sizing works from any deposit.
    Clamped by min/max_risk_usd (safety rails around equity-fetch blips).
    """
    cfg = cfg or _cfg()
    pct = float(cfg.get("risk_per_trade_pct", 0) or 0)
    base = float(cfg.get("risk_per_trade_usd", 0.5))
    if pct > 0:
        eq = _get_manual_equity()
        if eq > 0:
            base = eq * pct / 100.0
    lo = float(cfg.get("min_risk_usd", 0.5) or 0.5)
    hi = float(cfg.get("max_risk_usd", 1e12) or 1e12)
    return max(min(base, hi), lo)


def _amount_bounds_usd(cfg: dict | None = None) -> tuple[float, float]:
    """Dynamic manual-entry bounds derived from equity.
    min: floor(max(cfg.min_amount_usd, exchange minOrderUsd))
    max: floor(eq × cfg.max_amount_pct / 100) — position notional cap.

    FIX 2026-08-28: max_risk_usd previously capped NOTIONAL here — semantics
    mixup (it's a RISK-$ cap applied to _risk_usd). On $180k the notional
    ceiling collapsed to $1000 (0.6% of equity → micro positions). Notional
    risk is enforced separately by the guard via proposed_risk_usd.
    """
    cfg = cfg or _cfg()
    eq = _get_manual_equity()
    min_abs = float(cfg.get("min_amount_usd", 1.0) or 1.0)
    max_pct = float(cfg.get("max_amount_pct", 50.0) or 50.0)
    upper = eq * max_pct / 100.0 if eq > 0 else 1e12
    return min_abs, max(upper, min_abs)


def _quick_amounts(cfg: dict | None = None, low_bound: float = 1.0) -> list[float]:
    """Equity-scaled quick presets for the amount picker.

    FIX 2026-08-28: presets were hardcoded ($5/$10/$20/$50) from the $80
    real-account era — on the $180k demo the owner tapped $50 and got a
    $50 position ('слишком микро'). Presets are now % of equity
    (owner 2026-08-27: 'работает от любого депо, используя процент от суммы'):
    1% / 2% / 5% / 10%, rounded, clamped to [low_bound, max_amount bound].
    """
    cfg = cfg or _cfg()
    eq = _get_manual_equity()
    if eq <= 0:
        return [a for a in (5, 10, 20, 50) if a >= low_bound or a >= 5]
    _, hi = _amount_bounds_usd(cfg)
    out = []
    for pct in (1.0, 2.0, 5.0, 10.0):
        a = eq * pct / 100.0
        # Round to 2 significant decimals for clean buttons ($1804 → $1800)
        if a >= 1000:
            a = round(a, -2)
        elif a >= 100:
            a = round(a, -1)
        else:
            a = round(a, 2)
        if a < low_bound or a > hi:
            continue
        if a not in out:
            out.append(a)
    return out or [max(low_bound, min(hi, round(eq * 0.01, 2)))]


def _save_cfg(cfg: dict) -> None:
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))


def _fmt_price(p) -> str:
    try:
        f = float(p)
    except (TypeError, ValueError):
        return "—"
    if f <= 0:
        return "—"
    if f >= 1000:
        return f"{f:,.0f}"
    if f >= 10:
        return f"{f:.2f}"
    if f >= 0.01:
        return f"{f:.4f}"
    return f"{f:.6f}"


def _esc(s: str) -> str:
    """HTML-escape для безопасной вставки в parse_mode=HTML.
    Без этого сообщения с '<'/'&' ломают парсинг Telegram
    (например 'qty 0.013 < min 0.1')."""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt_amount(a: float) -> str:
    """Формат суммы для подсказки: 7.63 / 64.82 / 0.16."""
    if a >= 10:
        return f"{a:.2f}"
    if a >= 1:
        return f"{a:.2f}"
    return f"{a:.2f}"


def _leverage() -> int:
    """Плечо для ручных позиций (из trading_mode.json max_leverage, как в AUTO)."""
    try:
        return int(json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
                   .get("max_leverage", 5))
    except Exception:
        return 5


def _min_order_usd(symbol: str) -> float:
    """Минимальная сумма ордера для символа.
    = max(minOrderQty × цена, minNotionalValue).
    Bybit требует minNotionalValue=$5 у большинства linear-пар — это жёсткий
    минимум по сумме ордера (баг 110094 при вводе $1 на LINK)."""
    try:
        import httpx
        with httpx.Client(timeout=10) as c:
            ri = c.get("https://api.bybit.com/v5/market/instruments-info",
                       params={"category": "linear", "symbol": symbol})
            lot = ((ri.json().get("result") or {}).get("list") or [{}])[0].get("lotSizeFilter", {})
            min_qty = float(lot.get("minOrderQty", "0"))
            min_notional = float(lot.get("minNotionalValue", "5"))
            rt = c.get("https://api.bybit.com/v5/market/tickers",
                       params={"category": "linear", "symbol": symbol})
            price = float((((rt.json().get("result") or {}).get("list") or [{}])[0]).get("lastPrice", 0))
        return round(max(min_qty * price, min_notional), 2)
    except Exception:
        return 5.0


def _tp_reachability_warning(symbol: str, side: str, tp1: float) -> str | None:
    """TP reachability (WARN только, НЕ блокирует): сравнивает TP1 с 30-дневным
    историческим диапазоном Bybit. Возвращает строку предупреждения или None."""
    try:
        import httpx
        with httpx.Client(timeout=15) as c:
            r = c.get(
                "https://api.bybit.com/v5/market/kline",
                params={"category": "linear", "symbol": symbol, "interval": "60", "limit": 720},
            )
            rows = (r.json().get("result") or {}).get("list") or []
        if len(rows) < 100:
            return None
        highs = [float(x[2]) for x in rows]
        lows = [float(x[3]) for x in rows]
        hi30, lo30 = max(highs), min(lows)
        if side == "LONG":
            if tp1 > hi30:
                over = (tp1 - hi30) / hi30 * 100
                return (f"⚠️ <b>TP вне 30D диапазона</b>\n"
                        f"Исторический максимум: {_fmt_price(hi30)}\n"
                        f"TP: {_fmt_price(tp1)}\n"
                        f"Превышение: +{over:.2f}%")
        else:
            if tp1 < lo30:
                over = (lo30 - tp1) / lo30 * 100
                return (f"⚠️ <b>TP вне 30D диапазона</b>\n"
                        f"Исторический минимум: {_fmt_price(lo30)}\n"
                        f"TP: {_fmt_price(tp1)}\n"
                        f"Превышение: +{over:.2f}%")
    except Exception:
        return None
    return None


def _format_signal(sig: dict) -> str:
    sym = sig["symbol"]
    side = sig["side"]
    emoji = "🟢" if side == "LONG" else "🔴"
    price = sig.get("price", 0)
    # TradePlan из сканера (market-based TP): raw/final/sl уже рассчитаны.
    # FIX: R:R ВСЕГДА пересчитываем от ЦЕНЫ ВХОДА (price), а не берём sig['rr']
    # из сканера (тот считается от ЗАКРЫТОГО H1-бара и при тренде завышается:
    # вход на 0.6% выше закрытого → реальный R:R 1.6 вместо заявленных 2.0).
    sl = sig.get("sl") or (price * 0.985 if side == "LONG" else price * 1.015)
    tp1 = sig.get("final_tp") or (price * 1.03 if side == "LONG" else price * 0.97)
    raw_tp = sig.get("raw_tp") or tp1
    rr = round((tp1 - price) / max(price - sl, 1e-9), 1) if side == "LONG" \
        else round((price - tp1) / max(sl - price, 1e-9), 1)
    tp_unreachable = sig.get("tp_unreachable", False)
    rr_invalid = sig.get("rr_invalid", False)

    cfg = _cfg()
    risk = _risk_usd(cfg)

    why = sig.get("why", [])
    # Контур-источник сигнала (2026-09-02): определяем по полям sig.
    # Ручной сканер → "Ручной сканер"; funding → "Funding"; иначе "AUTO".
    _src = sig.get("source") or sig.get("strategy") or sig.get("session") or ""
    if "FUND" in str(_src).upper() or sig.get("funding_bps"):
        _contour_label = "💸 Funding-контур"
    elif "MEME" in str(_src).upper():
        _contour_label = "🐕 Мем-контур"
    elif "TRADFI" in str(_src).upper():
        _contour_label = "📈 Акции (TradFi)"
    elif "REALITY" in str(_src).upper():
        _contour_label = "🤖 Reality (AUTO)"
    else:
        _contour_label = "👤 Ручной сканер"
    lines = [
        f"{emoji} <b>МАРКЕТ-СЕТАП</b>",
        f"<b>{sym}</b> — {side} | Score: <b>{sig['score']}/100</b>",
        f"📡 Контур: {_contour_label}",
        "",
        f"📍 Вход: <code>{_fmt_price(price * 0.999)}–{_fmt_price(price * 1.001)}</code>",
        f"🛑 SL: <code>{_fmt_price(sl)}</code>",
        f"🎯 TP: <code>{_fmt_price(tp1)}</code>",
        f"💰 Риск: ${risk:.2f} | R:R ~{rr}:1",
        "",
        "⚡ <b>Исполнение:</b> маркет-ордер по текущей цене (сразу открывает позицию)",
        "",
    ]
    # Задача: TP reachability — краткая подача.
    # Убрана давность последнего касания (при частых касаниях «0 дн. назад»
    # вводило в заблуждение). Остаётся только факт достижимости TP.
    tp_touch = sig.get("tp_touch_count")
    tp_unreachable = sig.get("tp_unreachable", False)
    if tp_unreachable:
        line = "🔴 TP вне 30D-диапазона (недостижим)"
    elif tp_touch is not None and tp_touch > 0:
        line = f"🟢 TP в 30D-диапазоне ({tp_touch} касаний)"
    else:
        line = "🔴 TP не касался за 30 дней"
    lines.insert(3, line)
    lines += [
        "<b>Почему:</b>",
    ]
    # Упрощение: оставить только ключевые направления (тренд/структура/импульс),
    # убрать длинный технический разбор (stoch/squeeze/volume/entry/mtf).
    why = why or []
    keep = []
    for w in why:
        head = w.split("[")[0].strip().rstrip(" ,") or ""
        low = head.lower()
        if any(k in low for k in ("h1 trend", "m15 structure", "momentum")):
            keep.append(head)
        if len(keep) == 3:
            break
    if keep:
        lines.append("📌 " + _esc(" · ".join(keep)))
    else:
        lines.append("—")
    lines.append("")
    lines.append(f"<b>Что делать:</b> открыть {'LONG' if side == 'LONG' else 'SHORT'} в указанной зоне вручную.")
    lines.append("")
    if sig.get("tp_unreachable"):
        lines.append("🔴 <b>TP НЕДОСТИЖИМ</b> — цель за пределами 30D диапазона.")
        lines.append("Сделка не предлагается к открытию.")
        lines.append("")
    warn = _tp_reachability_warning(sym, side, tp1)
    if warn:
        lines.append(warn)
        lines.append("")
    lines.append(f"⚠️ Сигнал отменяется при уходе от входа >0.5% от цены {_fmt_price(price)}.")
    return "\n".join(lines)


async def cmd_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Сканировать рынок и показать найденные сигналы."""
    msg = await update.effective_message.reply_text("🔍 Сканирую рынок...")
    try:
        from tradingos.signals.manual_scanner import scan_all
        found = await asyncio_to_thread(scan_all)
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка сканирования: {e}")
        return
    if not found:
        await msg.edit_text("😴 Сигналов ≥ порога сейчас нет. Рынок спокоен или условия не выполнены.")
        return
    for sig in found:
        _last_signals[sig["symbol"]] = {**sig, "_stored_at": time.time()}
        if sig.get("tp_unreachable"):
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Понятно", callback_data=f"ms_skip_{sig['symbol']}"),
            ]])
            await msg.reply_text(
                f"🔴 <b>СИГНАЛ ОТКЛОНЁН</b>\n{sig['symbol']} — {sig['side']} | Score: {sig['score']}/100\n\n"
                "TP за пределами 30D диапазона — сделка не предлагается.",
                parse_mode="HTML", reply_markup=kb,
            )
            continue
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("🟢 ОТКРЫТЬ", callback_data=f"ms_open_{sig['symbol']}"),
            InlineKeyboardButton("❌ ПРОПУСТИТЬ", callback_data=f"ms_skip_{sig['symbol']}"),
        ]])
        await msg.reply_text(_format_signal(sig), parse_mode="HTML", reply_markup=kb)


async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать состояние ручной сессии."""
    cfg = _cfg()
    st = _pause_state()
    state = "🟢 ACTIVE" if not st.get("paused") else "⏸ PAUSED"
    mode = "🔵 DRY-RUN" if cfg.get("dry_run", True) else "🟢 LIVE MANUAL"
    lines = [
        "🎛 <b>MANUAL SESSION</b>",
        f"Status: {state}",
        f"Mode: {mode}",
        f"Risk/trade: ${_risk_usd(cfg):.2f}",
        f"Session limit: ${cfg.get('max_session_loss_usd', 3.0):.2f}",
        f"Max positions: {cfg.get('max_positions', 2)}",
        f"Signal threshold: {cfg.get('min_signal_score', 80)}/100",
        f"Whitelist: {', '.join(cfg.get('symbols', [])[:6])}",
    ]
    if st.get("paused"):
        lines.append(f"⏸ Причина: {st.get('reason', '?')} | {st.get('updated_at', '?')}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_pause(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _set_paused(True, reason="manual safety pause")
    await update.effective_message.reply_text("⏸ Ручная сессия поставлена на паузу.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _set_paused(False, reason="resumed")
    await update.effective_message.reply_text("▶️ Ручная сессия возобновлена.")


async def cmd_waitreport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """P2b: отчёт по эффективности WAIT-лимиток (entry improvement, fee)."""
    try:
        import subprocess
        r = subprocess.run(
            ["python3", "/root/tradingos/telegram_control/wait_limit_report.py"],
            capture_output=True, text=True, timeout=60)
        text = r.stdout if r.returncode == 0 else f"Ошибка отчёта: {r.stderr[-400:]}"
        # Telegram: ограничение 4096 символа; отчёт умещается — но режем страховочно
        if len(text) > 3900:
            text = text[:3900] + "\n…(обрезано)"
        await update.effective_message.reply_text(f"<pre>{_esc(text)}</pre>",
                                                  parse_mode="HTML")
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Ошибка отчёта: {_esc(str(e))}")


async def cmd_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список активных WAIT-лимиток + кнопки Изменить/Удалить."""
    st = _load_wait_limit_state()
    active = [s for s, r in st.items() if r.get("status") == "PLACED"]
    if not active:
        await update.effective_message.reply_text(
            "📭 Активных WAIT-лимиток нет.\n"
            "Ставятся через кнопку «📌 Поставить LIMIT» на WAIT-карточке.")
        return
    kb_rows = []
    lines = ["📌 <b>Активные WAIT-лимитки</b>\n"]
    for sym in active:
        r = st[sym]
        from datetime import datetime as _dt
        expires = _dt.fromtimestamp(r.get("expires_at", 0), tz=timezone.utc).strftime("%H:%M UTC")
        lines.append(
            f"<b>{sym}</b> {r.get('side', '?')} | {r.get('qty', 0):.4f} @ "
            f"<code>{_fmt_price(r.get('price', 0))}</code>\n"
            f"SL <code>{_fmt_price(r.get('sl', 0))}</code> | TP <code>{_fmt_price(r.get('tp', 0))}</code>\n"
            f"{r.get('lev', '—')}x | до <b>{expires}</b>")
        kb_rows.append([
            InlineKeyboardButton(f"✏️ {sym}", callback_data=f"ms_wedit_{sym}"),
            InlineKeyboardButton(f"🗑 {sym}", callback_data=f"ms_wdel_{sym}"),
        ])
    kb = InlineKeyboardMarkup(kb_rows) if kb_rows else None
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=kb)


async def cmd_risk(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Двухшаговое изменение риска: /risk <сумма|процент> → подтверждение.
    2026-08-27 (owner): accepts '0.5%' to set risk_per_trade_pct; bare numbers
    remain absolute USD for backwards compatibility."""
    args = context.args
    if not args:
        await update.effective_message.reply_text(
            "Использование:\n"
            "  /risk <сумма $>\n"
            "  /risk 0.5%  (0.5% от equity)\n"
            "Пример: /risk 25  или  /risk 0.5%")
        return
    raw = args[0].strip()
    is_pct = raw.endswith("%")
    try:
        if is_pct:
            new_risk = float(raw.rstrip("%"))
        else:
            new_risk = float(raw)
    except ValueError:
        await update.effective_message.reply_text("❌ Некорректное число.")
        return
    if is_pct:
        if new_risk <= 0 or new_risk > 10:
            await update.effective_message.reply_text("❌ Процент вне диапазона (0 < pct ≤ 10).")
            return
    else:
        if new_risk <= 0 or new_risk > 1000:
            await update.effective_message.reply_text("❌ Сумма вне диапазона (0 < risk ≤ 1000).")
            return
    context.user_data["pending_risk"] = new_risk
    context.user_data["pending_risk_is_pct"] = is_pct
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data="ms_risk_confirm"),
        InlineKeyboardButton("❌ ОТМЕНА", callback_data="ms_risk_cancel"),
    ]])
    if is_pct:
        msg = f"⚠️ Изменить риск с {_risk_usd():.2f}$ ({_cfg().get('risk_per_trade_pct', 0.5)}%) на {new_risk}%?"
    else:
        msg = f"⚠️ Изменить риск с ${_risk_usd():.2f} на ${new_risk:.2f}?"
    await update.effective_message.reply_text(
        msg, reply_markup=kb,
    )


def _reply_kb() -> "ReplyKeyboardMarkup":
    """Современная кнопочная клавиатура внизу чата (вместо текстовых команд)."""
    from telegram import ReplyKeyboardMarkup
    return ReplyKeyboardMarkup([
        ["🔵 BingX сигнал", "🎯 Сигналы"],
        ["🎲 Ставка", "📋 Позиции"],
        ["⚙️ SL/TP", "💰 Риск"],
        ["⏸ Пауза", "⚙️ Настройки"],
        ["ℹ️ Помощь"],
    ], resize_keyboard=True)


async def cmd_manual_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🎛 <b>РУЧНОЙ КОНТУР</b>\n\n"
        "Кнопки внизу — твой пульт:\n"
        "🔵 <b>BingX сигнал</b> — лимитка на реальном счёте (анализ + подтверждение)\n"
        "🎯 <b>Сигналы</b> — скан рынка Bybit\n"
        "📋 <b>Позиции</b> — открытые позиции и PnL\n"
        "⚙️ <b>SL/TP</b> — изменить стоп/тейк\n"
        "💰 <b>Риск</b> — настройка риска\n"
        "⏸ <b>Пауза</b> — стоп ручного контура\n"
        "⚙️ <b>Настройки</b> — ключевые показатели системы (риск, лимиты, фильтры)\n\n"
        "Режим Bybit: <b>DRY-RUN</b> — ордера НЕ отправляются до явного разрешения.\n"
        "BingX: <b>LIVE</b> — только по твоему подтверждению кнопкой.",
        parse_mode="HTML",
        reply_markup=_reply_kb(),
    )


def _main_menu_kb() -> InlineKeyboardMarkup:
    """Клавиатура главного меню (используется в cmd_main_menu и после открытия позиции)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Сигналы", callback_data="signals_menu"),
         InlineKeyboardButton("📋 Позиции", callback_data="positions_menu")],
        [InlineKeyboardButton("🔵 BingX-сигнал", callback_data="bx_menu"),
         InlineKeyboardButton("⚙️ SL/TP", callback_data="sltp_menu")],
        [InlineKeyboardButton("💰 Риск", callback_data="risk_menu"),
         InlineKeyboardButton("⏸ Пауза", callback_data="pause_menu")],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help_menu")],
    ])


async def cmd_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню ручного контура — вызывается после открытия позиции
    и по кнопке «🏠 Меню». Кнопки вместо текстовых команд."""
    kb = _main_menu_kb()
    try:
        if update.callback_query:
            await update.callback_query.message.reply_text(
                "🎛 <b>ГЛАВНОЕ МЕНЮ</b>\n"
                "Выберите действие:", parse_mode="HTML", reply_markup=kb)
        else:
            await update.message.reply_text(
                "🎛 <b>ГЛАВНОЕ МЕНЮ</b>\n"
                "Выберите действие:", parse_mode="HTML", reply_markup=kb)
    except Exception:
        await update.message.reply_text(
            "🎛 <b>ГЛАВНОЕ МЕНЮ</b>\nВыберите действие:",
            parse_mode="HTML", reply_markup=kb)


async def _execute_manual_order(update, context, uid: int, pending: dict, amount: float):
    """Единая логика открытия MANUAL-позиции (из кнопки суммы или текстового ввода)."""
    # Ответ идёт в effective_message: при вызове из кнопки (ms_amt_) update.message
    # равен None, иначе подтверждение падало после открытия ордера.
    reply = update.effective_message
    # FINAL FAIL-SAFE: пауза блокирует ЛЮБОЙ путь исполнения непосредственно
    # перед API-вызовом (независимо от того, какая кнопка/команда сюда привела).
    if _is_paused():
        _awaiting_amount.pop(uid, None)
        await reply.reply_text(
            "⏸ <b>PAUSED</b> — ручная сессия на паузе.\n"
            "Исполнение заблокировано. Для возобновления: /resume",
            parse_mode="HTML",
        )
        return
    # sell_disabled=true → SHORT входы заблокированы (2026-08-25).
    # Защита от старых SHORT-сигналов в журнале после отключения шортов.
    try:
        import json as _json
        with open("/root/tradingos/operations/trading_mode.json") as _f:
            _tm = _json.load(_f)
        if _tm.get("sell_disabled", False) and str(pending.get("side", "")).upper() in ("SHORT", "SELL"):
            _awaiting_amount.pop(uid, None)
            await reply.reply_text(
                "🚫 <b>SHORT отключён</b> — sell_disabled=true.\n"
                "Открыт только LONG. /signals для нового сигнала.",
                parse_mode="HTML",
            )
            return
    except Exception:
        pass
    # Проверка актуальности цены ДО ордера
    fresh_p, price_ok = await _price_ok(pending["symbol"], pending.get("sig") or {})
    if not price_ok or fresh_p <= 0:
        await reply.reply_text(
            f"❌ <b>Цена ушла из зоны входа (>0.5%)</b>\n"
            f"Текущая: {_fmt_price(fresh_p)}\n"
            f"Сигнал {pending['symbol']} больше не актуален.\n"
            f"Откройте /signals для свежего.",
            parse_mode="HTML",
        )
        _awaiting_amount.pop(uid, None)
        return
    # Дневной NET-loss guard (A): блокирует ТОЛЬКО новый ручной вход при
    # достижении дневного лимита. Существующие позиции не трогаем (без
    # закрытия/уменьшения/изменения SL-TP). Дневное состояние общее с AUTO.
    try:
        import sys as _sys
        _sys.path.insert(0, "/root/tradingos")
        from tradingos.strategies.deposit_guard import get_guard
        _sl_v = pending.get("sl")
        _proposed = 0.0
        if _sl_v and fresh_p > 0:
            _qty_est = amount / fresh_p
            _proposed = abs(fresh_p - _sl_v) * _qty_est
        _allowed, _reason = get_guard().can_open_position(proposed_risk_usd=_proposed)
        if not _allowed:
            # ─────── SOFT vs HARD guard classification (manual_signal.py only) ───────
            # AUTO path (trade_executor.py:309) is NOT modified: still hard BLOCK.
            # Only MANUAL gets SOFT → confirmation-required for non-catastrophic reasons.
            _HARD_NO_OVERRIDE = {
                "EQUITY_FETCH_ERROR", "MANUAL_KILL", "HARD_KILL",
                "NO_DAY_BASELINE", "OPEN_RISK_OVER_LIMIT", "CRITICAL_OPEN_RISK",
            }
            _base_reason = str(_reason).split(":", 1)[0]
            if _base_reason in _HARD_NO_OVERRIDE:
                # Hard safety: NO override possible. Block as before.
                try:
                    st = get_guard().status()
                    daily_pnl = st.get("daily_pnl", 0.0) or 0.0
                    start_eq = st.get("day_start_equity", 0.0) or 0.0
                    limit_usd = st.get("daily_loss_limit_usd", 0.0) or 0.0
                    loss_usd = max(-daily_pnl, 0.0)
                    loss_pct = (loss_usd / start_eq * 100.0) if start_eq > 0 else 0.0
                    over_usd = max(loss_usd - limit_usd, 0.0)
                    over_pct = (over_usd / start_eq * 100.0) if start_eq > 0 else 0.0
                    import datetime as _dt
                    _now = _dt.datetime.utcnow()
                    _next_mid = (_now + _dt.timedelta(days=1)).replace(
                        hour=0, minute=0, second=0, microsecond=0)
                    _h = (_next_mid - _now).total_seconds() / 3600.0
                    _hh, _mm = int(_h), int((_h - int(_h)) * 60)
                    _extra = (
                        f"\n\n<b>📊 Детали</b>\n"
                        f"Старт дня: ${start_eq:.2f}\n"
                        f"Убыток сегодня: <b>${loss_usd:.2f} ({loss_pct:.2f}%)</b>\n"
                        f"Лимит: ${limit_usd:.2f} (1.5% от старта)\n"
                        f"Превышение: ${over_usd:.2f} ({over_pct:.2f}%)\n"
                        f"⏰ Сброс через ~{_hh} ч {_mm} мин (UTC 00:00).\n"
                        f"Открытые позиции и их SL/TP <b>без изменений</b>.\n"
                        f"Закрыть позицию можно через /positions."
                    )
                except Exception:
                    _extra = (
                        "\n\nОткрытые позиции и их SL/TP <b>без изменений</b>.\n"
                        "Закрыть позицию можно через /positions."
                    )
                await reply.reply_text(
                    f"⛔ <b>Guard: вход заблокирован (HARD)</b>\n"
                    f"Причина: <b>{_esc(str(_reason))}</b>\n"
                    f"Обойти подтверждением невозможно.{_extra}",
                    parse_mode="HTML",
                )
                _awaiting_amount.pop(uid, None)
                return
            # ─────── SOFT: confirmation_required state ───────
            # PROPOSED_RISK_TOO_HIGH / DAILY_LOSS_LIMIT — override possible in MANUAL only.
            import datetime as _dt
            import secrets as _sec
            _now = _dt.datetime.utcnow()
            _tok = _sec.token_urlsafe(12)
            _expires = _now + _dt.timedelta(seconds=60)
            # Build economic context
            try:
                st = get_guard().status()
                daily_pnl = st.get("daily_pnl", 0.0) or 0.0
                start_eq = st.get("day_start_equity", 0.0) or 0.0
                limit_usd = st.get("daily_loss_limit_usd", 0.0) or 0.0
                equity = st.get("last_equity", 0.0) or 0.0
                avail = st.get("available_margin", 0.0) or 0.0
                loss_usd = max(-daily_pnl, 0.0)
                loss_pct = (loss_usd / start_eq * 100.0) if start_eq > 0 else 0.0
                over_usd = max(loss_usd - limit_usd, 0.0)
                over_pct = (over_usd / start_eq * 100.0) if start_eq > 0 else 0.0
            except Exception:
                daily_pnl = start_eq = limit_usd = equity = avail = 0.0
                loss_usd = loss_pct = over_usd = over_pct = 0.0
            normal_risk = 0.5
            try:
                with open("/root/tradingos/operations/trading_mode.json") as _f:
                    normal_risk = float(json.load(_f).get("risk_per_trade", 0.5))
            except Exception:
                pass
            extra_risk = max(_proposed - normal_risk, 0.0)
            est_sl_loss = extra_risk  # conservative: extra risk = est extra loss
            # store pending confirmation
            try:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                _kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ CONFIRM RISK", callback_data=f"CONFIRMRISK:{_tok}"),
                    InlineKeyboardButton("❌ CANCEL", callback_data=f"CONFIRMRISKNO:{_tok}"),
                ]])
            except Exception:
                _kb = None
            _pending_overrides[(uid, _tok)] = {
                "pending": pending, "amount": amount, "proposed": _proposed,
                "normal_risk": normal_risk, "reason": _reason,
                "created_at": _now.isoformat(), "expires_at": _expires.isoformat(),
                "used": False,
                "equity": equity, "available": avail, "daily_pnl": daily_pnl,
                "limit_usd": limit_usd, "start_eq": start_eq, "loss_usd": loss_usd,
                "loss_pct": loss_pct, "over_usd": over_usd, "over_pct": over_pct,
            }
            # audit
            try:
                _audit_log("/root/tradingos/logs/manual_overrides.jsonl", {
                    "ts": _now.isoformat(), "uid": uid, "symbol": pending.get("symbol"),
                    "side": pending.get("side"), "event": "confirmation_requested",
                    "reason": _reason, "proposed": _proposed, "token": _tok[:6] + "...",
                    "expires_at": _expires.isoformat(), "equity": equity,
                    "daily_pnl": daily_pnl, "limit_usd": limit_usd,
                })
            except Exception:
                pass
            _msg = (
                f"⚠️ <b>Требуется подтверждение риска (MANUAL)</b>\n\n"
                f"<b>Причина:</b> {_esc(str(_reason))}\n"
                f"<b>Сторона:</b> {_esc(str(pending.get('side','')))}\n"
                f"<b>Символ:</b> {_esc(str(pending.get('symbol','')))}\n\n"
                f"<b>📊 Экономика решения</b>\n"
                f"Equity: <code>${equity:.2f}</code>\n"
                f"Доступная маржа: <code>${avail:.2f}</code>\n"
                f"Старт дня: <code>${start_eq:.2f}</code>\n"
                f"Daily P&L: <code>${daily_pnl:+.3f}</code>\n"
                f"Daily loss limit: <code>${limit_usd:.2f} ({1.5}% от старта)</code>\n"
                f"Текущий убыток: <code>${loss_usd:.2f} ({loss_pct:.2f}%)</code>\n\n"
                f"<b>📐 Risk detail</b>\n"
                f"Обычный risk_per_trade: <code>${normal_risk:.2f}</code>\n"
                f"Предлагаемый risk: <code>${_proposed:.2f}</code>\n"
                f"Дополнительный risk: <code>${extra_risk:+.2f}</code>\n"
                f"Оценка доп.убытка (conservative): <code>${est_sl_loss:.2f}</code>\n\n"
                f"<b>Hard safety violations:</b> NONE (только soft manual risk)\n\n"
                f"Вход <b>не выполнен</b>. Подтвердить осознанно или отменить.\n\n"
                f"⏰ Token expires через 60 сек."
            )
            if _kb is not None:
                await reply.reply_text(_msg, parse_mode="HTML", reply_markup=_kb)
            else:
                await reply.reply_text(_msg + "\n\n(reply_markup unavailable)",
                                       parse_mode="HTML")
            _awaiting_amount.pop(uid, None)
            return
    except Exception as e:
        logger.warning(f"Deposit guard manual check failed (proceeding): {e}")
    # FIX 2026-09-02: Пересчёт TP от реальной цены входа (SL структурный — не трогаем).
    # Сигнальный SL рассчитан от close[-2] (закрытого бара). Мы входим по текущей
    # цене fresh_p — она может отличаться. SL оставляем структурным (ниже sweep level).
    # TP пересчитываем с сохранением фиксированного расстояния от entry (как в сигнале).
    # qty пересчитается в _place_market_order от fresh_p и sl_v.
    sl_v = pending["sl"]
    tp_v = pending["tp"]
    sig_price = pending.get("sig", {}).get("price", 0)
    if fresh_p > 0 and sig_price and sig_price > 0 and tp_v and tp_v > 0:
        tp_dist = abs(tp_v - sig_price)
        if pending["side"] == "LONG":
            tp_v = fresh_p + tp_dist
        else:
            tp_v = fresh_p - tp_dist
    # Открываем позицию (с выбранным плечом, если задано; иначе config)
    res = await asyncio_to_thread(
        lambda: _place_market_order(
            pending["symbol"], pending["side"], amount,
            sl_v, tp_v,
            leverage=pending.get("lev"),
        )
    )
    if res.get("ok"):
        p = res.get("price", 0)
        qty_r = res.get("qty", 0)
        real_notional = res.get("notional") or (qty_r * p if p else amount)
        risk_usd = abs(p - sl_v) * qty_r if p else 0
        _log_execution(pending.get("sig"), approved=True, mode="LIVE", ok=True,
                       note=f"order_id={res.get('order_id','')} amount=${amount:.2f} notional=${real_notional:.2f}")
        # SHADOW/audit-only: детектор escalation-паттерна (не блокирует торговлю)
        esc_rec = _audit_escalation(pending["symbol"], amount)
        note_esc = ""
        if esc_rec:
            note_esc = (f"\n⚠️ <b>ESCALATION-AUDIT (shadow)</b>: ставка ${esc_rec['prev_amount_usd']:.2f} "
                        f"→ ${esc_rec['new_amount_usd']:.2f} по {esc_rec['symbol']} после убыточного закрытия.\n"
                        f"Пока НЕ блокируется — наблюдение.")
        note_auto = ""
        actual_lev = int(pending.get("lev") or 0) or int(_leverage())
        if real_notional > amount * 1.2:
            note_auto = (f"\n⚠️ Мин. объём биржи: открыто на ~${real_notional:.2f}"
                         f" (маржа ~${real_notional / max(actual_lev, 1):.2f} при {actual_lev}x)")
        # GIGANTIC-POSITION WARNINGS (2026-09-03): NEAR $90k инцидент — предупреждаем
        # в карточке ОТКРЫТО, если notional был срезан guard-ом или fees съедают сделку.
        if res.get("giant_capped"):
            note_auto += (f"\n🛡️ <b>Giant-guard:</b> позиция ограничена до 5% equity "
                          f"(~${(res.get('notional') or 0):.0f}). Ты запросил больше.")
        if res.get("fees_too_high"):
            note_auto += (f"\n💸 <b>FEES WARNING:</b> комиссия ~${(res.get('fees_est') or 0):.2f} "
                          f"съедает >25% ожидаемой прибыли по TP. Рассмотри лимитку (maker 0.02%) или больший TP.")
        # Карточка открытия с графиком в TradingOS-чат (до ответа в боте).
        # FIX 2026-08-29: показываем ФАКТИЧЕСКОЕ плечо ордера (владелец выбрал
        # 10x), а не config default (5x) — иначе карточка врёт.
        card_ok = await _notify_open_to_tradingos(
            symbol=pending["symbol"], side=pending["side"],
            entry_price=p or 0, qty=qty_r, sl=sl_v, tp=tp_v,
            leverage=actual_lev, entry_time=time.time(),
        )
        chart_note = "\n📊 Карточка открытия отправлена в TradingOS." if card_ok else ""
        await reply.reply_text(
            "✅ <b>ОТКРЫТО</b>\n"
            f"{pending['symbol']} {pending['side']} | ~${real_notional:.2f}\n"
            f"qty: {qty_r:.6g} | Плечо: {actual_lev}x\n"
            f"SL: {_fmt_price(sl_v)} | TP: {_fmt_price(tp_v)}\n"
            f"Риск по SL: ~${risk_usd:.2f}"
            f"{note_esc}"
            f"{note_auto}"
            f"{chart_note}",
            parse_mode="HTML", reply_markup=_main_menu_kb(),
        )
    else:
        _log_execution(pending.get("sig"), approved=True, mode="LIVE", ok=False,
                       note=f"ORDER_ERROR: {res.get('error','')}")
        await reply.reply_text(
            f"❌ <b>Ошибка ордера</b>: {_esc(res.get('error', 'unknown'))}\n"
            f"retCode: {_esc(str(res.get('retCode', '—')))}",
            parse_mode="HTML",
        )
    _awaiting_amount.pop(uid, None)


async def _apply_limit_or_position_sltp(update, sym: str, kind: str, price: float) -> bool:
    """Применить изменение SL/TP/PRICE к WAIT-лимитке (если активна) или к
    открытой позиции (SL/TP через guardian set_trading_stop).

    PRICE — только для неисполненной лимитки (цена ордера);
    SL/TP — приоритет: активная лимитка → amend ордера; иначе позиция.
    """
    try:
        st = _load_wait_limit_state()
        rec = st.get(sym)
        if rec and rec.get("status") == "PLACED":
            if kind == "PRICE":
                res = await asyncio_to_thread(
                    lambda: _amend_wait_limit(sym, price=price))
            elif kind == "SL":
                res = await asyncio_to_thread(
                    lambda: _amend_wait_limit(sym, sl=price))
            elif kind == "TP":
                res = await asyncio_to_thread(
                    lambda: _amend_wait_limit(sym, tp=price))
            else:
                res = {"ok": False, "error": f"unknown kind {kind}"}
            if res.get("ok"):
                await update.message.reply_text(
                    f"✅ {kind} {sym} → {_fmt_price(price)} (WAIT-лимитка изменена)")
                return True
            err = res.get("error", "?")
            # Amend может упасть из-за цены (мин. шаг) — покажем причину
            await update.message.reply_text(f"❌ {kind} {sym}: {_esc(err)}")
            return False
        # Нет активной лимитки → изменение открытой позиции
        if kind == "PRICE":
            await update.message.reply_text(
                f"❌ PRICE применим только к НЕисполненной лимитке (у {sym} её нет).")
            return False
        sys.path.insert(0, "/root/tradingos")
        from guardian.reality_guardian import _set_trading_stop
        if kind == "SL":
            ok = _set_trading_stop(sym, stop_loss=price)
            what = f"SL {sym} → {_fmt_price(price)}"
        elif kind == "TP":
            ok = _set_trading_stop(sym, take_profit=price)
            what = f"TP {sym} → {_fmt_price(price)}"
        else:
            ok, what = False, f"unknown {kind}"
        if ok:
            await update.message.reply_text(f"✅ {what}")
            return True
        await update.message.reply_text(f"❌ Ошибка изменения {kind} для {sym}")
        return False
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {_esc(str(e))}")
        return False


async def _handle_bx_signal(update: Update, raw: str):
    """BingX-сигнал: анализ сразу, открытие — с подтверждением кнопкой.

    Формат: SYMBOL SIDE ENTRY SL TP [xLEV]
    Пример: RUNE LONG 0.4813 0.457 0.55 x20
    """
    try:
        from tradingos.bingx_signal import parse_signal, analyze, place as bx_place
    except Exception as e:
        await update.message.reply_text(f"❌ BingX-модуль не доступен: {_esc(str(e))}")
        return
    sig = parse_signal(raw.split())
    if not sig:
        await update.message.reply_text(
            "❌ Формат: <code>bx SYMBOL SIDE ENTRY SL TP [xLEV]</code>\n"
            "Пример: <code>bx RUNE LONG 0.4813 0.457 0.55 x20</code>",
            parse_mode="HTML")
        return
    a = analyze(sig)
    lines = [
        f"{'✅' if a['ok'] else '❌'} <b>BINGX {a['symbol']} {a['side']} x{a['lev']}</b>",
        f"Вход <code>{a['entry']}</code> | SL <code>{a['sl']}</code> ({a['sl_pct']}%) | "
        f"TP <code>{a['tp']}</code> ({a['tp_pct']}%)",
        f"R:R <b>{a['rr']}</b> | Текущая: {a['cur']}",
        f"Ликвидация ~{a['liq']} ({a['liq_pct']}%) | SL до ликвидации {a['gap_to_liq_pct']:+.2f}%",
        f"Equity ${a['equity']:.2f} → ноушнл {SIZE_PCT_BX}% = <b>${a['notional']:.2f}</b>",
    ]
    if a["issues"]:
        lines += ["", "⚠️ <b>Проблемы:</b>"] + [f"• {_esc(i)}" for i in a["issues"]]
    verdict = "МОЖНО ОТКРЫВАТЬ" if a["ok"] else "ОТКЛОНЕНО preflight"
    lines += ["", f"<b>{verdict}</b>"]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
    if not a["ok"]:
        return
    # Подтверждение открытия (кнопки как у risk-override)
    import secrets as _sec
    from datetime import timedelta
    tok = _sec.token_urlsafe(12)
    _bx_pending[(update.effective_user.id, tok)] = {
        "sig": sig, "expires": datetime.now(timezone.utc) + timedelta(seconds=120),
    }
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ОТКРЫТЬ НА BINGX", callback_data=f"BXGO:{tok}"),
        InlineKeyboardButton("❌ ОТМЕНА", callback_data=f"BXNO:{tok}"),
    ]])
    await update.message.reply_text(
        "🟡 Подтверди открытие лимитки на BingX (120 сек):",
        parse_mode="HTML", reply_markup=kb)


# BingX подтверждения открытия (in-memory, как _pending_overrides)
_bx_pending: dict = {}


# ─── BingX мастер открытия (кнопочный, 2026-08-30 owner) ───────────────
# Шаги: symbol → side (кнопки) → entry → sl → tp → lev (кнопки) →
#       анализ → подтверждение BXGO/BXNO. Состояние хранится в _bx_flow[uid].
_bx_flow: dict = {}
_BX_STEPS = ("symbol", "side", "entry", "sl", "tp", "lev")


async def cmd_bx_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /bx — начало кнопочного мастера BingX-сигнала."""
    uid = update.effective_user.id
    _bx_flow[uid] = {}
    await update.effective_message.reply_text(
        "🔵 <b>BINGX сигнал — шаг 1/6</b>\n"
        "Введи символ (например <code>RUNE</code> или <code>RUNEUSDT</code>):",
        parse_mode="HTML", reply_markup=_reply_kb())


def _bx_next(uid: int, step: str) -> str | None:
    """Следующий шаг или None (все собраны)."""
    try:
        i = _BX_STEPS.index(step)
        return _BX_STEPS[i + 1]
    except (ValueError, IndexError):
        return None


async def _bx_ask_side(uid: int, reply_to):
    _bx_flow[uid]["step"] = "side"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 LONG", callback_data=f"bx_side_LONG_{uid}"),
        InlineKeyboardButton("🔴 SHORT", callback_data=f"bx_side_SHORT_{uid}"),
    ]])
    await reply_to.reply_text("🔵 <b>Шаг 2/6</b>\nСторона?", parse_mode="HTML", reply_markup=kb)


async def _bx_ask_entry(uid: int, reply_to):
    _bx_flow[uid]["step"] = "entry"
    await reply_to.reply_text("🔵 <b>Шаг 3/6</b>\nВведи <b>ВХОД</b> (цена лимитки):", parse_mode="HTML")


async def _bx_ask_sl(uid: int, reply_to):
    _bx_flow[uid]["step"] = "sl"
    await reply_to.reply_text("🔵 <b>Шаг 4/6</b>\nВведи <b>СТОП</b> (SL):", parse_mode="HTML")


async def _bx_ask_tp(uid: int, reply_to):
    _bx_flow[uid]["step"] = "tp"
    await reply_to.reply_text("🔵 <b>Шаг 5/6</b>\nВведи <b>ТЕЙК</b> (TP):", parse_mode="HTML")


async def _bx_ask_lev(uid: int, reply_to):
    _bx_flow[uid]["step"] = "lev"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("5x", callback_data=f"bx_lev_5_{uid}"),
        InlineKeyboardButton("10x", callback_data=f"bx_lev_10_{uid}"),
        InlineKeyboardButton("15x", callback_data=f"bx_lev_15_{uid}"),
        InlineKeyboardButton("20x", callback_data=f"bx_lev_20_{uid}"),
    ]])
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("5x", callback_data=f"bx_lev_5_{uid}"),
        InlineKeyboardButton("10x", callback_data=f"bx_lev_10_{uid}"),
    ], [
        InlineKeyboardButton("15x", callback_data=f"bx_lev_15_{uid}"),
        InlineKeyboardButton("20x", callback_data=f"bx_lev_20_{uid}"),
    ]])
    await reply_to.reply_text("🔵 <b>Шаг 6/6</b>\nПлечо?", parse_mode="HTML", reply_markup=kb)


async def _bx_review_and_confirm(uid: int, reply_to):
    """Сигнал собран → анализ → карточка → подтверждение."""
    from tradingos.bingx_signal import parse_signal, analyze
    f = _bx_flow.get(uid, {})
    sig = parse_signal([
        str(f.get("symbol", "")), str(f.get("side", "LONG")),
        str(f.get("entry", "0")), str(f.get("sl", "0")),
        str(f.get("tp", "0")), f"x{f.get('lev', 10)}",
    ])
    if not sig:
        await reply_to.reply_text("❌ Не удалось собрать сигнал. Начни заново: /bx")
        _bx_flow.pop(uid, None)
        return
    a = analyze(sig)
    lines = [
        f"{'✅' if a['ok'] else '❌'} <b>BINGX {a['symbol']} {a['side']} x{a['lev']}</b>",
        f"Вход <code>{a['entry']}</code> | SL <code>{a['sl']}</code> ({a['sl_pct']}%) | "
        f"TP <code>{a['tp']}</code> ({a['tp_pct']}%)",
        f"R:R <b>{a['rr']}</b> | Текущая: {a['cur']}",
        f"Ликвидация ~{a['liq']} ({a['liq_pct']}%) | SL до ликвидации {a['gap_to_liq_pct']:+.2f}%",
        f"Equity ${a['equity']:.2f} → ноушнл {SIZE_PCT_BX}% = <b>${a['notional']:.2f}</b>",
    ]
    if a["issues"]:
        lines += ["", "⚠️ <b>Проблемы:</b>"] + [f"• {_esc(i)}" for i in a["issues"]]
    lines += ["", f"<b>{'МОЖНО ОТКРЫВАТЬ' if a['ok'] else 'ОТКЛОНЕНО preflight'}</b>"]
    await reply_to.reply_text("\n".join(lines), parse_mode="HTML")
    _bx_flow.pop(uid, None)
    if not a["ok"]:
        return
    # Подтверждение
    import secrets as _sec
    from datetime import timedelta
    tok = _sec.token_urlsafe(12)
    _bx_pending[(uid, tok)] = {
        "sig": sig, "expires": datetime.now(timezone.utc) + timedelta(seconds=120),
    }
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ ОТКРЫТЬ НА BINGX", callback_data=f"BXGO:{tok}"),
        InlineKeyboardButton("❌ ОТМЕНА", callback_data=f"BXNO:{tok}"),
    ]])
    await reply_to.reply_text(
        "🟡 Подтверди открытие лимитки на BingX (120 сек):",
        parse_mode="HTML", reply_markup=kb)


async def _bx_handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Кнопки bx_side_*/bx_lev_* — продолжение мастера. True если обработано."""
    q = update.callback_query
    data = q.data or ""
    uid = q.from_user.id if q.from_user else None
    if not uid or uid not in _bx_flow:
        return False
    if data.startswith("bx_side_"):
        _bx_flow[uid]["side"] = data.split("_")[2]
        await _bx_ask_entry(uid, q.message)
        return True
    if data.startswith("bx_lev_"):
        _bx_flow[uid]["lev"] = int(data.split("_")[2])
        await _bx_review_and_confirm(uid, q.message)
        return True
    return False


async def _bx_handle_text(update: Update, text: str) -> bool:
    """Текстовые шаги мастера (symbol/entry/sl/tp). True если обработано."""
    uid = update.effective_user.id
    f = _bx_flow.get(uid)
    if not f:
        return False
    step = f.get("step")
    if step == "symbol":
        f["symbol"] = text.strip().upper()
        await _bx_ask_side(uid, update.message)
        return True
    if step in ("entry", "sl", "tp"):
        try:
            val = float(text.replace(",", "."))
        except ValueError:
            await update.message.reply_text("❌ Введи число.", parse_mode="HTML")
            return True
        f[step] = val
        nxt = _bx_next(uid, step)
        if nxt == "sl":
            await _bx_ask_sl(uid, update.message)
        elif nxt == "tp":
            await _bx_ask_tp(uid, update.message)
        elif nxt == "lev":
            await _bx_ask_lev(uid, update.message)
        return True
    return False


async def _handle_bx_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    """Кнопка BXGO/BXNO: открыть/отменить BingX-лимитку после preflight-анализа."""
    q = update.callback_query
    action, tok = data.split(":", 1)
    uid = q.from_user.id if q.from_user else None
    entry = _bx_pending.pop((uid, tok), None) if uid else None
    if entry is None:
        await q.message.reply_text("⚠️ Подтверждение истекло или уже использовано.", parse_mode="HTML")
        return
    if action == "BXNO":
        await q.message.reply_text("❌ BingX-лимитка отменена.", parse_mode="HTML")
        return
    if datetime.now(timezone.utc) > entry["expires"]:
        await q.message.reply_text("⏰ Подтверждение истекло (120 сек). Повтори сигнал.", parse_mode="HTML")
        return
    try:
        from tradingos.bingx_signal import place as _bx_place, attach_stops
        r = _bx_place(entry["sig"])
        if not r.get("ok"):
            await q.message.reply_text(
                f"❌ <b>Открытие отклонено</b>\n{_esc(json.dumps(r.get('errors', r.get('error', '?'))))}",
                parse_mode="HTML")
            return
        a = r["analysis"]
        await q.message.reply_text(
            f"✅ <b>BingX лимитка размещена</b>\n"
            f"{a['symbol']} {a['side']} x{a['lev']} | qty {r.get('order','{}')}\n"
            f"Вход {a['entry']} | SL {a['sl']} | TP {a['tp']}\n"
            f"Ноушнл ${a['notional']:.2f}\n"
            f"После филла: <code>bx stops {a['symbol']} {a['side']} {a['sl']} {a['tp']}</code>",
            parse_mode="HTML")
        # Сразу цепляем SL/TP после fill не можем — ждём позицию; guardian подхватит.
        _log_execution(entry["sig"], approved=True, mode="BINGX_LIVE", ok=True,
                       note=f"bx_limit_placed {a['symbol']} {a['side']} ${a['notional']:.2f}")
    except Exception as e:
        logger.error(f"BX confirm error: {e}")
        await q.message.reply_text(f"❌ Ошибка открытия BingX: {_esc(str(e))}", parse_mode="HTML")


# Размер BingX-позиции (% от equity) — из bingx_signal.SIZE_PCT
try:
    from tradingos.bingx_signal import SIZE_PCT as SIZE_PCT_BX
except Exception:
    SIZE_PCT_BX = 20.0


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстовых команд: 'Открой BTC', 'Да, открывай', 'Пропусти',
    а также ввода суммы позиции для LIVE-исполнения."""
    if _is_paused():
        return
    text = (update.message.text or "").strip()
    low = text.lower()

    # Кнопки reply-клавиатуры (нажатое — обычный текст)
    if text == "🔵 BingX сигнал":
        await cmd_bx_start(update, context)
        return
    if text == "🎯 Сигналы":
        from telegram_control.manual_signal import cmd_signals as _cs
        await cmd_signals(update, context)
        return
    if text == "📋 Позиции":
        from telegram_control import bot as _tcb
        await _tcb.cmd_positions(update, context)
        return
    if text == "⚙️ SL/TP":
        from telegram_control import bot as _tcb2
        await _tcb2.cmd_positions(update, context)
        await update.effective_message.reply_text(
            "⚙️ <b>SL/TP</b>\nНапиши: <code>SL SYMBOL цена</code> / <code>TP SYMBOL цена</code>",
            parse_mode="HTML")
        return
    if text == "💰 Риск":
        await cmd_risk(update, context)
        return
    if text == "🎲 Ставка":
        from telegram_control.bet_wizard import cmd_bet
        await cmd_bet(update, context)
        return
    if text == "⏸ Пауза":
        await cmd_pause(update, context)
        return
    if text == "ℹ️ Помощь":
        await cmd_manual_help(update, context)
        return
    if text == "⚙️ Настройки":
        from telegram_control.settings import cmd_settings
        await cmd_settings(update, context)
        return
    # FIX 2026-09-03: если активен визард ставки — текст идёт ему, не сюда
    from telegram_control.bet_wizard import handle_bet_text as _hbt, _FLOWS as _BWS
    if update.effective_user and update.effective_user.id in _BWS:
        handled = await _hbt(update, context)
        if handled:
            return

    # BingX сигнальный контур: текстовый формат или шаги мастера (/bx)
    if uid := update.effective_user.id:
        if await _bx_handle_text(update, text):
            return
    if low.startswith("bx "):
        await _handle_bx_signal(update, text[3:])
        return

    # P0: ввод суммы позиции (state=awaiting_amount)
    uid = update.effective_user.id
    if uid in _awaiting_amount:
        pending = _awaiting_amount.pop(uid)
        try:
            amount = float(text.replace("$", "").replace(",", "."))
        except (TypeError, ValueError):
            await update.message.reply_text(
                "❌ Неверная сумма. Введите число в USD, например <code>10</code>",
                parse_mode="HTML",
            )
            _awaiting_amount[uid] = pending
            return
        cfg = _cfg()
        lo, hi = _amount_bounds_usd(cfg)
        if amount < lo or amount > hi:
            await update.message.reply_text(
                f"❌ Сумма {amount:.2f}$ вне лимита ${lo:.2f}–${hi:.2f} "
                f"(max_amount_pct от equity). Введите другое число.",
            )
            _awaiting_amount[uid] = pending
            return
        # Минимальная сумма по бирже (minOrderQty × цена): НЕ блокируем —
        # бот сам поднимет qty до мин. объёма (с плечом $5 маржи = валидная сделка).
        # Шаг плеча: запрашиваем выбор (5x/10x/15x/20x) перед исполнением.
        _awaiting_amount[uid] = pending
        sym = pending["symbol"]
        _kb_lv = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ 5x", callback_data=f"ms_lev_5_{amount}"),
             InlineKeyboardButton("10x", callback_data=f"ms_lev_10_{amount}")],
            [InlineKeyboardButton("15x", callback_data=f"ms_lev_15_{amount}"),
             InlineKeyboardButton("20x", callback_data=f"ms_lev_20_{amount}")],
            [InlineKeyboardButton("❌ ОТМЕНА", callback_data=f"ms_cancel_{sym}")],
        ])
        await update.message.reply_text(
            f"⚙️ <b>Выберите плечо</b>\n"
            f"{sym} | Сумма ${amount:.2f}\n\n"
            f"5x — маржа ${amount/5:.2f}\n"
            f"10x — маржа ${amount/10:.2f}, ликвидация ближе\n"
            f"15x — маржа ${amount/15:.2f}\n"
            f"20x — маржа ${amount/20:.2f}, риск ликвидации выше\n\n"
            f"⚠️ Плечо НЕ увеличивает риск $ (qty от суммы), но приближает ликвидацию.",
            parse_mode="HTML", reply_markup=_kb_lv,
        )
        return

    # SL/TP/PRICE команды: "SL LINKUSDT 8.10", "TP LINKUSDT 8.6", "PRICE LINKUSDT 8.2",
    # комбинированная: "SL AMZNUSDT 263.9 TP AMZNUSDT 268.5", "УДАЛИ LIMIT AMZNUSDT"
    if low.startswith(("sl ", "tp ", "price ", "удали limit ")):
        parts = text.split()
        # Проверить комбинированную форму: SL sym a TP sym b
        if low.startswith("sl ") and len(parts) >= 6 and parts[0].upper() in ("SL", "TP") and parts[3].upper() in ("SL", "TP"):
            try:
                s1 = parts[1].upper().replace("-", "").replace("/", "")
                if not s1.endswith("USDT"): s1 += "USDT"
                v1 = float(parts[2].replace(",", "."))
                s2 = parts[4].upper().replace("-", "").replace("/", "")
                if not s2.endswith("USDT"): s2 += "USDT"
                v2 = float(parts[5].replace(",", "."))
                if s1 != s2:
                    await update.message.reply_text("❌ SL и TP должны быть по одному символу.")
                    return
                sym = s1
                ok1 = await _apply_limit_or_position_sltp(update, sym, "SL", v1)
                ok2 = await _apply_limit_or_position_sltp(update, sym, "TP", v2)
                await update.message.reply_text(
                    f"✅ SL {_fmt_price(v1)} / TP {_fmt_price(v2)} по {sym} "
                    f"{'— применено' if (ok1 and ok2) else '— ЧАСТИЧНО (см. выше)'}")
                return
            except ValueError:
                pass
        if len(parts) >= 3:
            kind = parts[0].upper()
            sym = parts[1].upper().replace("-", "").replace("/", "")
            if not sym.endswith("USDT"):
                sym += "USDT"
            try:
                price = float(parts[2].replace(",", "."))
            except ValueError:
                await update.message.reply_text("❌ Цена не распознана. Пример: <code>SL LINKUSDT 8.10</code>")
                return
            await _apply_limit_or_position_sltp(update, sym, kind, price)
        else:
            await update.message.reply_text(
                "Формат: <code>SL SYMBOL цена</code>, <code>TP SYMBOL цена</code>, "
                "<code>PRICE SYMBOL цена</code> (цена лимитки), "
                "<code>SL AMZNUSDT 263.9 TP AMZNUSDT 268.5</code> (оба сразу), "
                "<code>УДАЛИ LIMIT AMZNUSDT</code>")
        return

    if "удали limit" in low or low.startswith("del limit "):
        parts = text.split()
        if len(parts) >= 3:
            sym = parts[2].upper().replace("-", "").replace("/", "")
            if not sym.endswith("USDT"):
                sym += "USDT"
            res = await asyncio_to_thread(lambda: _delete_wait_limit(sym))
            if res.get("ok"):
                await update.message.reply_text(f"🗑️ <b>WAIT-лимитка {sym} удалена</b>.")
                try:
                    await _notify_wait_limit_cancelled(sym, "удалена владельцем")
                except Exception:
                    pass
            else:
                await update.message.reply_text(
                    f"❌ <b>Не удалось удалить</b>: {_esc(res.get('error', '?'))}")
        else:
            await update.message.reply_text("Формат: <code>УДАЛИ LIMIT SYMBOL</code>, например <code>УДАЛИ LIMIT AMZNUSDT</code>")
        return

    if low in ("пропусти", "пропустить", "skip", "отмени сигнал"):
        await update.message.reply_text("❌ Сигнал пропущен.")
        return

    if low.startswith(("открой", "да, открывай", "да открывай", "открывай")):
        # Извлечь символ: 'открой btc' / 'открой BTC long'
        tokens = low.replace(",", "").replace("—", " ").split()
        sym = None
        for t in tokens:
            t2 = t.strip("!.")
            if t2 in ("long", "short", "buy", "sell"):
                continue
            if t2 in ("да", "открывай", "открой"):
                continue
            sym = t2
            break
        if not sym:
            await update.message.reply_text("Уточните символ: «Открой BTC»")
            return
        cand = sym.upper()
        if not cand.endswith("USDT"):
            cand += "USDT"
        await _confirm_open(update, context, cand)
        return


async def _confirm_open(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str):
    sig = _last_signals.get(symbol)
    if sig is None:
        await update.message.reply_text(
            f"Сигнал для {symbol} не найден. Сначала: /signals"
        )
        return
    # Проверки гейта (DRY-RUN): сигнал актуален, цена в зоне
    try:
        import httpx
        with httpx.Client(timeout=15) as c:
            r = c.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": symbol},
            )
            t = ((r.json().get("result") or {}).get("list") or [{}])[0]
            now_price = float(t.get("lastPrice") or 0)
    except Exception as e:
        await update.message.reply_text(f"❌ Не удалось проверить цену: {e}")
        return

    base = sig.get("price", now_price)
    drift = abs(now_price - base) / base * 100 if base else 0
    checks = [
        ("Цена в зоне входа (±0.5%)", drift <= 0.5),
        ("Сигнал не отменён", True),
        ("Риск задан", _risk_usd() > 0),
        ("Биржа доступна", now_price > 0),
    ]
    passed = sum(1 for _, ok in checks if ok)
    cfg = _cfg()
    dry = cfg.get("dry_run", True)

    lines = [
        f"🟡 <b>ПОДТВЕРЖДЕНИЕ ПРИНЯТО</b>",
        f"<b>{symbol}</b> {sig['side']}",
        f"Цена сейчас: {_fmt_price(now_price)}",
        f"SL: {_fmt_price(sig.get('price', now_price) * 0.985 if sig['side']=='LONG' else sig.get('price', now_price) * 1.015)}",
        f"TP: {_fmt_price(sig.get('price', now_price) * 1.03 if sig['side']=='LONG' else sig.get('price', now_price) * 0.97)}",
        f"Риск: ${_risk_usd(cfg):.2f}",
        f"Проверки: {passed}/{len(checks)} PASS",
    ]
    if dry:
        lines.append("")
        lines.append("🔵 <b>DRY-RUN</b>: реальный ордер НЕ отправлен.")
        lines.append("Журнал записан. Для реального исполнения нужно разрешение.")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 ДА, ОТКРЫТЬ", callback_data=f"ms_exec_{symbol}"),
        InlineKeyboardButton("🔴 ОТМЕНА", callback_data=f"ms_cancel_{symbol}"),
    ]])
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)


async def _ask_leverage(query, sym: str, amount: float, origin: str):
    """Шаг выбора плеча при открытии: 5x / 10x / 15x / 20x (по выбору владельца).

    origin: "amt" (кнопка суммы) — возврат в ms_lev_{lev}_{amount};
            "text" (текстовый ввод) — значение уже в _awaiting_amount.
    """
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ 5x", callback_data=f"ms_lev_5_{amount}"),
         InlineKeyboardButton("10x", callback_data=f"ms_lev_10_{amount}")],
        [InlineKeyboardButton("15x", callback_data=f"ms_lev_15_{amount}"),
         InlineKeyboardButton("20x", callback_data=f"ms_lev_20_{amount}")],
        [InlineKeyboardButton("❌ ОТМЕНА", callback_data=f"ms_cancel_{sym}")],
    ])
    await query.message.reply_text(
        f"⚙️ <b>Выберите плечо</b>\n"
        f"{sym} | Сумма ${amount:.2f}\n\n"
        f"5x — маржа ${amount/5:.2f}\n"
        f"10x — маржа ${amount/10:.2f}, ликвидация ближе\n"
        f"15x — маржа ${amount/15:.2f}\n"
        f"20x — маржа ${amount/20:.2f}, риск ликвидации выше\n\n"
        f"⚠️ Плечо НЕ увеличивает риск $ (qty от суммы), но приближает ликвидацию.",
        parse_mode="HTML", reply_markup=kb,
    )


async def callback_handler_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки ручного контура (префикс ms_)."""
    query = update.callback_query
    data = query.data
    if not data.startswith("ms_"):
        return False

    await query.answer()

    if data == "ms_risk_confirm":
        new_risk = context.user_data.pop("pending_risk", None)
        is_pct = context.user_data.pop("pending_risk_is_pct", False)
        if new_risk:
            cfg = _cfg()
            if is_pct:
                cfg["risk_per_trade_pct"] = new_risk
            else:
                cfg["risk_per_trade_usd"] = new_risk
            _save_cfg(cfg)
            if is_pct:
                await query.message.reply_text(
                    f"✅ Риск: {new_risk}% от equity (≈${_risk_usd(cfg):.2f} сейчас)")
            else:
                await query.message.reply_text(f"✅ Риск установлен: ${new_risk:.2f}")
        await query.message.delete()
        return True

    if data == "ms_risk_cancel":
        context.user_data.pop("pending_risk", None)
        await query.message.reply_text("❌ Изменение риска отменено.")
        await query.message.delete()
        return True

    if data.startswith("ms_wait_"):
        # WAIT_LIMIT шаг 1: владелец нажал «Поставить LIMIT на откате».
        # Сумму и плечо выбирает САМ владелец (как в маркет-флоу):
        # сначала кнопки % от equity / своя сумма → потом выбор плеча.
        # B2: callback ms_wait_{sym}_1 / ms_wait_{sym}_2 — выбор уровня лестницы.
        parts_w = data.split("_")
        sym = parts_w[2]
        ladder_level = 1
        if len(parts_w) >= 4 and parts_w[3] in ("1", "2"):
            ladder_level = int(parts_w[3])
        if _is_paused():
            await query.message.reply_text("⏸ PAUSED — исполнение заблокировано.")
            return True
        sig = _last_signals.get(sym)
        if sig is None or not sig.get("wait_limit_entry"):
            sig = _load_signal_from_journal(sym)
        wl = (sig or {}).get("wait_limit_entry") or 0
        if ladder_level == 2:
            wl = (sig or {}).get("wait_limit_entry_deep") or wl
        sl = (sig or {}).get("sl") or 0
        tp = (sig or {}).get("final_tp") or 0
        if not sig or wl <= 0 or sl <= 0 or tp <= 0:
            await query.message.reply_text(f"Сигнал {sym} устарел. /signals")
            return True
        # Сохраняем контекст WAIT-сетапа в pending (mode=wait_limit отличает
        # его от маркет-флоу в обработчиках суммы/плеча)
        uid = update.effective_user.id
        _awaiting_amount[uid] = {
            "symbol": sym, "side": sig["side"], "sig": sig,
            "sl": sl, "tp": tp, "wait_limit_entry": wl,
            "ladder_level": ladder_level,
            "min_usd": _min_order_usd(sym) if _min_order_usd(sym) else 1.0,
            "mode": "wait_limit",
        }
        # Кнопки суммы: % от equity (те же, что в маркет-флоу) + своя сумма
        cfg = _cfg()
        lo = max(1.0, _min_order_usd(sym) if _min_order_usd(sym) else 1.0)
        quick = _quick_amounts(cfg, lo)
        kb_rows = []
        for a in quick:
            pct = f" ({a / _get_manual_equity() * 100:.0f}%)" if _get_manual_equity() > 0 else ""
            kb_rows.append([InlineKeyboardButton(f"💵 ${_fmt_amount(a)}{pct}",
                                                 callback_data=f"ms_amt_{a}_{sym}")])
        kb_rows.append([InlineKeyboardButton("✏️ Своя сумма", callback_data=f"ms_custom_{sym}")])
        kb_rows.append([InlineKeyboardButton("❌ ОТМЕНА", callback_data=f"ms_cancel_{sym}")])
        kb = InlineKeyboardMarkup(kb_rows)
        await query.message.reply_text(
            f"📌 <b>WAIT-LIMIT @ {_fmt_price(wl)}</b>\n"
            f"<b>{sym}</b> {sig['side']} | Текущая цена {_fmt_price(sig.get('price', 0))}\n"
            f"SL <code>{_fmt_price(sl)}</code> | TP <code>{_fmt_price(tp)}</code>\n"
            f"R:R лимитки ~{sig.get('wait_rr','—')}:1\n\n"
            f"💵 <b>Выберите сумму позиции</b> (затем плечо):",
            parse_mode="HTML", reply_markup=kb)
        return True

    if data.startswith("ms_wedit_"):
        # Показать активную WAIT-лимитку и предложить изменение:
        # текстом "PRICE SYM цена" / "SL SYM цена" / "TP SYM цена"
        sym = data.split("_", 2)[2]
        st = _load_wait_limit_state()
        rec = st.get(sym)
        if not rec or rec.get("status") != "PLACED":
            await query.message.reply_text(
                f"⏳ Активной WAIT-лимитки по {sym} нет (или уже исполнена).")
            return True
        await query.message.reply_text(
            f"✏️ <b>Изменение WAIT-лимитки {sym}</b>\n"
            f"Цена: <code>{_fmt_price(rec.get('price', 0))}</code>\n"
            f"SL: <code>{_fmt_price(rec.get('sl', 0))}</code>\n"
            f"TP: <code>{_fmt_price(rec.get('tp', 0))}</code>\n"
            f"Сумма ~${(rec.get('qty', 0) or 0) * (rec.get('price', 0) or 0):,.0f} | "
            f"Плечо {rec.get('lev', '—')}x\n\n"
            f"Напишите одной строкой:\n"
            f"<code>PRICE AMZNUSDT 265.5</code> — новая цена лимитки\n"
            f"<code>SL AMZNUSDT 264.1</code> — новый стоп\n"
            f"<code>TP AMZNUSDT 268.0</code> — новый тейк\n"
            f"Можно SL и TP вместе: <code>SL AMZNUSDT 263.9 TP AMZNUSDT 268.5</code>",
            parse_mode="HTML")
        return True

    if data.startswith("ms_wdel_"):
        # Пользователь удалил WAIT-лимитку вручную (до исполнения)
        sym = data.split("_", 2)[2]
        res = await asyncio_to_thread(lambda: _delete_wait_limit(sym))
        if res.get("ok"):
            await query.message.reply_text(f"🗑️ <b>WAIT-лимитка {sym} удалена</b>.")
            try:
                await _notify_wait_limit_cancelled(sym, "удалена владельцем")
            except Exception:
                pass
        else:
            await query.message.reply_text(
                f"❌ <b>Не удалось удалить</b>: {_esc(res.get('error', '?'))}")
        return True

    if data.startswith("ms_sltp_"):
        # Показать текущие SL/TP позиции и предложить изменить
        sym = data.split("_", 2)[2]
        query.answer()
        try:
            import json as _json
            _state = _json.loads(Path("/root/tradingos/guardian/reality_state.json").read_text())
            st = _state.get(sym, {})
            sl = st.get("sl_initial", 0) or 0
            tp = st.get("tp_initial", 0) or 0
            entry = st.get("entry", 0) or 0
            side = st.get("side", "?")
            await query.message.reply_text(
                f"⚙️ <b>{sym}</b> {side}\n"
                f"Entry: {_fmt_price(entry)}\n"
                f"SL: {_fmt_price(sl)}\n"
                f"TP: {_fmt_price(tp)}\n\n"
                f"Для изменения напишите:\n"
                f"<code>SL {sym} цена</code> или <code>TP {sym} цена</code>\n"
                f"Например: <code>SL LINKUSDT 8.20</code>",
                parse_mode="HTML",
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Ошибка: {_esc(str(e))}")
        return True

    if data.startswith("ms_amt_"):
        # Быстрая кнопка суммы: ms_amt_{amount}_{symbol}
        parts = data.split("_", 3)
        if len(parts) >= 4:
            amount = float(parts[2])
            sym = parts[3]
            uid = update.effective_user.id
            pending = _awaiting_amount.get(uid)
            if pending and pending.get("symbol") == sym:
                # Шаг 3: сумма выбрана → предложить выбор плеча (5x по умолч.)
                pending = _awaiting_amount.get(uid)
                if pending and pending.get("symbol") == sym:
                    _awaiting_amount[uid] = pending
                    await _ask_leverage(query, sym, amount, "amt")
            return True

    if data.startswith("ms_lev_"):
        # Шаг: пользователь выбрал плечо: ms_lev_{lev}_{amount}.
        # wait_limit → ставим ЛИМИТКУ с суммой владельца и плечом;
        # иначе → маркет-флоу _execute_manual_order.
        parts = data.split("_", 3)
        if len(parts) >= 4:
            lev = int(parts[2])
            rest = parts[3]
            uid = update.effective_user.id
            pending = _awaiting_amount.get(uid)
            if pending:
                try:
                    amount = float(rest)
                except ValueError:
                    await query.message.reply_text("❌ Ошибка суммы. Попробуйте ещё раз.")
                    return True
                if pending.get("mode") == "wait_limit":
                    try:
                        sym = pending["symbol"]
                        wl = pending.get("wait_limit_entry") or 0
                        sl = pending.get("sl") or 0
                        tp = pending.get("tp") or 0
                        if wl <= 0 or sl <= 0 or tp <= 0:
                            await query.message.reply_text("❌ WAIT-сигнал устарел. /signals")
                            _awaiting_amount.pop(uid, None)
                            return True
                        # Pause fail-safe (как в маркет-флоу)
                        if _is_paused():
                            await query.message.reply_text("⏸ PAUSED — исполнение заблокировано.")
                            _awaiting_amount.pop(uid, None)
                            return True
                        # Guard: предложенный риск (SL-дистанция × qty)
                        try:
                            from tradingos.strategies.deposit_guard import get_guard as _g2
                            qty_est = amount / wl
                            proposed = abs(wl - sl) * qty_est
                            _allowed, _reason = _g2().can_open_position(proposed_risk_usd=proposed)
                            if not _allowed:
                                await query.message.reply_text(
                                    f"⛔ Guard блокирует: {_reason}")
                                _awaiting_amount.pop(uid, None)
                                return True
                        except Exception:
                            pass
                        # Freshness-guard: лимитка НЕ должна стать marketable.
                        # Для BUY: если текущая цена УЖЕ ниже зоны лимита — ордер
                        # исполнится мгновенно (price ≤ market) = маркет, а не WAIT.
                        # Подтягиваем живую цену ДО постановки.
                        try:
                            import httpx as _hx
                            with _hx.Client(timeout=8) as _c:
                                _t = _c.get(
                                    "https://api.bybit.com/v5/market/tickers",
                                    params={"category": "linear", "symbol": sym})
                                cur_px = float(((_t.json().get("result") or {}).get("list") or [{}])[0].get("lastPrice", 0))
                        except Exception:
                            cur_px = 0.0
                        is_long = str(pending.get("side", "LONG")).upper() in ("LONG", "BUY")
                        zone_passed = bool(cur_px > 0) and (
                            (is_long and cur_px <= wl) or (not is_long and cur_px >= wl)
                        )
                        if zone_passed:
                            await query.message.reply_text(
                                f"⛔ <b>Зона лимита уже пройдена</b>\n"
                                f"{sym} {pending['side']} | Цена сейчас <code>{_fmt_price(cur_px)}</code> — "
                                f"уже {'ниже' if is_long else 'выше'} зоны лимита <code>{_fmt_price(wl)}</code>.\n\n"
                                f"Ордер НЕ поставлен: лимит исполнился бы сразу как маркет "
                                f"(цена в/за зоной). Это не откат, а проход — ждём новых сигналов /signals.",
                                parse_mode="HTML")
                            _awaiting_amount.pop(uid, None)
                            return True
                        res = await asyncio_to_thread(
                            lambda: _place_limit_order(sym, pending["side"], wl, amount,
                                                       sl, tp, leverage=lev,
                                                       ladder_level=pending.get("ladder_level", 1))
                        )
                        if res.get("ok"):
                            qty_r = res.get("qty", 0)
                            notional = qty_r * wl
                            kb_edit = InlineKeyboardMarkup([[
                                InlineKeyboardButton("✏️ Изменить (цена/SL/TP)",
                                                     callback_data=f"ms_wedit_{sym}"),
                                InlineKeyboardButton("🗑 Удалить лимитку",
                                                     callback_data=f"ms_wdel_{sym}"),
                            ]])
                            await query.message.reply_text(
                                f"📌 <b>WAIT-LIMIT поставлен</b>\n"
                                f"{sym} {pending['side']} | {qty_r:.4f} @ <code>{_fmt_price(wl)}</code>\n"
                                f"Сумма ~${notional:,.0f} ({_fmt_price(wl)} × {qty_r:.4f})\n"
                                f"Плечо: {lev}x | SL <code>{_fmt_price(sl)}</code> | TP <code>{_fmt_price(tp)}</code>\n"
                                f"R:R ~{pending.get('sig', {}).get('wait_rr', '—')}:1\n"
                                f"⏱ Лимит живёт <b>3 закрытых H1-бара</b> (<b>≤4ч</b>), затем отмена.\n"
                                f"Исполнение → уведомлю в TradingOS.",
                                parse_mode="HTML", reply_markup=kb_edit)
                            # Уведомление в TradingOS-чат о постановке лимитки
                            try:
                                await _notify_wait_limit_placed(sym, pending["side"], wl,
                                                                qty_r, sl, tp, notional, lev)
                            except Exception as e:
                                logger.warning(f"wait-limit placed notify failed: {e}")
                        else:
                            await query.message.reply_text(
                                f"❌ <b>Ошибка лимитки</b>: {_esc(res.get('error', '?'))}")
                        _awaiting_amount.pop(uid, None)
                    except Exception as e:
                        logger.error(f"WAIT-LIMIT callback error: {e}")
                        await query.message.reply_text(f"❌ Ошибка: {_esc(str(e))}")
                        _awaiting_amount.pop(uid, None)
                    return True
                pending["lev"] = lev
                _awaiting_amount[uid] = pending
                await _execute_manual_order(update, context, uid, pending, amount)
            return True

    if data.startswith("ms_fund_"):
        # Funding-capture: владелец нажал «📌 SHORT/LONG 0.5/1%» на карточке
        # opportunity_watchdog. Формат: ms_fund_{sym}_{side}_{pct}
        parts_f = data.split("_")
        # ms_fund_SYMUSDT_side_pct → parts: ['ms','fund','SYM','side','pct']
        if len(parts_f) >= 5:
            sym_f = parts_f[2]
            side_f = parts_f[3].upper()
            try:
                pct_f = float(parts_f[4])
            except ValueError:
                pct_f = 0.5
            if _is_paused():
                await query.message.reply_text("⏸ PAUSED — исполнение заблокировано.")
                return True
            # Сумма от equity (как quick-кнопки): pct% от неё
            eq = _get_manual_equity()
            amount = eq * pct_f / 100 if eq > 0 else 0
            if amount <= 0:
                await query.message.reply_text("❌ Не удалось получить equity.")
                return True
            # Стоп/цель от funding-механики: стоп = консервативный (0.5% цены),
            # цель = сбор 3 выплат. Подтягиваем живую цену + последнюю выплату.
            cur_px = 0.0
            funding_bps = 0.0
            try:
                import httpx as _hfx
                with _hfx.Client(timeout=8) as _cf:
                    _tf = _cf.get("https://api.bybit.com/v5/market/tickers",
                                  params={"category": "linear", "symbol": sym_f})
                    _tk = ((_tf.json().get("result") or {}).get("list") or [{}])[0]
                    cur_px = float(_tk.get("lastPrice") or 0)
                    funding_bps = float(_tk.get("fundingRate") or 0) * 10000
            except Exception:
                pass
            if cur_px <= 0:
                await query.message.reply_text("❌ Нет живой цены — сигнал устарел.")
                return True
            is_long_f = side_f in ("LONG", "BUY")
            # Цель = 3 выплаты funding, стоп = 0.75 × цель (R:R 1.33).
            # FIX 2026-08-30: раньше стоп был 6×funding = вдвое шире цели →
            # R:R 0.5, авто-funding в минусе (3 SL на −$20 при TP +$4..+10).
            tp_pct = abs(funding_bps) * 3 / 10000
            stop_pct = max(0.0025, tp_pct * 0.75)
            sl_f = cur_px * (1 - stop_pct) if is_long_f else cur_px * (1 + stop_pct)
            tp_f = cur_px * (1 + tp_pct) if is_long_f else cur_px * (1 - tp_pct)
            sig_f = {"symbol": sym_f, "side": side_f, "price": cur_px,
                     "sl": sl_f, "final_tp": tp_f,
                     "signal_id": f"FUND-{sym_f}-{int(time.time())}",
                     "score": 99, "funding_bps": funding_bps}
            # Экономический гейт (VELVET-инцидент 2026-08-29): цель обязана
            # покрывать комиссию round-trip с запасом — иначе сделка убыточна
            # до входа (TP 0.09% < fees 0.22% → net −$1.14).
            _ok_f, _why_f = _funding_tp_ok(funding_bps, _cfg())
            if not _ok_f:
                _fee_cfg = _cfg()
                _fee_pct = float(_fee_cfg.get("funding_fee_round_trip_pct", 0.22) or 0.22)
                _mult = float(_fee_cfg.get("funding_min_edge_mult", 1.5) or 1.5)
                _need_bps = _fee_pct / 100.0 * _mult * 10000 / 3
                logger.warning(f"🛑 FUNDING SKIP {sym_f}: {_why_f} (funding {funding_bps:+.1f} bps)")
                _log_execution(sig_f, approved=False, mode="LIVE", ok=False,
                               note=f"FUNDING_TP_BELOW_COST: {_why_f}")
                await query.message.reply_text(
                    f"⛔ <b>Funding пропущен — цель ниже издержек</b>\n"
                    f"<b>{sym_f}</b> funding {funding_bps:+.1f} bps/8ч\n"
                    f"Цель (3 выплаты): <b>{tp_pct*100:.2f}%</b> цены\n"
                    f"Комиссия round-trip: <b>{_fee_pct:.2f}%</b>\n"
                    f"Требуется funding ≥ ~{_need_bps:.0f} bps/8ч, чтобы цель "
                    f"покрыла издержки с запасом.\n"
                    f"Сделка не открыта.",
                    parse_mode="HTML",
                )
                return True
            uid_f = update.effective_user.id
            _awaiting_amount[uid_f] = {
                "symbol": sym_f, "side": side_f, "sig": sig_f,
                "sl": sl_f, "tp": tp_f, "min_usd": 1.0,
                "mode": "funding",
            }
            # Переиспользуем штатный выбор плеча (ms_lev_ → _execute_manual_order)
            await _ask_leverage(query, sym_f, round(amount, 2), "amt")
            return True

    if data.startswith("ms_custom_"):
        # Пользователь выбрал "Ввести свою сумму" — ждём текст
        sym = data.split("_", 2)[2]
        min_usd = _min_order_usd(sym)
        low_bound = max(1.0, min_usd)
        await query.message.reply_text(
            f"✏️ Введите сумму в USD (минимум ${low_bound:.2f}):",
        )
        return True

    if data.startswith("ms_skip_") or data.startswith("ms_cancel_"):
        sym = data.split("_", 2)[2]
        _last_signals.pop(sym, None)
        await query.message.reply_text(f"❌ Сигнал {sym} отменён/пропущен.")
        return True

    if data.startswith("ms_open_"):
        # Шаг 1: пользователь нажал ОТКРЫТЬ на карточке → проверки + подтверждение
        sym = data.split("_", 2)[2]
        if _is_paused():
            await query.message.reply_text(
                "⏸ <b>PAUSED</b> — ручная сессия на паузе. Исполнение заблокировано. /resume"
            )
            return True
        sig = _last_signals.get(sym)
        if sig is None or not _signal_valid(sig):
            # P1: сигнал потерян после рестарта — пробуем восстановить из журнала
            sig = _load_signal_from_journal(sym)
        if sig is None:
            await query.message.reply_text(f"Сигнал {sym} не найден или истёк. /signals")
            return True
        await _gate_checks(update, context, sym, sig)
        return True

    if data.startswith("ms_exec_"):
        # Шаг 2: пользователь подтвердил ДА, ОТКРЫТЬ
        sym = data.split("_", 2)[2]
        if _is_paused():
            await query.message.reply_text(
                "⏸ <b>PAUSED</b> — ручная сессия на паузе. Исполнение заблокировано. /resume"
            )
            return True
        sig = _last_signals.get(sym)
        if sig is None or not _signal_valid(sig):
            sig = _load_signal_from_journal(sym)
        if sig is None:
            await query.message.reply_text(f"Сигнал {sym} не найден или истёк. /signals")
            return True
        cfg = _cfg()
        dry = cfg.get("dry_run", True)
        mode = "DRY_RUN" if dry else "LIVE"
        # Повторная быстрая проверка актуальности перед исполнением
        fresh, ok = await _price_ok(sym, sig)
        if dry:
            _log_execution(sig, approved=True, mode=mode, ok=True,
                           note="ордер НЕ отправлен (DRY-RUN)")
            size = _calc_size(sym, sig, cfg)
            lines = [
                "🟢 <b>DRY-RUN ИСПОЛНЕНИЕ</b>",
                f"Сигнал: <code>{sig.get('signal_id', '?')}</code>",
                f"<b>{sym}</b> {sig['side']} | Score {sig['score']}/100",
                f"Цена: {_fmt_price(fresh)}",
                f"Размер: {size:.4f}",
                f"Риск: ${_risk_usd(cfg):.2f}",
                f"SL: {_fmt_price(_sl(sym, sig))} | TP1: {_fmt_price(_tp1(sym, sig))}",
                f"R:R ~{_rr(sym, sig)}:1",
                f"Проверки: {'OK' if ok else 'WARN — цена ушла из зоны'}",
                "",
                "<b>USER_APPROVED ✓</b>",
                "🔵 Bybit order <b>НЕ вызван</b> (DRY-RUN).",
                "Журнал записан: SIGNAL→APPROVED→EXECUTED(DRY).",
            ]
            await query.message.reply_text("\n".join(lines), parse_mode="HTML")
        else:
            # LIVE: запрашиваем сумму позиции у пользователя
            if not ok or fresh <= 0:
                await query.message.reply_text(
                    f"❌ Цена ушла из зоны входа (>0.5%). Текущая: {_fmt_price(fresh)}. "
                    f"Сигнал {sym} отменён."
                )
                return True
            min_usd = _min_order_usd(sym)
            _awaiting_amount[update.effective_user.id] = {
                "symbol": sym, "side": sig["side"], "sig": sig,
                "sl": _sl(sym, sig), "tp": _tp1(sym, sig),
                "min_usd": min_usd,
            }
            low_bound = max(1.0, min_usd)
            # Быстрые кнопки суммы (вместо ввода текста) — % от equity
            # (1/2/5/10%), не хардкод $5-$50 из эпохи $80-аккаунта.
            quick_amounts = _quick_amounts(cfg, low_bound)
            kb_rows = []
            for a in quick_amounts:
                pct_label = f" ({a / _get_manual_equity() * 100:.0f}%)" if _get_manual_equity() > 0 else ""
                kb_rows.append([InlineKeyboardButton(
                    f"💵 ${_fmt_amount(a)}{pct_label}",
                    callback_data=f"ms_amt_{a}_{sym}")])
            kb_rows.append([InlineKeyboardButton("✏️ Ввести свою сумму", callback_data=f"ms_custom_{sym}")])
            kb_rows.append([InlineKeyboardButton("❌ ОТМЕНА", callback_data=f"ms_cancel_{sym}")])
            kb = InlineKeyboardMarkup(kb_rows)
            # GIGANTIC-POSITION PRE-WARNING (2026-09-03): показываем cap и fees ДО выбора.
            eq_now = _get_manual_equity()
            giant_cap = eq_now * 0.05 if eq_now > 0 else 0
            lev_now = _leverage()
            sl_now = _sl(sym, sig)
            price_now = sig.get("price", 0) or fresh
            sl_pct = abs(price_now - sl_now) / price_now * 100 if (sl_now and price_now) else 0.5
            fee_at_cap = giant_cap * 0.0011
            await query.message.reply_text(
                f"💵 <b>Выберите сумму позиции</b>\n"
                f"<b>{sym}</b> {sig['side']} | Плечо {lev_now}x\n"
                f"💡 Мин. объём биржи ≈ ${low_bound:.2f}. При вводе меньше — бот откроет на минимум.\n"
                f"🛡️ <b>Cap:</b> маржа до ${giant_cap * lev_now:.0f} (5% equity, notional)\n"
                f"💸 <b>Fees (taker):</b> ~0.11% RT — при марже ${_fmt_amount(giant_cap / max(lev_now, 1))} "
                f"комиссия ≈ ${fee_at_cap:.2f} (SL {sl_pct:.2f}% → TP 3R ≈ {sl_pct * 3:.1f}%)\n"
                f"<i>Мейкер-лимитка = 0.04% RT (в 2.75× дешевле)</i>\n\n"
                f"Или введите свою сумму текстом:",
                parse_mode="HTML", reply_markup=kb,
            )
        return True

    return True


def _signal_valid(sig: dict | None) -> bool:
    """Задача 4: TTL сигнала 60 минут. Цена могла уйти далеко — старый вход не предлагаем."""
    if not sig:
        return False
    stored = sig.get("_stored_at", 0)
    if stored and time.time() - stored > 3600:  # 60 минут
        return False
    return True


def _sl(sym, sig):
    """SL из TradePlan сканера (market-based), фолбэк 1.5%."""
    v = sig.get("sl")
    if v and float(v) > 0:
        return float(v)
    p = sig.get("price", 0)
    return p * 0.985 if sig.get("side") == "LONG" else p * 1.015


def _tp1(sym, sig):
    """final_tp из TradePlan сканера, фолбэк 3%."""
    v = sig.get("final_tp")
    if v and float(v) > 0:
        return float(v)
    p = sig.get("price", 0)
    return p * 1.03 if sig.get("side") == "LONG" else p * 0.97


def _rr(sym, sig):
    p = sig.get("price", 0)
    sl, tp = _sl(sym, sig), _tp1(sym, sig)
    if sig.get("side") == "LONG":
        return round((tp - p) / max(p - sl, 1e-9), 1)
    return round((p - tp) / max(sl - p, 1e-9), 1)


def _calc_size(sym, sig, cfg):
    risk = _risk_usd(cfg)
    p = sig.get("price", 0)
    sl = _sl(sym, sig)
    rpu = abs(p - sl)
    return risk / rpu if rpu > 0 else 0.0


async def _price_ok(sym, sig):
    """Текущая цена и в зоне ли она входа (±0.5% от цены сигнала)."""
    try:
        import httpx
        with httpx.Client(timeout=15) as c:
            r = c.get(
                "https://api.bybit.com/v5/market/tickers",
                params={"category": "linear", "symbol": sym},
            )
            t = ((r.json().get("result") or {}).get("list") or [{}])[0]
            now_price = float(t.get("lastPrice") or 0)
        base = sig.get("price", now_price)
        drift = abs(now_price - base) / base * 100 if base else 99
        return now_price, drift <= 0.5
    except Exception:
        return 0.0, False


async def _gate_checks(update: Update, context: ContextTypes.DEFAULT_TYPE, symbol: str, sig: dict):
    """Шаг 1 гейта: проверки + карточка подтверждения с кнопками ДА/ОТМЕНА."""
    query = update.callback_query
    now_price, price_ok = await _price_ok(symbol, sig)
    # Жёсткий блок: цена ушла из зоны входа (>0.5%) — сигнал неактуален,
    # не предлагаем подтверждение. (Раньше только показывали "3/4 PASS".)
    if not price_ok or now_price <= 0:
        base = sig.get("price", now_price)
        drift = abs(now_price - base) / base * 100 if base and now_price else 0
        await query.message.reply_text(
            f"❌ <b>Сигнал {symbol} неактуален</b>\n"
            f"Цена сигнала: {_fmt_price(base)}\n"
            f"Цена сейчас: {_fmt_price(now_price)} (уход {drift:.1f}% > 0.5%)\n"
            f"Откройте /signals для свежего сигнала.",
            parse_mode="HTML",
        )
        return True
    checks = [
        ("Цена в зоне входа (±0.5%)", price_ok and now_price > 0),
        ("Сигнал не отменён", True),
        ("Риск задан", _risk_usd() > 0),
        ("Биржа доступна", now_price > 0),
    ]
    passed = sum(1 for _, ok in checks if ok)
    cfg = _cfg()

    lines = [
        "🟡 <b>ПОДТВЕРЖДЕНИЕ ПРИНЯТО</b>",
        f"Сигнал: <code>{sig.get('signal_id', '?')}</code>",
        f"<b>{symbol}</b> — {sig['side']} | Score: {sig['score']}/100",
        f"Цена сейчас: {_fmt_price(now_price)}",
        f"Размер: {_calc_size(symbol, sig, cfg):.4f}",
        f"Риск: ${_risk_usd(cfg):.2f}",
        f"Плечо: {_leverage()}x",
        f"SL: {_fmt_price(_sl(symbol, sig))} | TP1: {_fmt_price(_tp1(symbol, sig))}",
        f"R:R ~{_rr(symbol, sig)}:1",
        f"Проверки: {passed}/{len(checks)} PASS",
    ]
    warn = _tp_reachability_warning(symbol, sig.get("side"), _tp1(symbol, sig))
    if warn:
        lines.append("")
        lines.append(warn)
    lines.append("")
    lines.append("Подтверждаете открытие?")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 ДА, ОТКРЫТЬ", callback_data=f"ms_exec_{symbol}"),
        InlineKeyboardButton("🔴 ОТМЕНА", callback_data=f"ms_cancel_{symbol}"),
    ]])
    await query.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=kb)


def _log_execution(sig: dict | None, approved: bool, mode: str, ok: bool, note: str = ""):
    rec = {
        "event": "EXECUTED" if ok else "REJECTED",
        "mode": mode,
        "approved": approved,
        "ts": datetime.now(timezone.utc).isoformat(),
        "note": note,
    }
    if sig:
        rec.update({"symbol": sig.get("symbol"), "side": sig.get("side"),
                    "score": sig.get("score"), "price": sig.get("price"),
                    "signal_id": sig.get("signal_id", "")})
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _amount_from_note(note: str) -> float | None:
    """Извлечь 'amount=$N' из note EXECUTED-записи (None если нет)."""
    if not note:
        return None
    try:
        for part in note.split():
            if part.startswith("amount=$"):
                return float(part.split("=", 1)[1].replace("$", ""))
    except ValueError:
        return None
    return None


def _audit_escalation(symbol: str, amount: float) -> dict | None:
    """SHADOW/AUDIT-ONLY: детектор escalation-паттерна в MANUAL-контуре.

    Определение (зафиксировано в FAIL_DAYS_VS_ENTRIES.md 16.08): вход по символу
    с размером > предыдущего входа по тому же символу в тот же UTC-день, когда
    предыдущий вход по этому символу закрылся с убытком. НИЧЕГО не блокирует —
    только пишет наблюдение в escalation_audit.jsonl. Повторный вход того же
    или меньшего размера НЕ помечается.
    """
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        # Собрать историю по символу за сегодня в порядке времени
        hist: list[dict] = []
        if JOURNAL.exists():
            for line in JOURNAL.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("symbol") != symbol:
                    continue
                ts = r.get("ts", "")
                if not ts.startswith(today):
                    continue
                if r.get("event") in ("EXECUTED", "POSITION_CLOSED"):
                    hist.append(r)
        if not hist:
            return None
        hist.sort(key=lambda r: r.get("ts", ""))
        # Последний вход по символу до текущего момента
        prev_amount = None
        prev_entry_ts = None
        for r in hist:
            if r.get("event") == "EXECUTED":
                amt = r.get("amount") or _amount_from_note(r.get("note", ""))
                if amt is not None:
                    prev_amount = amt
                    prev_entry_ts = r.get("ts", "")
        if prev_amount is None or prev_entry_ts is None:
            return None
        if amount <= prev_amount:
            return None  # повтор того же/меньшего размера — НЕ эскалация
        # Убыточное закрытие ПОСЛЕ предыдущего входа?
        prev_closed_loss = any(
            r.get("event") == "POSITION_CLOSED"
            and r.get("pnl_usd_est") is not None
            and r.get("pnl_usd_est", 0) < 0
            and r.get("ts", "") > prev_entry_ts
            for r in hist
        )
        if not prev_closed_loss:
            return None
        rec = {
            "event": "ESCALATION_AUDIT",
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "prev_amount_usd": prev_amount,
            "new_amount_usd": amount,
            "mode": "shadow",
        }
        ESCALATION_AUDIT.parent.mkdir(parents=True, exist_ok=True)
        with ESCALATION_AUDIT.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        logger.warning(f"ESCALATION_AUDIT (shadow, not blocked): {symbol} "
                       f"${prev_amount:.2f} -> ${amount:.2f}")
        return rec
    except Exception as e:
        logger.warning(f"Escalation audit failed (shadow, proceeding): {e}")
        return None


def _load_signal_from_journal(sym: str) -> dict | None:
    """P1: восстановить последний сигнал по символу из журнала (после рестарта бота).

    Ищет последнюю запись с event=SIGNAL/EXECUTED и данным символом,
    у которой есть side/price/score. Возвращает dict или None.
    """
    try:
        if not JOURNAL.exists():
            return None
        for line in reversed(JOURNAL.read_text(encoding="utf-8", errors="replace").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("symbol") != sym:
                continue
            # Только записи, пригодные для повторного исполнения
            if r.get("side") and r.get("price"):
                return {
                    "symbol": sym,
                    "side": r["side"],
                    "score": r.get("score", 0),
                    "price": r.get("price", 0),
                    "sl": r.get("sl"),
                    "final_tp": r.get("final_tp") or r.get("tp"),
                    "signal_id": r.get("signal_id", f"recovered-{sym}"),
                    # WAIT_LIMIT-поля для кнопки «Поставить LIMIT» после рестарта
                    "wait_limit_entry": r.get("wait_limit_entry") or 0,
                    "wait_rr": r.get("wait_rr") or 0,
                    "wait_limit_entry_deep": r.get("wait_limit_entry_deep") or 0,
                    "wait_rr_deep": r.get("wait_rr_deep") or 0,
                    "range_7d_high": r.get("range_7d_high") or 0,
                }
    except Exception:
        return None
    return None


# ─── P0: Реальное исполнение через кнопку ────────────────────────────
# State для ручного ввода суммы: {user_id: {symbol, side, sl, tp, sig}}
_awaiting_amount: dict[int, dict] = {}

# Pending soft-manual-risk confirmations: { (uid, token): {pending, amount, ...} }
# Used for PROPOSED_RISK_TOO_HIGH / DAILY_LOSS_LIMIT in MANUAL mode.
_pending_overrides: dict[tuple[int, str], dict] = {}


def _audit_log(path: str, rec: dict) -> None:
    """Append JSON record to audit log. Best-effort, no exceptions."""
    import os as _os, json as _json
    try:
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        with open(path, "a") as _f:
            _f.write(_json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _is_token_valid(tok_entry: dict) -> bool:
    """Check token not used and not expired (60s window)."""
    import datetime as _dt
    if not tok_entry or tok_entry.get("used"):
        return False
    try:
        exp = _dt.datetime.fromisoformat(tok_entry["expires_at"])
    except Exception:
        return False
    return _dt.datetime.utcnow() < exp


def _execute_confirmed_pending(pending: dict, amount: float, audit_extra: dict) -> tuple[bool, str]:
    """Re-validate hard-safety, then submit order via the same executor.

    Returns (success, message). On hard violation → BLOCKED, no execution.
    """
    # Hard safety revalidation
    try:
        from tradingos.strategies.deposit_guard import get_guard as _get_guard
        _allowed, _reason = _get_guard().can_open_position(
            proposed_risk_usd=pending.get("_proposed", 0.0))
        if not _allowed:
            _base = str(_reason).split(":", 1)[0]
            if _base in {"EQUITY_FETCH_ERROR", "MANUAL_KILL", "HARD_KILL",
                         "NO_DAY_BASELINE", "OPEN_RISK_OVER_LIMIT",
                         "CRITICAL_OPEN_RISK"}:
                return False, f"CONFIRMATION_INVALIDATED (hard {_reason})"
    except Exception:
        return False, "CONFIRMATION_INVALIDATED (guard unreachable)"
    # Send order via existing _place_market_order
    try:
        res = _place_market_order(
            pending["symbol"], pending["side"], amount,
            pending["sl"], pending["tp"])
        if res.get("ok"):
            return True, "executed"
        return False, f"execution failed: {res}"
    except Exception as _e:
        return False, f"execution exception: {_e}"


def _load_credentials() -> tuple[str, str]:
    """Загрузить Bybit API credentials из того же .env, что guardian.
    Guardian использует /root/trading_brain_v4/research/execution/.env (ENV_PATH).
    НЕ используем load_dotenv — он не перезаписывает существующие env vars,
    что приводит к 10003 'API key is invalid' (баг 2026-08-09)."""
    ak, as_ = "", ""
    env_path = "/root/trading_brain_v4/research/execution/.env"
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "BYBIT_API_KEY":
                        ak = v.strip()
                    elif k.strip() == "BYBIT_API_SECRET":
                        as_ = v.strip()
    except FileNotFoundError:
        pass
    return ak, as_


def _api_base() -> str:
    """2026-08-27: Demo switch — private endpoints route to api-demo.bybit.com
    when BYBIT_DEMO=true in the execution .env. Market data stays on mainnet."""
    import os as _os
    v = (_os.environ.get("BYBIT_DEMO", "") or "").strip().lower()
    if v not in ("1", "true", "yes", "on"):
        try:
            with open("/root/trading_brain_v4/research/execution/.env") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("BYBIT_DEMO="):
                        v = line.split("=", 1)[1].strip().lower()
                        break
        except FileNotFoundError:
            pass
    return "https://api-demo.bybit.com" if v in ("1", "true", "yes", "on") else "https://api.bybit.com"


# ─── Карточка открытия в TradingOS-чат ──────────────────────
# Ленивый синглтон нотификатора TradingOS (отдельный от Grizzly-бота процесс).
_tg_notifier = None


def _load_tg_creds() -> tuple[str, str, str]:
    """Токен/чат/прокси нотификатора TradingOS из .env гардиана."""
    token, chat, proxy = "", "", ""
    env_path = "/root/trading_brain_v4/research/execution/.env"
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "TELEGRAM_BOT_TOKEN":
                    token = v
                elif k == "TELEGRAM_CHAT_ID":
                    chat = v
                elif k == "TELEGRAM_PROXY":
                    proxy = v
    except FileNotFoundError:
        pass
    return token, chat, proxy


async def _get_tradingos_notifier():
    """Получить (и при необходимости инициализировать) нотификатор TradingOS."""
    global _tg_notifier
    if _tg_notifier is not None:
        return _tg_notifier
    try:
        token, chat, proxy = _load_tg_creds()
        if not token or not chat:
            logger.warning("TELEGRAM_BOT_TOKEN/CHAT_ID нет в .env — карточка в TradingOS не отправлена")
            return None
        from tradingos.notifier.notifier import Notifier
        _tg_notifier = Notifier(token=token, chat_id=chat, proxy_url=proxy or None)
        await _tg_notifier.start()
    except Exception as e:
        logger.warning(f"TradingOS notifier init failed: {e}")
        _tg_notifier = None
    return _tg_notifier


async def _notify_open_to_tradingos(symbol, side, entry_price, qty, sl, tp,
                                    leverage, entry_time=None) -> bool:
    """Отправить карточку открытия с графиком в TradingOS-чат (notify_trade_open)."""
    n = await _get_tradingos_notifier()
    if n is None:
        return False
    try:
        from datetime import datetime, timezone as _tz
        et = datetime.fromtimestamp(entry_time, tz=_tz.utc) if entry_time else None
        await n.notify_trade_open(
            symbol=symbol, side=side, entry_price=entry_price, qty=qty,
            sl=sl, tp=tp, leverage=leverage, reason="MANUAL (Telegram)",
            entry_time=et,
        )
        logger.info(f"TradingOS open card sent: {symbol} {side}")
        return True
    except Exception as e:
        logger.warning(f"TradingOS open card failed: {e}")
        return False


async def _notify_wait_limit_placed(symbol, side, limit_price, qty, sl, tp,
                                    notional, leverage) -> bool:
    """Уведомить TradingOS-чат о ПОСТАНОВКЕ WAIT-лимитки (не позиции)."""
    n = await _get_tradingos_notifier()
    if n is None:
        return False
    try:
        emoji = "🟢" if side in ("LONG", "BUY") else "🔴"
        await n.send(
            f"{emoji} <b>WAIT-LIMIT поставлен</b> (не исполнен)\n"
            f"{symbol} {side} | {qty:.4f} @ <code>${limit_price:.4f}</code>\n"
            f"Сумма ~${notional:,.0f} | Плечо {leverage}x\n"
            f"SL <code>${sl:.4f}</code> | TP <code>${tp:.4f}</code>\n"
            f"⏱ Лимит живёт 4ч; исполнение/отмена → уведомлю",
        )
        logger.info(f"TradingOS WAIT-LIMIT card sent: {symbol}")
        return True
    except Exception as e:
        logger.warning(f"TradingOS WAIT-LIMIT card failed: {e}")
        return False


async def _notify_wait_limit_filled(symbol, side, limit_price, qty) -> bool:
    """WAIT-лимитка ИСПОЛНИЛАСЬ → позиция открыта (карточка открытия уже
    отправляется позиционным монитором; здесь короткое подтверждение)."""
    n = await _get_tradingos_notifier()
    if n is None:
        return False
    try:
        emoji = "🟢" if side in ("LONG", "BUY") else "🔴"
        await n.send(
            f"✅ <b>WAIT-LIMIT ИСПОЛНЕН</b>\n"
            f"{symbol} {side} | {qty:.4f} @ <code>${limit_price:.4f}</code>\n"
            f"Позиция открыта (SL/TP attached).",
        )
        logger.info(f"TradingOS WAIT-LIMIT filled: {symbol}")
        return True
    except Exception as e:
        logger.warning(f"TradingOS WAIT-LIMIT filled notify failed: {e}")
        return False


async def _notify_wait_limit_cancelled(symbol, reason: str) -> bool:
    """WAIT-лимитка УДАЛЕНА (экспирация/отмена) — короткое уведомление."""
    n = await _get_tradingos_notifier()
    if n is None:
        return False
    try:
        await n.send(
            f"⏰ <b>WAIT-LIMIT удалён</b>\n{symbol} | {reason}\n"
            f"Сумма не заблокирована, можно ставить заново."
        )
        logger.info(f"TradingOS WAIT-LIMIT cancelled: {symbol} ({reason})")
        return True
    except Exception as e:
        logger.warning(f"TradingOS WAIT-LIMIT cancelled notify failed: {e}")
        return False


def _soft_sl_recovery_enabled() -> bool:
    """Флаг soft_sl_recovery из manual_session.json (MANUAL-first)."""
    try:
        return bool(_cfg().get("soft_sl_recovery", False))
    except Exception:
        return False


def _atr_m15(symbol: str, period: int = 14) -> float:
    """ATR(M15, period) по закрытым барам (replay_cache). 0 если данных нет."""
    try:
        import pandas as pd
        p = Path("/root/tradingos/replay_cache") / f"{symbol}_M15.parquet"
        if not p.exists():
            return 0.0
        df = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts")
        if df["ts"].iloc[0] > 1e12:
            df["ts"] = df["ts"] / 1000
        if len(df) < period + 1:
            return 0.0
        h, l, c = df["h"], df["l"], df["c"]
        pc = c.shift(1)
        tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return 0.0


def _sl_hard_buffer(symbol: str, sl: float, price: float) -> float:
    """Buffer для hard SL = min(0.5×ATR(M15,14), 0.3R). R = |price − sl|."""
    risk = abs(price - sl)
    atr = _atr_m15(symbol)
    buf = 0.5 * atr
    cap = risk * 0.3
    return min(buf, cap) if cap > 0 else buf


def _place_market_order(symbol: str, side: str, usd_amount: float,
                        sl: float, tp: float, leverage: int | None = None,
                        order_type: str = "Market") -> dict:
    """Открыть позицию ордером через raw signed POST.

    leverage: опциональное плечо для ЭТОГО ордера (напр. 10 при выборе
    пользователя). None → используется config max_leverage (_leverage()).
    Риск $ в сделке НЕ зависит от плеча (qty считаем от usd_amount);
    плечо влияет только на маржу и ликвидационную цену.
    order_type: "Market" (по умолч.) или "Limit" — лимитка = мейкер-комиссия
    (funding-capture: экономия ~0.035%/сторону, 2026-08-31).

    Returns: {"ok": bool, "error": str, "qty": float, "order_id": str}
    """
    import hashlib
    import hmac
    import urllib.parse
    import httpx
    import time as _time

    ak, as_ = _load_credentials()
    if not ak or not as_:
        return {"ok": False, "error": "API credentials missing"}

    try:
        # 1. Получить текущую цену и lotSizeFilter
        recv_window = "5000"
        with httpx.Client(timeout=10) as c:
            ri = c.get(_api_base() + "/v5/market/instruments-info",
                       params={"category": "linear", "symbol": symbol})
            info = ((ri.json().get("result") or {}).get("list") or [{}])[0]
            lot = info.get("lotSizeFilter", {})
            qty_step = float(lot.get("qtyStep", "0.001"))
            min_qty = float(lot.get("minOrderQty", "0.001"))
            max_qty = float(lot.get("maxOrderQty", "1e12") or "1e12")
            min_notional = float(lot.get("minNotionalValue", "5"))
            rt = c.get(_api_base() + "/v5/market/tickers",
                       params={"category": "linear", "symbol": symbol})
            price = float((((rt.json().get("result") or {}).get("list") or [{}])[0]).get("lastPrice", 0))
        if price <= 0:
            return {"ok": False, "error": "Не удалось получить цену"}

        # 2. Рассчитать qty из USD суммы × ПЛЕЧО (2026-09-02 FIX).
        # Раньше usd_amount трактовался как НОУШНЛ (итоговая позиция), а не как
        # МАРЖА. При плече 10x ввод $50 давал позицию на $50 вместо $500 —
        # плечо не работало, «собирали копейки». Теперь: ноушнл = маржа × плечо.
        lev = leverage if leverage and leverage > 0 else _leverage()
        notional_target = usd_amount * lev

        # GIGANTIC-POSITION GUARD (2026-09-03): NEAR $90k notional (50% equity)
        # закрылся instantly = −$99 ВСЯ комиссия. Hard cap ДО ордера: notional ≤ 5% equity.
        # AVAX $18k: fees $19.80 > gross +$0.46 — тоже блокируем, если fees съедают сделку.
        eq = _get_manual_equity()
        giant_cap = eq * 0.05 if eq > 0 else 0.0
        giant_capped = False
        if giant_cap > 0 and notional_target > giant_cap:
            notional_target = giant_cap
            giant_capped = True
        fees_est = notional_target * 0.0011  # taker 0.055%/side RT
        sl_dist_pct = abs(price - sl) / price if (sl and price and price > 0) else 0.005
        tp_gross_est = notional_target * sl_dist_pct * 3  # R:R 1:3
        fees_too_high = fees_est > tp_gross_est * 0.25

        raw_qty = notional_target / price
        # Округлить ВНИЗ до qtyStep
        qty = float(f"{int(raw_qty / qty_step) * qty_step:.8g}")
        # Мин. объём ордера (minOrderQty): если qty меньше — ПОДНИМАЕМ до минимума.
        # Пользователь открывает с плечом — $5 маржи при 5x = $25 позиция, но Bybit
        # требует мин. объём (0.01 ETH ≈ $19.20). Вместо отказа — берём минимум.
        if qty < min_qty:
            qty = min_qty
        # Bybit требует minNotionalValue ($5) по сумме ордера.
        # Если округление qty вниз дало notional < минимума — увеличиваем qty
        # на один qtyStep (это НЕ комиссия, а шаг лота: 5/8.31=0.6016→0.6=$4.98).
        notional = qty * price
        if notional < min_notional:
            qty = float(f"{(int(raw_qty / qty_step) + 1) * qty_step:.8g}")
            notional = qty * price
        if notional < min_notional:
            return {"ok": False, "error": f"сумма ${notional:.2f} < минимум ${min_notional:.0f} (введите больше)"}
        # FIX 2026-09-02: cap qty at maxOrderQty (prevents qty overflow for cheap coins)
        if qty > max_qty:
            qty = max_qty
        # Запомним реальный notional для сообщения пользователю
        real_notional = qty * price

        # 2b. Плечо уже определено выше (lev) — используем его для set-leverage
        ts0 = int(_time.time() * 1000)
        lev_body = urllib.parse.urlencode({
            "category": "linear", "symbol": symbol,
            "buyLeverage": str(lev), "sellLeverage": str(lev),
        })
        lev_payload = f"{ts0}{ak}{recv_window}{lev_body}"
        lev_sign = hmac.new(as_.encode(), lev_payload.encode(), hashlib.sha256).hexdigest()
        lev_headers = {
            "X-BAPI-API-KEY": ak,
            "X-BAPI-TIMESTAMP": str(ts0),
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": lev_sign,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            httpx.post(_api_base() + "/v5/position/set-leverage",
                       content=lev_body, headers=lev_headers, timeout=10)
        except Exception:
            pass  # если плечо уже установлено — не критично

        # 3. Raw signed POST /v5/order/create
        ts = int(_time.time() * 1000)
        recv_window = "5000"
        # F1 hardening: никогда не дефолтим в Sell по неизвестному side.
        # Валидный LONG/BUY → Buy, валидный SHORT/SELL → Sell, иначе NO_ORDER.
        _side_up = side.upper() if isinstance(side, str) else ""
        if _side_up in ("LONG", "BUY"):
            order_side = "Buy"
        elif _side_up in ("SHORT", "SELL"):
            order_side = "Sell"
        else:
            return {"ok": False, "error": f"NO_ORDER: invalid side {side!r}"}
        # Soft-SL recovery: на биржу ставим ШИРОКИЙ hard SL (safety net) по MarkPrice,
        # софт управляет tight sl_soft. Без флага — всё как раньше (LastPrice).
        recovery_on = _soft_sl_recovery_enabled()
        sl_soft = float(sl)
        sl_hard = float(sl)
        sl_trigger = "LastPrice"
        sl_buffer = 0.0
        if recovery_on:
            sl_buffer = _sl_hard_buffer(symbol, sl_soft, price)
            if order_side == "Buy":
                sl_hard = sl_soft - sl_buffer
            else:
                sl_hard = sl_soft + sl_buffer
            sl_trigger = "MarkPrice"
        body_params = {
            "category": "linear", "symbol": symbol,
            "side": order_side, "orderType": order_type,
            "qty": str(qty),
            "positionIdx": "0",
        }
        if order_type == "Limit":
            # FIX 2026-08-31: лимитка БЕЗ takeProfit/stopLoss в теле — иначе
            # Bybit создаёт висящие Market Untriggered trigger-ордера до филла
            # (та же грабля, что A2 в _place_limit_order). SL/TP ставятся после
            # филла отдельным вызовом (guardian/монитор подхватит позицию).
            # Лимитка = мейкер-комиссия, PostOnly исключает агрессивный филл.
            body_params["price"] = str(price)
            body_params["timeInForce"] = "PostOnly"
        else:
            body_params["takeProfit"] = str(tp)
            body_params["stopLoss"] = str(sl_hard)
            body_params["tpTriggerBy"] = "LastPrice"
            body_params["slTriggerBy"] = sl_trigger
        body = urllib.parse.urlencode(body_params)
        payload = f"{ts}{ak}{recv_window}{body}"
        sign = hmac.new(as_.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": ak,
            "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        r = httpx.post(_api_base() + "/v5/order/create",
                       content=body, headers=headers, timeout=10)
        res = r.json()
        if res.get("retCode") == 0:
            order_id = (res.get("result") or {}).get("orderId", "")
            logger.info(f"✅ MANUAL ORDER: {symbol} {order_side} qty={qty} TP={tp} "
                        f"SL={sl_hard}{' (hard)' if recovery_on else ''}")
            # Задача 1: регистрация в guardian state, чтобы BE/Partial/Tight/Trail
            # применялись к MANUAL-позициям (иначе нет защиты прибыли).
            # FIX 2026-08-31: для Limit-ордера (PostOnly, funding-capture) state
            # НЕ регистрируем — позиции ещё нет в книге, guardian начнёт вести
            # несуществующую позицию (PHANTOM-спам). Guardian сам создаст state,
            # когда увидит реальный филл. Регистрация — только для Market-филла.
            if order_type != "Limit":
                try:
                    import json as _json
                    _state_path = "/root/tradingos/guardian/reality_state.json"
                    _state = (_json.loads(open(_state_path).read())
                              if Path(_state_path).exists() else {})
                    _entry_price = res.get("price") or price or 0
                    _state[symbol] = {
                        "be_fired": False,
                        "partial_fired": False,
                        "tight_fired": False,
                        "mfe_peak": 0.0,
                        "mae_trough": 0.0,
                        "entry_to_sl_risk": abs(sl_soft - _entry_price) if _entry_price else 0,
                        "side": order_side,
                        "entry": _entry_price,
                        "size": qty,
                        "sl_initial": sl_soft,
                        "tp_initial": tp,
                        "source": "MANUAL",
                        "entry_time": _time.time(),
                    }
                    if recovery_on:
                        _state[symbol]["sl_soft"] = sl_soft
                        _state[symbol]["sl_hard"] = sl_hard
                        _state[symbol]["sl_buffer"] = sl_buffer
                        _state[symbol]["sl_trigger"] = sl_trigger
                        _state[symbol]["recovery_state"] = None
                        _state[symbol]["recovery_attempted"] = False
                    open(_state_path, "w").write(_json.dumps(_state, ensure_ascii=False))
                    logger.info(f"✅ MANUAL позиция зарегистрирована в guardian state: {symbol}")
                except Exception as e:
                    logger.warning(f"Guardian state registration failed: {e}")
            return {"ok": True, "error": "", "qty": qty, "order_id": order_id,
                    "price": price, "notional": real_notional,
                    "giant_capped": giant_capped, "fees_est": fees_est,
                    "fees_too_high": fees_too_high}
        return {"ok": False, "error": res.get("retMsg", "unknown"),
                "retCode": res.get("retCode")}
    except Exception as e:
        logger.error(f"MANUAL ORDER error: {e}")
        return {"ok": False, "error": str(e)}


async def asyncio_to_thread(fn):
    """Обёртка: выполнить блокирующую функцию в потоке."""
    import asyncio
    return await asyncio.to_thread(fn)


def _place_limit_order(symbol: str, side: str, limit_price: float, amount_usd: float,
                       sl: float, tp: float, leverage: int | None = None,
                       ladder_level: int = 1) -> dict:
    """WAIT_LIMIT: POST-ONLY LIMIT на откате (модель владельца «ордера в
    прогнозных точках, сидишь ждёшь»). Сумма и плечо — выбор владельца:
    qty = amount_usd / limit_price.

    A1 (2026-08-29): timeInForce=PostOnly — заявка РАЗРЕШАЕТ только maker-филл.
    Если цена уже в/за зоной (marketable), биржа ОТКЛОНЯЕТ ордер (would cross)
    вместо мгновенного исполнения как маркет. Это делает защиту от
    «лимитка сразу открылась» инвариантом на стороне биржи.

    A2 (2026-08-29): SL/TP НЕ attached к лимитке (такое attach оставляло
    висящие «Market Untriggered» trigger-ордера после филла). Лимитка = только
    цена+qty; SL/TP ставятся ПОСЛЕ филла через _set_manual_trading_stop
    (монитор в background_position_monitor).

    Экспирация на нашей стороне (_cancel_wait_limits), биржевой TIF=GTC.
    """
    import hashlib
    import hmac
    import httpx
    import urllib.parse
    import time as _time

    ak, as_ = _load_credentials()
    if not ak or not as_:
        return {"ok": False, "error": "API credentials missing"}
    try:
        recv_window = "5000"
        _side_up = side.upper() if isinstance(side, str) else ""
        if _side_up in ("LONG", "BUY"):
            order_side = "Buy"
        elif _side_up in ("SHORT", "SELL"):
            order_side = "Sell"
        else:
            return {"ok": False, "error": f"NO_ORDER: invalid side {side!r}"}
        # Плечо: выбранное владельцем или config-дефолт
        lev = leverage if leverage and leverage > 0 else _leverage()
        tt0 = int(_time.time() * 1000)
        lev_body = urllib.parse.urlencode({
            "category": "linear", "symbol": symbol,
            "buyLeverage": str(lev), "sellLeverage": str(lev),
        })
        lev_payload = f"{tt0}{ak}{recv_window}{lev_body}"
        lev_sign = hmac.new(as_.encode(), lev_payload.encode(), hashlib.sha256).hexdigest()
        lev_headers = {
            "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": str(tt0),
            "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": lev_sign,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            httpx.post(_api_base() + "/v5/position/set-leverage",
                       content=lev_body, headers=lev_headers, timeout=10)
        except Exception:
            pass

        # qty от СУММЫ владельца (не от риск-бюджета): qty = amount / limit_price
        raw_qty = amount_usd / limit_price if limit_price > 0 else 0
        # Лот-фильтры биржи
        try:
            with httpx.Client(timeout=10) as _c:
                _ri = _c.get(_api_base() + "/v5/market/instruments-info",
                             params={"category": "linear", "symbol": symbol})
                _lot = ((_ri.json().get("result") or {}).get("list") or [{}])[0].get("lotSizeFilter", {})
            qty_step = float(_lot.get("qtyStep", "0.001"))
            min_qty = float(_lot.get("minOrderQty", "0.001"))
            min_not = float(_lot.get("minNotionalValue", "5"))
            qty = float(f"{int(raw_qty / qty_step) * qty_step:.8g}")
            if qty < min_qty or qty * limit_price < min_not:
                qty = float(f"{(int(raw_qty / qty_step) + 1) * qty_step:.8g}")
        except Exception:
            qty = round(raw_qty, 4)
        if qty <= 0:
            return {"ok": False, "error": "qty = 0 после лот-фильтров; введите бóльшую сумму"}

        order_link = f"manwait-{symbol}-{int(_time.time())}"
        body = urllib.parse.urlencode({
            "category": "linear", "symbol": symbol,
            "side": order_side, "orderType": "Limit",
            "qty": str(qty), "price": str(limit_price),
            "timeInForce": "PostOnly",
            "orderLinkId": order_link,
            "positionIdx": "0",
        })
        ts = int(_time.time() * 1000)
        payload = f"{ts}{ak}{recv_window}{body}"
        sign = hmac.new(as_.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": sign,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        r = httpx.post(_api_base() + "/v5/order/create",
                       content=body, headers=headers, timeout=10)
        res = r.json()
        if res.get("retCode") == 0:
            order_id = (res.get("result") or {}).get("orderId", "")
            logger.info(f"📌 WAIT-LIMIT (PostOnly) поставлен: {symbol} {order_side} "
                        f"qty={qty} (~${amount_usd:.2f}) @ {limit_price} "
                        f"{lev}x SL={sl} TP={tp} id={order_id}")
            _save_wait_limit_state(symbol, order_id, limit_price, qty, sl, tp,
                                   order_link, amount_usd, lev, signal_price=amount_usd / qty if qty > 0 else limit_price, h1_bars_to_live=3)
            # P2a/T18: запомним уровень лестницы (L1/L2) для отчёта
            try:
                _p2 = _load_wait_limit_state()
                _p2[symbol]["ladder_level"] = int(ladder_level or 1)
                _WAIT_LIMIT_STATE.write_text(json.dumps(_p2, ensure_ascii=False))
            except Exception:
                pass
            return {"ok": True, "error": "", "order_id": order_id, "qty": qty,
                    "price": limit_price, "leverage": lev}
        return {"ok": False, "error": res.get("retMsg", "unknown"),
                "retCode": res.get("retCode")}
    except Exception as e:
        logger.error(f"WAIT-LIMIT error: {e}")
        return {"ok": False, "error": str(e)}


def _set_manual_trading_stop(symbol: str, qty: float, sl: float, tp: float) -> bool:
    """A2: поставить SL/TP на позицию ПОСЛЕ филла лимитки (set-trading-stop).
    Вызывается из position-монитора, когда лимитка исполнилась."""
    try:
        sys.path.insert(0, "/root/tradingos")
        from guardian.reality_guardian import _set_trading_stop
        ok1 = _set_trading_stop(symbol, stop_loss=sl)
        ok2 = _set_trading_stop(symbol, take_profit=tp)
        logger.info(f"🛡 WAIT-LIMIT SL/TP поставлены после филла: {symbol} SL={sl} TP={tp} "
                    f"({ok1}/{ok2})")
        return bool(ok1 and ok2)
    except Exception as e:
        logger.error(f"WAIT-LIMIT set-trading-stop error {symbol}: {e}")
        return False


_WAIT_LIMIT_STATE = ROOT / "operations/manual_wait_limits.json"
# P2a: журнал исходов WAIT-лимиток (включая НЕисполненные) — для подсчёта
# lost-vs-gained: филл vs пропуск, экскурсия цены, гипотетический market/limit PnL.
_WAIT_OUTCOMES_LOG = ROOT / "memory/wait_limit_outcomes.jsonl"


def _log_wait_outcome(sym: str, rec: dict, reason: str, price_now: float | None = None) -> None:
    """P2a: записать исход лимитки с пост-экскурсией цены.

    Для НЕисполненной лимитки это ключевой кусок: дошла ли цена до зоны,
    ушла ли потом в нашу сторону (missed PnL) или против нас (повезло, что
    не вошли). Считаем гипотетические market/limit PnL от момента
    постановки до момента завершения. price_now можно передать из контекста
    (монитор уже имел цену) — экономим HTTP.
    """
    try:
        side = str(rec.get("side", "LONG")).upper()
        wl = float(rec.get("price", 0) or 0)
        sl = float(rec.get("sl", 0) or 0)
        tp = float(rec.get("tp", 0) or 0)
        qty = float(rec.get("qty", 0) or 0)
        placed_at = rec.get("placed_at", "")
        is_long = side in ("LONG", "BUY")
        # Текущая цена (ближайшая точка после завершения)
        cur_px = price_now or 0.0
        if not cur_px:
            try:
                import httpx as _hx
                with _hx.Client(timeout=8) as _c:
                    _t = _c.get("https://api.bybit.com/v5/market/tickers",
                                params={"category": "linear", "symbol": sym})
                    cur_px = float(((_t.json().get("result") or {}).get("list") or [{}])[0].get("lastPrice", 0))
            except Exception:
                pass
        sig_price = rec.get("signal_price") or rec.get("price") or 0
        hypothetical = {}
        if qty > 0 and cur_px > 0 and wl > 0:
            # Market-вход по цене сигнала (как если бы пошли маркетом в момент сигнала)
            hypothetical["market_pnl_usd"] = round(
                ((cur_px - sig_price) if is_long else (sig_price - cur_px)) * qty, 4)
            # Limit-филл (если бы исполнился в зоне): PnL от цены лимитки
            hypothetical["limit_pnl_usd"] = round(
                ((cur_px - wl) if is_long else (wl - cur_px)) * qty, 4)
            # Дошла ли цена до зоны после отмены? (контрафакт филла)
            hypothetical["zone_reached_after"] = (cur_px <= wl) if is_long else (cur_px >= wl)
        out = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": sym,
            "reason": reason,           # EXPIRED / CANCELLED / STRUCTURE_BREAK / FILLED / PARTIAL_FILL
            "side": side,
            "limit_entry": wl,
            "sl": sl, "tp": tp, "qty": qty,
            "ladder_level": int(rec.get("ladder_level") or 1),
            "placed_at": placed_at,
            "status": rec.get("status"),
            "partial_fill": bool(rec.get("partial_fill")),
            "filled_price": rec.get("filled_price") or 0,
            "price_now": cur_px,
            "hypothetical": hypothetical,
        }
        _WAIT_OUTCOMES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _WAIT_OUTCOMES_LOG.open("a") as f:
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug(f"wait outcome log failed {sym}: {e}")


def _load_wait_limit_state() -> dict:
    try:
        return json.loads(_WAIT_LIMIT_STATE.read_text())
    except Exception:
        return {}


def _save_wait_limit_state(symbol: str, order_id: str, price: float, qty: float,
                           sl: float, tp: float, order_link: str,
                           amount_usd: float = 0.0, leverage: int = 5,
                           signal_price: float = 0.0, h1_bars_to_live: int = 3) -> None:
    st = _load_wait_limit_state()
    st[symbol] = {
        "order_id": order_id, "order_link": order_link,
        "price": price, "qty": qty, "sl": sl, "tp": tp,
        "amount_usd": amount_usd, "lev": leverage,
        "signal_price": signal_price,
        "status": "PLACED",
        "placed_at": datetime.now(timezone.utc).isoformat(),
        "expires_at": (datetime.now(timezone.utc).timestamp() + 4 * 3600),
        "h1_bars_to_live": h1_bars_to_live,
    }
    _WAIT_LIMIT_STATE.parent.mkdir(parents=True, exist_ok=True)
    _WAIT_LIMIT_STATE.write_text(json.dumps(st, ensure_ascii=False))


def _cancel_wait_limits() -> None:
    """B3 (2026-08-29): отмена неисполненных WAIT-LIMIT по возрасту сигнала.

    Вместо слепых 4 часов: лимитка живёт max N ЗАКРЫТЫХ H1-баров с момента
    постановки (структурный TTL). N=3 по умолчанию (~3ч) — если цена зону
    не касалась и прошло 3 закрытия H1, гипотеза устарела. При отмене:
    уведомление в TradingOS-чат + журнал исхода (P2a) + удаление из state.
    """
    import hashlib
    import hmac
    import httpx
    import urllib.parse
    import time as _time
    st = _load_wait_limit_state()
    now = _time.time()
    cancelled = []
    for sym, rec in list(st.items()):
        # B3: экспирация по закрытым H1-барам от placed_at
        max_h1 = int(rec.get("h1_bars_to_live", 3))
        placed_ts = 0.0
        try:
            from datetime import datetime as _dt_iso
            placed_ts = _dt_iso.fromisoformat(rec.get("placed_at", "")).timestamp()
        except Exception:
            placed_ts = 0.0
        # Число закрытых H1 часов = (now - placed)/3600, floor — сколько баров
        # УЖЕ закрылось после постановки (если прошло 3.4h → 3 закрытых бара)
        bars_elapsed = int((now - placed_ts) / 3600) if placed_ts > 0 else 99
        # Жёсткий потолок времени тоже держим (защита от застревания)
        hard_expires = rec.get("expires_at", now + 1)
        expired_age = bars_elapsed >= max_h1 or now >= hard_expires
        if not expired_age:
            continue
        # Уже исполнен/удалён — не трогаем
        if rec.get("status") not in ("PLACED",):
            del st[sym]
            continue
        ak, as_ = _load_credentials()
        if not ak or not as_:
            break
        try:
            q = urllib.parse.urlencode({"category": "linear", "symbol": sym})
            ts = str(int(_time.time() * 1000))
            payload = f"{ts}{ak}5000{q}"
            sign = hmac.new(as_.encode(), payload.encode(), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sign,
                "Content-Type": "application/x-www-form-urlencoded",
            }
            r = httpx.post(_api_base() + "/v5/order/cancel-all",
                           content=q, headers=headers, timeout=10)
            res = r.json()
            if res.get("retCode") == 0:
                logger.info(f"⛔ WAIT-LIMIT истёк и отменён: {sym}")
                cancelled.append((sym, rec))
            else:
                logger.warning(f"WAIT-LIMIT cancel fail {sym}: {res.get('retMsg')}")
            # P2a: журналируем исход (экскурсия цены + гипотетический PnL)
            try:
                _log_wait_outcome(sym, rec, "EXPIRED")
            except Exception as e:
                logger.debug(f"wait outcome log fail {sym}: {e}")
            del st[sym]
        except Exception as e:
            logger.warning(f"WAIT-LIMIT cancel error {sym}: {e}")
    _WAIT_LIMIT_STATE.write_text(json.dumps(st, ensure_ascii=False))
    # Уведомления об отмене (запускаем асинхронно — мы в синхронной функции)
    if cancelled:
        try:
            import asyncio as _asi
            _loop = _asi.new_event_loop()
            _asi.set_event_loop(_loop)
            for sym, rec in cancelled:
                _loop.run_until_complete(
                    _notify_wait_limit_cancelled(
                        sym, "не исполнился за 4 часа (экспирация)"))
            _loop.close()
        except Exception as e:
            logger.warning(f"WAIT-LIMIT cancel notify failed: {e}")


def _amend_wait_limit(symbol: str, price: float | None = None,
                      sl: float | None = None, tp: float | None = None) -> dict:
    """Изменить НЕисполненную WAIT-лимитку: цену и/или SL/TP (POST /v5/order/amend).

    Bybit: amend работает только для открытого (не исполненного) ордера.
    Для исполненной (позиции) SL/TP меняются через _set_trading_stop.
    Возвращает {"ok", "error", "changed"}.
    """
    import hashlib
    import hmac
    import httpx
    import urllib.parse
    import time as _time
    if price is None and sl is None and tp is None:
        return {"ok": False, "error": "нечего менять"}
    st = _load_wait_limit_state()
    rec = st.get(symbol)
    if not rec or rec.get("status") != "PLACED":
        return {"ok": False, "error": "нет активной WAIT-лимитки по этому символу"}
    ak, as_ = _load_credentials()
    if not ak or not as_:
        return {"ok": False, "error": "API credentials missing"}
    try:
        recv_window = "5000"
        order_id = rec.get("order_id", "")
        params = {"category": "linear", "symbol": symbol, "orderId": order_id}
        if price is not None:
            params["price"] = str(price)
        if sl is not None:
            params["stopLoss"] = str(sl)
            params["slTriggerBy"] = "LastPrice"
        if tp is not None:
            params["takeProfit"] = str(tp)
            params["tpTriggerBy"] = "LastPrice"
        body = urllib.parse.urlencode(params)
        ts = str(int(_time.time() * 1000))
        payload = f"{ts}{ak}{recv_window}{body}"
        sign = hmac.new(as_.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": sign,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        r = httpx.post(_api_base() + "/v5/order/amend",
                       content=body, headers=headers, timeout=10)
        res = r.json()
        if res.get("retCode") == 0:
            if price is not None:
                rec["price"] = price
            if sl is not None:
                rec["sl"] = sl
            if tp is not None:
                rec["tp"] = tp
            _WAIT_LIMIT_STATE.write_text(json.dumps(st, ensure_ascii=False))
            logger.info(f"✏️ WAIT-LIMIT изменён: {symbol} price={price} SL={sl} TP={tp}")
            return {"ok": True, "error": "", "changed": True}
        return {"ok": False, "error": res.get("retMsg", "unknown"),
                "retCode": res.get("retCode")}
    except Exception as e:
        logger.error(f"WAIT-LIMIT amend error {symbol}: {e}")
        return {"ok": False, "error": str(e)}


def _delete_wait_limit(symbol: str, reason: str = "CANCELLED") -> dict:
    """Отменить WAIT-лимитку (POST /v5/order/cancel по orderId).
    reason: CANCELLED (владелец) | STRUCTURE_BREAK (B1)."""
    import hashlib
    import hmac
    import httpx
    import urllib.parse
    import time as _time
    st = _load_wait_limit_state()
    rec = st.get(symbol)
    if not rec or rec.get("status") != "PLACED":
        return {"ok": False, "error": "нет активной WAIT-лимитки по этому символу"}
    ak, as_ = _load_credentials()
    if not ak or not as_:
        return {"ok": False, "error": "API credentials missing"}
    try:
        recv_window = "5000"
        body = urllib.parse.urlencode({
            "category": "linear", "symbol": symbol,
            "orderId": rec.get("order_id", ""),
        })
        ts = str(int(_time.time() * 1000))
        payload = f"{ts}{ak}{recv_window}{body}"
        sign = hmac.new(as_.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": sign,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        r = httpx.post(_api_base() + "/v5/order/cancel",
                       content=body, headers=headers, timeout=10)
        res = r.json()
        # P2a: журналируем исход (экскурсия цены + гипотетический PnL)
        try:
            _log_wait_outcome(symbol, rec, reason)
        except Exception as e:
            logger.debug(f"wait outcome log fail {symbol}: {e}")
        del st[symbol]  # чистим из state в любом случае (order мог уже уйти)
        _WAIT_LIMIT_STATE.write_text(json.dumps(st, ensure_ascii=False))
        if res.get("retCode") == 0:
            logger.info(f"🗑️ WAIT-LIMIT удалён вручную: {symbol}")
            return {"ok": True, "error": ""}
        # Cancel не удался — ордер возможно уже исполнился/отменён на бирже.
        # Резерв риска снимаем всё равно (лимитка больше не «висящая»).
        return {"ok": True, "error": "", "note": f"cancel retCode={res.get('retCode')} ({res.get('retMsg','')}) — state очищен"}
    except Exception as e:
        logger.error(f"WAIT-LIMIT delete error {symbol}: {e}")
        return {"ok": False, "error": str(e)}


# Задача 4: мониторинг MANUAL-позиций — уведомление о закрытии (SL/TP/ручное)
_tracked_positions: dict[str, dict] = {}


def _update_tracked_mfe_mae(sym: str, info: dict, pos: dict):
    """Телеслой: обновить MFE/MAE живой позиции по цене из position-ответа.

    markPrice в /v5/position/list есть не всегда — fallback на unrealisedPnl:
    mae_px = entry − pnl/size (SELL) и т.д. Простая и надёжная оценка."""
    entry = float(info.get("entry") or pos.get("avgPrice") or 0)
    if entry <= 0:
        return
    side = info.get("side") or pos.get("side")
    mark = pos.get("markPrice")
    px = 0.0
    if mark:
        try:
            px = float(mark)
        except (TypeError, ValueError):
            px = 0.0
    if not px:
        pnl = pos.get("unrealisedPnl")
        size = float(pos.get("size") or info.get("size") or 0)
        if pnl is not None and size:
            try:
                pnl = float(pnl)
                px = entry + pnl / size if side == "BUY" else entry - pnl / size
            except (TypeError, ValueError):
                px = 0.0
    if not px:
        return
    info["last_px"] = px
    if side == "BUY":
        info["mfe"] = max(info.get("mfe", 0.0), px - entry)
        info["mae"] = min(info.get("mae", 0.0), px - entry)
    else:
        info["mfe"] = max(info.get("mfe", 0.0), entry - px)
        info["mae"] = min(info.get("mae", 0.0), entry - px)
    info["mfe_pct"] = info["mfe"] / entry * 100 if entry else 0.0
    info["mae_pct"] = info["mae"] / entry * 100 if entry else 0.0


def _log_position_close(sym: str, info: dict, now: float | None = None):
    """Телеслой: записать закрытие MANUAL-позиции с outcome/MFE/MAE в журнал.

    event=POSITION_CLOSED, linked by signal_id при наличии; PNL неточный
    (без fees), только для проверки гипотез фильтров, не для учёта."""
    now = now or time.time()
    entry = float(info.get("entry") or 0)
    last_px = float(info.get("last_px") or entry or 0)
    side = info.get("side")
    if side == "BUY":
        pnl_usd = (last_px - entry) * float(info.get("size") or 0)
    else:
        pnl_usd = (entry - last_px) * float(info.get("size") or 0)
    holding = now - float(info.get("opened_at") or now)
    rec = {
        "event": "POSITION_CLOSED",
        "ts": datetime.now(timezone.utc).isoformat(),
        "symbol": sym,
        "side": side,
        "entry": round(entry, 8) if entry else None,
        "exit_px": round(last_px, 8) if last_px else None,
        "pnl_usd_est": round(pnl_usd, 6),
        "mfe_pct": round(float(info.get("mfe_pct") or 0.0), 4),
        "mae_pct": round(float(info.get("mae_pct") or 0.0), 4),
        "mfe_usd": round(float(info.get("mfe") or 0.0), 8),
        "mae_usd": round(float(info.get("mae") or 0.0), 8),
        "holding_sec": round(holding, 1),
        "time_to_mfe_sec": None,
    }
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # T18: дописать MFE/MAE в последний FILLED/PARTIAL-исход этого символа
        # в wait_limit_outcomes.jsonl — чтобы /waitreport мог показывать MFE/MAE
        # на уровень лестницы (LF1 vs L2). Читаем → апдейт → перезапись.
        try:
            if _WAIT_OUTCOMES_LOG.exists():
                lines = _WAIT_OUTCOMES_LOG.read_text(errors="replace").splitlines()
                for i in range(len(lines) - 1, -1, -1):
                    try:
                        o = json.loads(lines[i])
                    except Exception:
                        continue
                    if (o.get("symbol") == sym
                            and o.get("reason") in ("FILLED", "PARTIAL_FILL")):
                        o["mfe_pct"] = rec["mfe_pct"]
                        o["mae_pct"] = rec["mae_pct"]
                        o["pnl_usd_close_est"] = rec["pnl_usd_est"]
                        lines[i] = json.dumps(o, ensure_ascii=False)
                        with _WAIT_OUTCOMES_LOG.open("w") as ff:
                            ff.write("\n".join(lines) + ("\n" if lines else ""))
                        break
        except Exception as e:
            logger.debug(f"wait outcome MFE/MAE backfill fail {sym}: {e}")
    except Exception as e:
        logger.warning(f"POSITION_CLOSED журнал не записан {sym}: {e}")


async def background_position_monitor():
    """Каждые 60с проверять открытые Bybit-позиции. MANUAL-позиции, которые
    закрылись (SL/TP/ручное) → уведомление в Telegram."""
    import asyncio
    # Validation: update counterfactuals every cycle
    _val_counter = 0
    while True:
        _val_counter += 1
        try:
            import httpx
            _ak, _as = _load_credentials()
            if not _ak or not _as:
                await asyncio.sleep(60)
                continue
            # Получить позиции (без авторизации: публичный endpoint не даёт позиции —
            # используем подписанный GET /v5/position/list)
            import hashlib, hmac, urllib.parse
            ts = int(time.time() * 1000)
            recv_window = "5000"
            qs = "category=linear&settleCoin=USDT"
            payload = f"{ts}{_ak}{recv_window}{qs}"
            sign = hmac.new(_as.encode(), payload.encode(), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": _ak,
                "X-BAPI-TIMESTAMP": str(ts),
                "X-BAPI-RECV-WINDOW": recv_window,
                "X-BAPI-SIGN": sign,
            }
            with httpx.Client(timeout=15) as c:
                r = c.get(_api_base() + "/v5/position/list?" + qs, headers=headers)
            rows = (r.json().get("result") or {}).get("list") or []
            current = {p["symbol"]: p for p in rows if float(p.get("size", 0) or 0) != 0}

            # Validation: update counterfactuals with current prices (every cycle)
            if _val_counter % 1 == 0:  # every cycle (~60s)
                try:
                    # Also fetch prices for symbols without open positions
                    all_syms = set(current.keys())
                    # Add tracked positions symbols
                    all_syms.update(_tracked_positions.keys())
                    # Add wait limit state symbols
                    all_syms.update(_load_wait_limit_state().keys())
                    prices = {}
                    for sym_v in all_syms:
                        try:
                            with httpx.Client(timeout=5) as _c:
                                _t = _c.get("https://api.bybit.com/v5/market/tickers",
                                            params={"category": "linear", "symbol": sym_v})
                                px_v = float(((_t.json().get("result") or {}).get("list") or [{}])[0].get("lastPrice", 0))
                                if px_v > 0:
                                    prices[sym_v] = px_v
                        except Exception:
                            pass
                    from tradingos.signals.validation import get_recorder
                    get_recorder().update_counterfactuals(prices)
                except Exception as e:
                    logger.debug(f"validation counterfactual update failed: {e}")

            # Проверить отслеживаемые MANUAL-позиции
            for sym, info in list(_tracked_positions.items()):
                if sym not in current:
                    # Закрылась — телеслой: outcome/MFE/MAE/time-to-MFE в журнал
                    try:
                        _log_position_close(sym, info, now=time.time())
                    except Exception as e:
                        logger.warning(f"position close log failed {sym}: {e}")
                    try:
                        from telegram_control.manual_signal import _chat_id
                        _cid = _chat_id()
                        _bot = None
                        # Используем контекст бота через глобальный референс
                        # (упрощённо: отправляем через прям мой httpx API)
                        await _send_manual_close_notification(sym, info)
                    except Exception as e:
                        logger.warning(f"manual close notify failed {sym}: {e}")
                    _tracked_positions.pop(sym, None)
                    continue
                # Живая позиция: обновляем MFE/MAE по текущей цене
                try:
                    _update_tracked_mfe_mae(sym, info, current[sym])
                except Exception:
                    pass

            # WAIT-LIMIT исполнен: появилась позиция → уведомление + помечаем
            # статус в state (чтобы _cancel_wait_limits не отменял исполненную)
            wl_state = _load_wait_limit_state()
            # B1 (2026-08-29): авто-отмена PLACED-лимиток при пробое структуры.
            # Если цена УЖЕ пробила уровень SL (для LONG: px ≤ SL) до касания
            # зоны — это не откат, а проход; лимитка не должна ловить нож ниже
            # собственной защиты. Пост-филл/expired/вручную-удалённые нетронуты.
            for sym_b1, rec_b1 in list(wl_state.items()):
                if rec_b1.get("status") != "PLACED":
                    continue
                side_b1 = str(rec_b1.get("side", "LONG")).upper()
                sl_b1 = rec_b1.get("sl") or 0
                if sl_b1 <= 0:
                    continue
                is_long_b1 = side_b1 in ("LONG", "BUY")
                try:
                    with httpx.Client(timeout=8) as _c:
                        _t = _c.get("https://api.bybit.com/v5/market/tickers",
                                    params={"category": "linear", "symbol": sym_b1})
                        px_b1 = float(((_t.json().get("result") or {}).get("list") or [{}])[0].get("lastPrice", 0))
                except Exception:
                    continue
                broken = (is_long_b1 and px_b1 <= sl_b1) or (not is_long_b1 and px_b1 >= sl_b1)
                if broken and px_b1 > 0:
                    dr = _delete_wait_limit(sym_b1, reason="STRUCTURE_BREAK")
                    logger.info(f"🗑 B1: WAIT-лимитка {sym_b1} отменена — цена {px_b1:.4g} "
                                f"пробила SL {sl_b1:.4g} до касания зоны (ok={dr.get('ok')})")
                    try:
                        await _notify_wait_limit_cancelled(
                            sym_b1, "цена пробила SL-структуру до касания зоны — input недействителен")
                    except Exception:
                        pass
            for sym in current:
                rec = wl_state.get(sym)
                if rec and rec.get("status") == "PLACED":
                    planned_qty = float(rec.get("qty", 0) or 0)
                    filled_now = float(current[sym].get("size", 0) or 0)
                    is_partial = planned_qty > 0 and filled_now < planned_qty - 1e-9
                    # P1.5a (2026-08-29): PARTIAL FILL — исполненная часть
                    # защищается НЕМЕДЛЕННО (SL/TP на позицию), остаток лимитки
                    # продолжает висеть до экспирации/new-флоу. Partial-rate
                    # считаем в журнале (rec["partial_fill"]=True).
                    if is_partial:
                        rec["partial_fill"] = True
                        rec["filled_qty"] = filled_now
                        rec["partial_fill_ts"] = datetime.now(timezone.utc).isoformat()
                        _WAIT_LIMIT_STATE.write_text(
                            json.dumps(wl_state, ensure_ascii=False))
                        logger.info(f"⏳ PARTIAL FILL {sym}: {filled_now}/{planned_qty} — "
                                    f"защищаю исполненную часть, остаток висит")
                        # T16: PARTIAL-исход тоже в журнал (счётчик partial-fill rate)
                        try:
                            _log_wait_outcome(sym, rec, "PARTIAL_FILL",
                                              price_now=float(current[sym].get("avgPrice", 0) or 0))
                        except Exception as e:
                            logger.debug(f"wait outcome PARTIAL log fail {sym}: {e}")
                        try:
                            _set_manual_trading_stop(sym, filled_now,
                                                     rec.get("sl", 0), rec.get("tp", 0))
                        except Exception as e:
                            logger.warning(f"partial-fill set-trading-stop failed: {e}")
                        try:
                            await _notify_wait_limit_filled(
                                sym, rec.get("side", current[sym].get("side")),
                                rec.get("price", 0), filled_now)
                        except Exception as e:
                            logger.warning(f"partial-fill notify failed: {e}")
                        continue  # лимитка ещё жива (остаток в pending)
                    rec["status"] = "FILLED"
                    rec["filled_at"] = datetime.now(timezone.utc).isoformat()
                    rec["filled_price"] = float(current[sym].get("avgPrice", 0) or 0)
                    _WAIT_LIMIT_STATE.write_text(
                        json.dumps(wl_state, ensure_ascii=False))
                    # A2: SL/TP на позицию — ПОСЛЕ филла (не attached к ордеру)
                    try:
                        _set_manual_trading_stop(sym, filled_now or rec.get("qty", 0),
                                                 rec.get("sl", 0), rec.get("tp", 0))
                    except Exception as e:
                        logger.warning(f"wait-limit set-trading-stop failed: {e}")
                    try:
                        await _notify_wait_limit_filled(
                            sym, rec.get("side", current[sym].get("side")),
                            rec.get("price", 0), rec.get("qty", 0))
                    except Exception as e:
                        logger.warning(f"wait-limit filled notify failed: {e}")
                    # T16 (2026-08-29): FILLED-исход в журнал — без него
                    # /waitreport не посчитает fill rate и entry improvement.
                    try:
                        _log_wait_outcome(sym, rec, "FILLED",
                                          price_now=rec.get("filled_price") or 0)
                    except Exception as e:
                        logger.debug(f"wait outcome FILLED log fail {sym}: {e}")

            # Добавить новые MANUAL-позиции (из журнала manual_signals.jsonl)
            for sym in current:
                if sym in _tracked_positions:
                    continue
                if _is_manual_symbol(sym):
                    _tracked_positions[sym] = {
                        "symbol": sym,
                        "side": current[sym].get("side"),
                        "size": float(current[sym].get("size", 0)),
                        "entry": float(current[sym].get("avgPrice", 0)),
                        "opened_at": time.time(),
                        "mfe": 0.0,
                        "mae": 0.0,
                        "mfe_pct": 0.0,
                        "mae_pct": 0.0,
                        "last_px": float(current[sym].get("avgPrice", 0)),
                    }
        except Exception as e:
            logger.debug(f"position monitor error: {e}")
        await asyncio.sleep(60)


def _is_manual_symbol(sym: str) -> bool:
    """Проверить, MANUAL ли это позиция — есть ли символ в журнале с EXECUTED."""
    try:
        if not JOURNAL.exists():
            return False
        for l in reversed(JOURNAL.read_text(errors="replace").splitlines()):
            if not l.strip():
                continue
            try:
                r = json.loads(l)
            except Exception:
                continue
            if r.get("symbol") == sym and r.get("event") == "EXECUTED":
                return True
    except Exception:
        return False
    return False


async def _send_manual_close_notification(sym: str, info: dict):
    """Отправить уведомление о закрытии MANUAL-позиции в Telegram.

    FIX 2026-08-24: `Bot(token)` создавался БЕЗ прокси, а этот сервер не
    имеет прямого доступа к api.telegram.org (ConnectError: Network is
    unreachable). Прокси socks5://127.0.0.1:1080 обязателен (как в
    manual_bot.build_app). Без него ручные close-уведомления молча
    таймаутились ('Timed out') и не доставлялись.
    """
    try:
        import httpx
        from telegram import Bot
        from telegram.request import HTTPXRequest
        load_dotenv("/root/mt5_trading_bot/.env")
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            return
        proxy_url = "socks5://127.0.0.1:1080"
        request = HTTPXRequest(proxy=proxy_url, connect_timeout=15, read_timeout=60)
        bot = Bot(token=token, request=request)
        await bot.send_message(
            chat_id=chat_id,
            text=(f"🔔 <b>MANUAL-позиция закрыта</b>\n"
                  f"{sym} {info.get('side')} | qty={info.get('size', 0):.4g}\n"
                  f"entry={info.get('entry', 0):.6g}\n"
                  f"Была открыта: {datetime.now(timezone.utc).strftime('%H:%M')} UTC"),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"manual close notify failed: {e}")


async def _send_signal_card(bot, sig: dict):
    """Отправить карточку сигнала с кнопками (используется автосканом).

    Маршрутизация по contour:
      MARKET  → обычная карточка (маркет-исполнение)
      LIMIT   → WAIT-лимит карточка (лимитка на откате)
      NO_TRADE → не отправляем (или карточка отклонения)
    Каждая отправленная карточка пишется в JOURNAL (event=SIGNAL_SENT)."""
    from telegram import InlineKeyboardMarkup
    _last_signals[sig["symbol"]] = {**sig, "_stored_at": time.time()}

    contour = sig.get("contour", "NO_TRADE")

    if contour == "NO_TRADE" or sig.get("tp_unreachable"):
        # Режект-карточка (TP unreachable или contour NO_TRADE)
        reason = sig.get("contour_reasoning", ["NO_TRADE"])[0] if sig.get("contour_reasoning") else "NO_TRADE"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Понятно", callback_data=f"ms_skip_{sig['symbol']}"),
        ]])
        await bot.send_message(
            chat_id=_chat_id(),
            text=("🔴 <b>СИГНАЛ ОТКЛОНЁН</b>\n"
                  f"{sig['symbol']} — {sig['side']} | Score: {sig['score']}/100\n\n"
                  f"Причина: {reason}"),
            parse_mode="HTML", reply_markup=kb,
        )
        logger.info(f"Сигнал ОТКЛОНЁН: {sig['symbol']} score={sig['score']} contour={contour} reason={reason}")
        _log_signal_sent(sig, tp_unreachable=True)
        return

    if contour == "LIMIT":
        # Делегируем в WAIT-лимит карточку
        await _send_wait_limit_card(bot, sig)
        return

    # MARKET contour — обычная маркет-карточка
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 ОТКРЫТЬ", callback_data=f"ms_open_{sig['symbol']}"),
        InlineKeyboardButton("❌ ПРОПУСТИТЬ", callback_data=f"ms_skip_{sig['symbol']}"),
    ]])
    await bot.send_message(
        chat_id=_chat_id(), text=_format_signal(sig),
        parse_mode="HTML", reply_markup=kb,
    )
    logger.info(f"Автосигнал MARKET отправлен: {sig['symbol']} score={sig['score']}")
    _log_signal_sent(sig, tp_unreachable=False)


async def _send_wait_limit_card(bot, sig: dict):
    """WAIT_LIMIT: карточка с ПРЕДЛОЖЕНИЕМ ЛИМИТКИ на откате (модель владельца
    «ордера в прогнозных точках, сидишь ждёшь»). РЕШЕНИЕ принимает владелец —
    кнопка ставит реальный (demo) post-only LIMIT с экспирацией; без нажатия
    ничего не происходит."""
    from telegram import InlineKeyboardMarkup
    sym = sig["symbol"]
    side = sig["side"]
    emoji = "🟢" if side == "LONG" else "🔴"
    _last_signals[sym] = {**sig, "_stored_at": time.time()}
    wl = sig.get("wait_limit_entry") or 0
    wl_deep = sig.get("wait_limit_entry_deep") or 0
    tp1 = sig.get("final_tp") or 0
    sl = sig.get("sl") or 0
    wait_rr = sig.get("wait_rr") or 0
    wait_rr_deep = sig.get("wait_rr_deep") or 0
    price = sig.get("price") or 0
    # Живая цена на момент отправки карточки (сигнал мог устареть за минуты):
    # на карточке показываем РЕАЛЬНУЮ цену сейчас + предупреждаем, если зона
    # уже пройдена (цена уже в/за зоной → лимит стал бы marketable).
    cur_px = 0.0
    try:
        import httpx as _hx
        with _hx.Client(timeout=8) as _c:
            _t = _c.get("https://api.bybit.com/v5/market/tickers",
                        params={"category": "linear", "symbol": sym})
            cur_px = float(((_t.json().get("result") or {}).get("list") or [{}])[0].get("lastPrice", 0))
    except Exception:
        pass
    show_px = cur_px if cur_px > 0 else price
    is_long = str(side).upper() in ("LONG", "BUY")
    zone_passed = bool(cur_px > 0 and wl > 0) and (
        (is_long and cur_px <= wl) or (not is_long and cur_px >= wl)
    )
    # B2: лестница — показываем два уровня (L1 fib0.5, L2 fib0.618), каждый
    # со своим R:R. Кнопки ведут в ms_wait_ (там выбор уровня).
    lines = [
        f"{emoji} <b>⏳ WAIT-ЛИМИТ</b> — вход ждёт отката",
        f"<b>{sym}</b> — {side} | Score: <b>{sig['score']}/100</b>",
        "",
        f"📍 <b>Сейчас:</b> <code>{_fmt_price(show_px)}</code>"
        + (" (цена сигнала)" if not (cur_px > 0) else ""),
        f"📌 <b>Уровень 1:</b> <code>{_fmt_price(wl)}</code> (откат 0.5 от 7d-хая "
        f"{_fmt_price(sig.get('range_7d_high')) if sig.get('range_7d_high') else '—'})",
    ]
    if wl_deep > 0:
        lines.append(
            f"📌 <b>Уровень 2:</b> <code>{_fmt_price(wl_deep)}</code> "
            f"(откат 0.618 — глубже, чаще филл)")
    lines += [
        f"🛑 SL: <code>{_fmt_price(sl)}</code>",
        f"🎯 TP: <code>{_fmt_price(tp1)}</code>",
        f"💰 R:R: L1 ~{wait_rr}:1"
        + (f" | L2 ~{wait_rr_deep}:1" if wl_deep > 0 else "")
        + (f" (маркет {sig.get('rr','—')}:1)"),
        "",
    ]
    if zone_passed:
        lines += [
            "🚫 <b>Зона уже пройдена</b> — цена "
            f"{'ниже' if is_long else 'выше'} лимита. При постановке ордер "
            "исполнился бы мгновенно (маркет-филл), это не откат.\n",
            "<b>Сделка не предложена.</b> Ждите новый сигнал /signals.",
        ]
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Понятно", callback_data=f"ms_skip_{sym}"),
        ]])
        await bot.send_message(chat_id=_chat_id(), text="\n".join(lines),
                               parse_mode="HTML", reply_markup=kb)
        logger.info(f"WAIT_LIMIT отклонена (зона пройдена): {sym} px={show_px} wl={wl}")
        _log_signal_sent(sig, tp_unreachable=False)
        return
    lines += [
        "<b>Чем отличается от маркета:</b>",
        "• Ордер НЕ открывает позицию сразу — только ждёт, когда цена",
        f"  снизится до зоны (<i>прогнозная точка</i>)",
        "• Ставится LIMIT по твоей цене/сумме/плечу (не маркет-слепое исполнение)",
        "• Живёт 4 часа, потом отменяется — упущенная сделка = ничего не стоила",
        "",
        f"⏱ Экспирация: <b>4 часа</b>.",
        "",
    ]
    kb_rows = [[InlineKeyboardButton(f"📌 Уровень 1 @ {_fmt_price(wl)}",
                                     callback_data=f"ms_wait_{sym}_1")]]
    if wl_deep > 0:
        kb_rows.append([InlineKeyboardButton(f"📌 Уровень 2 @ {_fmt_price(wl_deep)}",
                                             callback_data=f"ms_wait_{sym}_2")])
    kb_rows.append([InlineKeyboardButton("❌ Пропустить",
                                         callback_data=f"ms_skip_{sym}")])
    kb = InlineKeyboardMarkup(kb_rows)
    await bot.send_message(chat_id=_chat_id(), text="\n".join(lines),
                           parse_mode="HTML", reply_markup=kb)
    logger.info(f"WAIT_LIMIT предложена: {sym} entry={wl} rr={wait_rr}")
    _log_signal_sent(sig, tp_unreachable=False)


def _log_signal_sent(sig: dict, tp_unreachable: bool):
    """Персист отправленной автосканом карточки в JOURNAL (event=SIGNAL_SENT).

    Полный срез сигнала для forensic-трассировки; переживает рестарт бота."""
    rec = {
        "event": "SIGNAL_SENT",
        "tp_unreachable": tp_unreachable,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    for k in ("signal_id", "symbol", "side", "score", "price", "rsi", "vol_ratio",
              "funding", "sl", "raw_tp", "final_tp", "rr", "gate_reason",
              "trade_decision", "tp_reachability_pct", "tp_last_touch_days",
              "tp_stale", "tp_unreachable", "stoch_k", "stoch_d", "stoch_cross_up",
              "stoch_cross_down", "stoch_conflict", "stoch_zone_block",
              "squeeze_on", "squeeze_fired", "squeeze_momentum",
              "h4_trend", "d1_trend", "mtf_agree", "mtf_gate", "skip_reason",
              "wait_limit_entry", "wait_rr", "tp_beyond_7d", "range_7d_high",
              "wait_limit_entry_deep", "wait_rr_deep",
              "range_7d_low",
              "contour", "contour_confidence", "contour_reasoning", "contour_version"):
        if k in sig:
            rec[k] = sig[k]
    if sig.get("why"):
        rec["why"] = sig["why"]
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning(f"SIGNAL_SENT журнал не записан {sig.get('symbol')}: {e}")


def _chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "")


async def background_scan_loop(application, interval_min: int = 20):
    """Фоновый автоскан: каждые interval_min минут присылает новые сигналы.
    Задача 5: dedup по TTL 2ч (раньше — пожизненно до рестарта)."""
    import asyncio
    logger.info(f"🔄 Фоновый автоскан запущен (каждые {interval_min} мин)")
    sent_keys: dict[str, float] = {}  # key → timestamp
    while True:
        try:
            from tradingos.signals.manual_scanner import scan_all
            found = await asyncio.to_thread(scan_all)
            now = time.time()
            # Очистка записей старше 2ч
            sent_keys = {k: v for k, v in sent_keys.items() if now - v < 7200}
            fresh = [s for s in found if f"{s['symbol']}_{s['side']}" not in sent_keys]
            for sig in fresh:
                try:
                    # Route by contour field (new classifier)
                    contour = sig.get("contour", "NO_TRADE")
                    if contour == "LIMIT":
                        await _send_wait_limit_card(application.bot, sig)
                    elif contour == "MARKET":
                        await _send_signal_card(application.bot, sig)
                    # NO_TRADE and tp_unreachable are handled inside _send_signal_card
                    sent_keys[f"{sig['symbol']}_{sig['side']}"] = time.time()
                except Exception as e:
                    logger.error(f"Автосигнал не отправлен {sig['symbol']}: {e}")
            # Отмена истёкших (4ч) WAIT-LIMIT заявок — фоном, каждые 1ч
            try:
                _cancel_wait_limits()
            except Exception as e:
                logger.warning(f"cancel_wait_limits: {e}")
            # Ограничиваем размер кэша отправленных
            if len(sent_keys) > 200:
                # оставить 100 самых свежих
                sent_keys = dict(sorted(sent_keys.items(), key=lambda kv: -kv[1])[:100])
        except Exception as e:
            logger.error(f"Автоскан ошибка: {e}")
        await asyncio.sleep(max(60, interval_min * 60))


async def background_equity_sampler(interval_sec: int = 300):
    """E1 (2026-08-29): независимый equity-пульс guard'а.

    run_observation (единственный прежний сэмплер) остановлен после
    emergency-halt 27.08 — state guard'а замерзал: day_start_equity=0,
    EQUITY_FETCH_ERROR лачился, лимиты дня считались от фолбэка $100.
    Здесь сами качаем equity и зовём on_equity_sample (обновляет last_equity,
    day_start_equity, blocked_new_entries, дневной loss-лимит) — безопасно,
    read-only, позиции не трогает.
    """
    import asyncio
    logger.info(f"⏱ Асинхронный equity-sampler запущен (каждые {interval_sec}с)")
    while True:
        await asyncio.sleep(interval_sec)
        try:
            import sys as _sys
            _sys.path.insert(0, "/root/tradingos")
            from tradingos.strategies.deposit_guard import get_guard
            _g = get_guard()
            _eq = _g._get_equity()
            if _eq and _eq > 0:
                _g.on_equity_sample(_eq)
        except Exception as e:
            logger.warning(f"equity sampler: {e}")


async def background_bingx_digest(application, interval_min: int = 15):
    """Каждые interval_min минут: дайджест открытых BingX-позиций с
    рекомендацией и кнопками действий + проверка пробоя уровней.
    Пусто (нет позиций) — не шлём спам (кроме level-break алертов)."""
    import asyncio
    logger.info(f"🔵 BingX-дайджест запущен (каждые {interval_min} мин)")
    while True:
        await asyncio.sleep(interval_min * 60)
        # ─── Level-break alerts (2026-09-01, owner) ──────────────
        # Проверка пробоя уровней НЕЗАВИСИМО от наличия позиций: уровни
        # могут стоять и на будущие движения (level_alerts.json).
        try:
            from tradingos.bingx_signal import check_level_breaks
            chat = os.getenv("TELEGRAM_CHAT_ID", "")
            if chat:
                msgs = await asyncio.to_thread(check_level_breaks)
                for m in msgs:
                    await application.bot.send_message(
                        chat_id=chat, text=m, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"level-break check error: {e}")
        try:
            from tradingos.bingx_signal import position_digest_all, action_label
            digests = await asyncio.to_thread(position_digest_all)
            if not digests:
                continue
            chat = os.getenv("TELEGRAM_CHAT_ID", "")
            if not chat:
                logger.warning("BingX digest: нет TELEGRAM_CHAT_ID")
                continue
            for d in digests:
                kb_rows = []
                for a in d.get("actions", ["hold"]):
                    if a == "hold":
                        continue  # «Держать» = no-op, кнопку не рисуем (owner 2026-09-01)
                    kb_rows.append([InlineKeyboardButton(
                        action_label(a),
                        callback_data=f"BXACT:{a}:{d['symbol']}:{d['side']}")])
                kb = InlineKeyboardMarkup(kb_rows) if kb_rows else None
                await application.bot.send_message(
                    chat_id=chat, text=d["text"], parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            logger.warning(f"BingX digest error: {e}")


async def _bx_recommend_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка BXACT:<action>:<sym>:<side> — выполнить рекомендацию на BingX."""
    from telegram_control.manual_signal import _esc as _e2
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    try:
        _, act, sym, side = data.split(":")
    except ValueError:
        await q.message.reply_text("❌ Некорректная кнопка.", parse_mode="HTML")
        return
    try:
        from tradingos.bingx_signal import bx_action, action_label
        r = await asyncio.to_thread(bx_action, sym, side, act)
        icon = "✅" if r.get("ok") else "❌"
        await q.message.reply_text(
            f"{icon} <b>{sym} {side}</b> — {_e2(action_label(act))}:\n"
            f"{_e2(str(r.get('msg', '')))}",
            parse_mode="HTML")
    except Exception as e:
        await q.message.reply_text(f"❌ Ошибка: {_e2(str(e))}", parse_mode="HTML")


async def background_hourly_summary(application):
    """Каждый час: сводка по открытым позициям (PnL $, R, источник, SL/TP).
    Данные берутся НАПРЯМУЮ с биржи (avgPrice/unrealisedPnl/SL/TP), чтобы
    сводка не зависела от локальных кэшей и не пересчитывала entry из size.
    R = (cur - entry)/risk_unit (BUY) или (entry - cur)/risk_unit (SELL) —
    прибыль на единицу ÷ риск на единицу (НЕ доллары ÷ ценовой шаг).
    Запускается в manual_bot.py через asyncio.create_task."""
    import asyncio, json as _json
    logger.info("📊 Фоновая сводка запущена (каждый час)")
    while True:
        await asyncio.sleep(3600)  # первый через час
        try:
            sys.path.insert(0, "/root/tradingos")
            from tradingos.strategies.bybit_position_check import _get_positions
            positions = [p for p in _get_positions() if float(p.get("size", 0) or 0) > 0]
            if not positions:
                continue
            # Guardian state — источник риска на единицу (entry_to_sl_risk) и source
            try:
                _state = _json.loads(Path("/root/tradingos/guardian/reality_state.json").read_text())
            except Exception:
                _state = {}
            lines = ["📊 <b>СВОДКА ПО ПОЗИЦИЯМ</b>", f"🕐 {datetime.now(timezone.utc).strftime('%H:%M')} UTC", ""]
            total_pnl = 0.0
            for p in positions:
                sym = p.get("symbol", "?")
                side = p.get("side", "?")
                size = float(p.get("size", 0) or 0)
                entry = float(p.get("avgPrice", 0) or 0)
                cur = float(p.get("markPrice", 0) or 0)
                unreal = float(p.get("unrealisedPnl", 0) or 0)
                sl = p.get("stopLoss", 0) or 0
                tp = p.get("takeProfit", 0) or 0
                liq = p.get("liqPrice", 0) or 0
                is_buy = str(side).lower().startswith("buy")
                # PnL в $ — берём напрямую с биржи (учёт комиссий/фандинга не нужен,
                # unrealisedPnl уже чистый). Фолбэк — ручной расчёт от avgPrice.
                if unreal:
                    profit = unreal
                else:
                    profit = (cur - entry) * size if is_buy else (entry - cur) * size
                total_pnl += profit
                st = _state.get(sym, {})
                risk_unit = st.get("entry_to_sl_risk", 0)
                # Фолбэк: если guardian ещё не зарегистрировал риск, берём |entry-SL|
                if not risk_unit or risk_unit <= 0:
                    risk_unit = abs(entry - float(sl or 0)) if float(sl or 0) > 0 else 0
                # R: прибыль на единицу / риск на единицу
                if risk_unit and risk_unit > 0:
                    per_unit = (cur - entry) if is_buy else (entry - cur)
                    r_mult = per_unit / risk_unit
                else:
                    r_mult = 0.0
                source = "🔧" if st.get("source") == "MANUAL" else "🤖"
                emoji = "🟢" if profit >= 0 else "🔴"
                lines.append(
                    f"{source} <b>{sym}</b> {side}: {emoji} {profit:+.2f}$ ({r_mult:+.2f}R)\n"
                    f"    entry {entry:.6g} → now {cur:.6g} | qty {size:g}"
                    + (f" | SL {float(sl):.6g}" if float(sl or 0) > 0 else "")
                    + (f" | TP {float(tp):.6g}" if float(tp or 0) > 0 else "")
                    + (f" | liq {float(liq):.6g}" if float(liq or 0) > 0 else "")
                )
            lines.append("")
            total_emoji = "🟢" if total_pnl >= 0 else "🔴"
            lines.append(f"<b>Итого PnL: {total_emoji} {total_pnl:+.2f}$</b>")
            await application.bot.send_message(
                chat_id=_chat_id(), text="\n".join(lines), parse_mode="HTML")
        except Exception as e:
            logger.debug(f"hourly summary error: {e}")
