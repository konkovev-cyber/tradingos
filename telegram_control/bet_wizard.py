"""
bet_wizard.py — Ручной визард ставок через Telegram-бота (модель владельца).

Поток: /bet → выбор пары (или ввод символа) → рекомендация системы
(вход у структуры, SL/TP) → владелец правит плечо/сумму → ставка.

Владелец управляет ВСЁМ: символ, плечо, сумма маржи, вход, SL, TP.
Бот только считает qty, ставит лимитку и рекомендует уровни.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger("bet_wizard")

ROOT = Path("/root/tradingos")
STATE = ROOT / "operations/auto_limit_state.json"

# Состояние визарда: uid -> {step, symbol, ...}
_FLOWS: dict[int, dict] = {}

# Дефолты ставки
DEF_BET_USD = 500.0      # маржа (снижен с 1000 — гигантские позиции на дешевых токенах)
DEF_LEV = 10
DEF_RR = 3.0
MAX_NOTIONAL_USD = 5000.0  # hard cap: позиция не больше $5k независимо от плеча
MAX_EQUITY_PCT = 0.05    # 5% equity — soft warning
MAX_BET_USD = 2000.0      # маржа не больше $2k


def _esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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


# ─── Рекомендация от системы ────────────────────────────────────────

def recommend(symbol: str) -> dict | None:
    """Структурные уровни для символа: вход EMA20+buf, SL под свингом, TP 3R."""
    try:
        import sys
        sys.path.insert(0, "/root")
        sys.path.insert(0, "/root/tradingos")
        from tradingos.signals.manual_scanner import _klines, _ema, _rsi_wilder
        import httpx

        with httpx.Client() as client:
            h1 = _klines(client, symbol, "60", 200)
            h4 = _klines(client, symbol, "240", 120)
            d1 = _klines(client, symbol, "D", 60)
        if not (h1 and h4 and d1):
            return None

        def tt(cls):
            e20 = _ema(cls, 20)[-2]
            e50 = _ema(cls, 50)[-2]
            cl = cls[-2]
            if cl > e20 > e50:
                return "UP"
            if cl < e20 < e50:
                return "DOWN"
            return "MIXED"

        t_h1, t_h4, t_d1 = tt([b["close"] for b in h1]), \
            tt([b["close"] for b in h4]), tt([b["close"] for b in d1])

        # Ликвидность
        vols = [(b["close"] * b["volume"]) for b in h1]
        med = sorted(vols)[len(vols) // 2]

        trs = []
        for i in range(1, len(h1)):
            tr = max(h1[i]["high"] - h1[i]["low"],
                     abs(h1[i]["high"] - h1[i - 1]["close"]),
                     abs(h1[i]["low"] - h1[i - 1]["close"]))
            trs.append(tr)
        atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else 0
        if atr <= 0:
            return None

        c1 = [b["close"] for b in h1]
        price = c1[-2]
        # FIX 2026-09-03 (MSFT bug): закрытый бар отстаёт от живой цены (MSFT:
        # close[-2]=509.92 vs lastPrice=512.56 → entry ниже рынка → PostOnly
        # мгновенный Cancel). Берём ЖИВУЮ цену тикера как референс для entry.
        try:
            import httpx as _hx0
            _rt = _hx0.get("https://api.bybit.com/v5/market/tickers",
                           params={"category": "linear", "symbol": symbol}, timeout=10).json()
            live_price = float(((_rt.get("result") or {}).get("list") or [{}])[0].get("lastPrice", 0) or 0)
            if live_price > 0:
                price = live_price
        except Exception:
            pass
        e20 = _ema(c1, 20)[-2]
        rsi = _rsi_wilder(c1)
        pb_low = min(b["low"] for b in h1[-12:])
        hi48 = max(b["high"] for b in h1[-48:])
        lo48 = min(b["low"] for b in h1[-48:])

        # Направление: по D1 (главный тренд для ставки)
        # FIX 2026-09-03: добавлены reversal-условия (контр-тренд при extreme RSI + свечной сигнал)
        reversal = False
        if t_d1 == "UP":
            side = "LONG"
        elif t_d1 == "DOWN":
            side = "SHORT"
        else:
            side = None

        # Counter-trend reversal: RSI divergence or extreme levels
        # FIX 2026-09-03: relaxed — RSI > 65 for SHORT reversal, RSI < 35 for LONG reversal
        last_closed = h1[-2]
        if t_d1 == "UP" and rsi > 65 and last_closed["close"] < last_closed["open"]:
            side = "SHORT"
            reversal = True
        elif t_d1 == "DOWN" and rsi < 35 and last_closed["close"] > last_closed["open"]:
            side = "LONG"
            reversal = True

        if side is None:
            return {"sym": symbol, "side": None, "price": price,
                    "note": "D1 MIXED — направления нет, ставку не рекомендую"}

        if side == "LONG":
            entry = e20 + 0.1 * atr
            # Limit must be BELOW current price for LONG (otherwise fills as market taker)
            if entry >= price:
                # FIX 2026-09-03: EMA20 выше цены (импульс вверх) — лимитка на
                # откат, но НЕ выше текущей цены: у текущей −0.2%.
                entry = price * 0.998 if e20 >= price * 0.995 else min(entry, price * 0.998)
            # FIX 2026-09-04 (адаптивный gap): если entry слишком близко к цене
            # (< 0.3% ATR-путешествия), филлиться будет только при точном развороте.
            # Отодвигаем минимум на 0.25×ATR ниже текущей — осмысленная зона ожидания.
            min_gap = max(0.0025 * price, 0.25 * atr)
            if (price - entry) < min_gap:
                entry = price - min_gap
            sl = pb_low - 0.15 * atr
            # Ensure entry > SL (SL must be below entry for LONG)
            if entry <= sl:
                sl = entry * 0.995
            sl_dist = entry - sl
            tp = entry + DEF_RR * sl_dist
            struct_ref = f"EMA20(H1)={_fmt_price(e20)}, 12h pb-low={_fmt_price(pb_low)}"
        else:
            entry = e20 - 0.1 * atr
            # Limit must be ABOVE current price for SHORT (otherwise fills as market taker)
            if entry <= price:
                # FIX 2026-09-03 (MSFT bug): EMA20 ниже цены на импульсе — ставим
                # лимитку на откат к EMA20, но НЕ ниже текущей цены. Если EMA20
                # далеко внизу — ставим у текущей цены +0.2% (ожидание паузы).
                entry = price * 1.002 if e20 <= price * 1.005 else max(entry, price * 1.002)
            # FIX 2026-09-04 (адаптивный gap): зеркально LONG — минимальная зона
            # ожидания 0.25×ATR выше цены.
            min_gap = max(0.0025 * price, 0.25 * atr)
            if (entry - price) < min_gap:
                entry = price + min_gap
            recent_hi = max(b["high"] for b in h1[-12:])
            sl = recent_hi + 0.15 * atr
            # Ensure entry < SL (SL must be above entry for SHORT)
            if entry >= sl:
                sl = entry * 1.005
            sl_dist = sl - entry
            tp = entry - DEF_RR * sl_dist
            struct_ref = f"EMA20(H1)={_fmt_price(e20)}, 12h pb-high={_fmt_price(recent_hi)}"

        # 30D reachability
        try:
            import httpx as _hx
            with _hx.Client() as _c:
                r30 = _hx.get("https://api.bybit.com/v5/market/kline",
                              params={"category": "linear", "symbol": symbol,
                                      "interval": "60", "limit": 720}, timeout=15).json()
            rows30 = (r30.get("result") or {}).get("list") or []
            hi30 = max(float(x[2]) for x in rows30) if rows30 else 0
            lo30 = min(float(x[3]) for x in rows30) if rows30 else 0
            tp_ok = (tp <= hi30) if side == "LONG" else (tp >= lo30)
            touches = sum(1 for x in rows30
                          if (float(x[2]) >= tp if side == "LONG" else float(x[3]) <= tp))
        except Exception:
            tp_ok, touches, hi30, lo30 = None, 0, 0, 0

        # FIX 2026-09-03: quality/volume/impulse metrics for strong vs weak setups
        vol_current = h1[-2]["volume"]
        vol_median = sorted(vols)[len(vols) // 2] if vols else 0
        vol_ratio = min((vol_current / vol_median) if vol_median > 0 else 1.0, 5.0)  # cap 5x
        last_body = abs(last_closed["close"] - last_closed["open"])
        last_range = max(last_closed["high"] - last_closed["low"], 1e-12)
        body_ratio = min(last_body / last_range, 0.8)
        # Momentum: consecutive close direction (last 3 bars closing in direction of trade)
        closes_3 = [b["close"] for b in h1[-5:-2]]
        mom_ok = False
        if side == "LONG":
            mom_ok = all(closes_3[i] > closes_3[i-1] for i in range(1, len(closes_3))) if len(closes_3) >= 3 else True
        elif side == "SHORT":
            mom_ok = all(closes_3[i] < closes_3[i-1] for i in range(1, len(closes_3))) if len(closes_3) >= 3 else True

        # Normalized quality score (0-100 range, threshold 55)
        quality_score = int(40 + (vol_ratio / 5.0) * 30 + (body_ratio / 0.8) * 30 + (20 if mom_ok else 0))

        return {
            "sym": symbol, "side": side, "price": price,
            "entry": entry, "sl": sl, "tp": tp,
            "sl_pct": abs(sl - entry) / entry * 100,
            "tp_pct": abs(tp - entry) / entry * 100,
            "atr": atr, "rsi": rsi,
            "trend": f"H1={t_h1} H4={t_h4} D1={t_d1}",
            "struct_ref": struct_ref,
            "tp_ok": tp_ok, "touches": touches,
            "range": (lo30, hi30),
            "reversal": reversal,
            "vol_ratio": vol_ratio,
            "body_ratio": body_ratio,
            "mom_ok": mom_ok,
            "quality_score": quality_score,
        }
    except Exception as e:
        logger.warning(f"recommend {symbol}: {e}")
        return None


def _recommend_text(r: dict) -> str:
    side_emoji = "🟢 LONG" if r["side"] == "LONG" else "🔴 SHORT"
    reversal_tag = " 🔄 РЕВЕРСАЛ" if r.get("reversal") else " 📈 ТРЕНД"
    rr = DEF_RR
    tp_state = "✅ в 30D диапазоне" if r["tp_ok"] else "⚠️ за 30D диапазоном!"
    quality = r.get("quality_score", 0)
    q_tag = "🟢" if quality >= 80 else "🟡" if quality >= 60 else "🔴"
    return (
        f"📊 <b>РЕКОМЕНДАЦИЯ: {r['sym']} {side_emoji}{reversal_tag}</b>\n"
        f"{q_tag} Quality: <code>{quality}</code> | Тренд: <code>{r['trend']}</code> | RSI <code>{r['rsi']:.0f}</code>\n\n"
        f"📍 <b>Вход (лимитка у структуры):</b> <code>{_fmt_price(r['entry'])}</code>\n"
        f"   ({(r['entry']-r['price'])/r['price']*100:+.2f}% от текущей {_fmt_price(r['price'])})\n"
        f"   Уровень: {r['struct_ref']}\n\n"
        f"🎯 <b>TP (3R):</b> <code>{_fmt_price(r['tp'])}</code> (+{r['tp_pct']:.2f}%)\n"
        f"🛑 <b>SL:</b> <code>{_fmt_price(r['sl'])}</code> (-{r['sl_pct']:.2f}%)\n"
        f"   {tp_state}, касаний за 30D: {r['touches']}\n\n"
        f"<i>Дальше укажи плечо и сумму — я посчитаю и поставлю.</i>"
    )


# ─── Wizard flow ────────────────────────────────────────────────────

def _get_verified_setups() -> dict:
    """Проверенные сетапы из кэша bet_push (шаг1 кнопками)."""
    try:
        cache = ROOT / "operations/bet_setups.json"
        if not cache.exists():
            return {}
        setups = json.loads(cache.read_text())
        # отфильтровать слишком старые (>4ч)
        now = time.time()
        return {s: r for s, r in setups.items()
                if now - float(r.get("ts", 0)) < 4 * 3600}
    except Exception:
        return {}


async def cmd_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/bet [SYMBOL] — визард ручной ставки."""
    uid = update.effective_user.id
    args = context.args
    if args:
        sym = args[0].upper().replace("/", "").replace("-", "")
        if not sym.endswith("USDT"):
            sym += "USDT"
        _FLOWS[uid] = {"step": "side", "symbol": sym}
        await _ask_side(update, sym)
        return
    _FLOWS[uid] = {"step": "symbol"}
    setups = _get_verified_setups()
    if setups:
        # Кнопочный выбор из проверенных сигналов (порядок: свежие первыми)
        rows = []
        for sym in sorted(setups, key=lambda s: -float(setups[s].get("ts", 0)))[:8]:
            r = setups[sym]
            emoji = "🟢" if r["side"] == "LONG" else "🔴"
            rows.append([InlineKeyboardButton(
                f"{emoji} {sym} {r['side']} "
                f"@{r['entry']:.4g} RR{r.get('rr_l1', 3.0):.0f}",
                callback_data=f"BWS:pick:{sym}")])
        rows.append([InlineKeyboardButton("🔎 Другой символ (ввести)", callback_data="BWS:manual_sym")])
        rows.append([InlineKeyboardButton("❌ Отмена", callback_data="BWS:cancel")])
        kb = InlineKeyboardMarkup(rows)
        await update.effective_message.reply_text(
            "🎲 <b>ВИЗАРД СТАВКИ</b>\n\n"
            f"<b>Шаг 1/5 — выбери проверенный сигнал:</b>\n"
            f"(из последнего скана, {len(setups)} сетапов)",
            parse_mode="HTML", reply_markup=kb)
    else:
        # Нет проверенных — предлагаем пересканировать или ввести вручную
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Сканировать рынок сейчас", callback_data="BWS:scan")],
            [InlineKeyboardButton("🔎 Другой символ (ввести)", callback_data="BWS:manual_sym")],
            [InlineKeyboardButton("❌ Отмена", callback_data="BWS:cancel")],
        ])
        await update.effective_message.reply_text(
            "🎲 <b>ВИЗАРД СТАВКИ</b>\n\n"
            "Проверенных сигналов сейчас нет — рынок спокоен.\n"
            "Могу пересканировать или ты введёшь символ вручную.",
            parse_mode="HTML", reply_markup=kb)


async def _ask_side(update: Update, sym: str):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 LONG (рост)", callback_data=f"BWS:side:LONG"),
         InlineKeyboardButton("🔴 SHORT (падение)", callback_data=f"BWS:side:SHORT")],
        [InlineKeyboardButton("📊 Сначала рекомендация бота", callback_data="BWS:recommend")],
        [InlineKeyboardButton("❌ Отмена", callback_data="BWS:cancel")],
    ])
    await update.effective_message.reply_text(
        f"🎲 <b>{sym}</b> — Шаг 2/5\nСторона?",
        parse_mode="HTML", reply_markup=kb)


async def _ask_bet_usd(update: Update, uid: int):
    f = _FLOWS.get(uid, {})
    eq = _get_equity()
    bet_pct = f.get("bet_usd", DEF_BET_USD) / max(eq, 1) * 100 if eq else 0
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("$100", callback_data="BWS:bet:100"),
         InlineKeyboardButton("$500", callback_data="BWS:bet:500")],
        [InlineKeyboardButton("$1000", callback_data="BWS:bet:1000"),
         InlineKeyboardButton("$5000", callback_data="BWS:bet:5000")],
        [InlineKeyboardButton("10% депо", callback_data="BWS:bet:pct10"),
         InlineKeyboardButton("20% депо", callback_data="BWS:bet:pct20")],
        [InlineKeyboardButton("❌ Отмена", callback_data="BWS:cancel")],
    ])
    await update.effective_message.reply_text(
        f"🎲 Шаг 3/5 — Сумма МАРЖИ?\n\n"
        f"Equity: <code>${_get_equity():,.0f}</code>\n"
        f"Текущая: <code>${f.get('bet_usd', DEF_BET_USD):,.0f}</code> (~{bet_pct:.1f}% депо)\n"
        f"<i>Ноушнл = маржа × плечо</i>",
        parse_mode="HTML", reply_markup=kb)


def _get_equity() -> float:
    try:
        st = json.loads((ROOT / "operations/deposit_guard_state.json").read_text())
        v = st.get("last_equity")
        if v and float(v) > 0:
            return float(v)
    except Exception:
        pass
    return 0.0


async def _ask_leverage(update: Update, uid: int):
    f = _FLOWS.get(uid, {})
    lev = f.get("lev", DEF_LEV)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"{x}x",
                               callback_data=f"BWS:lev:{x}") for x in (3, 5, 10)],
                               [InlineKeyboardButton("2x", callback_data="BWS:lev:2"),
                                InlineKeyboardButton("20x", callback_data="BWS:lev:20")],
                              [InlineKeyboardButton("❌ Отмена", callback_data="BWS:cancel")]])
    await update.effective_message.reply_text(
        f"🎲 Шаг 4/5 — Плечо?\n\nТекущее: <code>{lev}x</code>\n"
        f"<i>Больше плечо = больше ношнл на ту же маржу, но ликвидация ближе.</i>",
        parse_mode="HTML", reply_markup=kb)


async def _show_plan(update: Update, uid: int):
    """Итоговый план ставки: все параметры + кнопки Поставить/Править."""
    f = _FLOWS.get(uid, {})
    sym = f["symbol"]
    side = f["side"]
    entry = f["entry"]
    sl = f["sl"]
    tp = f["tp"]
    bet = f["bet_usd"]
    lev = f["lev"]
    notional = min(bet * lev, MAX_NOTIONAL_USD)
    # FIX 2026-09-03: safety cap — cheap tokens at 10x produce huge notional; warn if equity pct too high
    eq = _get_equity()
    if eq > 0 and notional / eq > MAX_EQUITY_PCT:
        notional = eq * MAX_EQUITY_PCT
    if bet > MAX_BET_USD:
        bet = MAX_BET_USD
    try:
        qty_raw = notional / entry
        # Round by lot step from exchange
        import httpx as _hx
        with _hx.Client() as c:
            ri = c.get("https://api.bybit.com/v5/market/instruments-info",
                       params={"category": "linear", "symbol": sym}).json()
            ri_list = ri.get("result", {}).get("list", [{}])
            lot = ri_list[0].get("lotSizeFilter", {})
        step = float(lot.get("qtyStep", "0.001") or 0.001)
        min_q = float(lot.get("minOrderQty", "0.001") or 0.001)
        max_q = float(lot.get("maxOrderQty", "1e12") or "1e12")
        # FIX 2026-09-03: Decimal-aware rounding for float-step qty
        from decimal import Decimal, ROUND_DOWN
        qty = float(Decimal(str(qty_raw)).quantize(
            Decimal(str(step)).normalize(), rounding=ROUND_DOWN))
        if qty < min_q:
            qty = min_q
        if qty > max_q:
            qty = max_q
    except Exception:
        qty = round(notional / entry, 4)
        step = 0.001

    risk_usd = abs(entry - sl) * qty
    reward_usd = abs(tp - entry) * qty
    rr = reward_usd / risk_usd if risk_usd > 0 else 0
    f["qty"] = qty

    # FIX 2026-09-03 (MSFT bug): stale-price warning. Пока владелец проходил
    # шаги (сумма → плечо), цена могла уйти и лимитка стала marketable —
    # PostOnly получит EC_PostOnlyWillTakeLiquidity. Проверяем ДО кнопки.
    try:
        from tradingos.signals.auto_limit_placer import get_current_price as _gcp
        cur_now = _gcp(sym)
    except Exception:
        cur_now = 0
    stale_note = ""
    if cur_now > 0:
        if side == "LONG" and entry >= cur_now:
            stale_note = (f"\n⚠️ <b>ЦЕНА УШЛА:</b> сейчас {cur_now} < entry {entry}. "
                          f"Лимитка LONG выше цены отклонится (PostOnly). Нажми «📊 Новая рекомендация».")
        elif side == "SHORT" and entry <= cur_now:
            stale_note = (f"\n⚠️ <b>ЦЕНА УШЛА:</b> сейчас {cur_now} > entry {entry}. "
                          f"Лимитка SHORT ниже цены отклонится (PostOnly). Нажми «📊 Новая рекомендация».")

    _esc2 = _esc
    # FIX 2026-09-03: LADDER — 1-15 лимитных ордеров на сетап (лестница входа).
    # n_levels задаёт владелец (кнопки), маржа делится поровну, уровни
    # распределяются от entry в сторону SL (DCA-лестница).
    n_levels = int(f.get("n_levels", 1))
    ladder_rows = []
    if n_levels > 1:
        sl_dist = abs(entry - sl)
        # Шаг лестницы: sl_dist / n_levels — последний уровень у самого SL
        step_px = sl_dist / n_levels if sl_dist > 0 else 0
        ladder_rows_txt = []
        total_qty = 0.0
        for i in range(n_levels):
            # уровень i: entry ∓ i × step (глубже к SL)
            lvl_price = entry - i * step_px if side == "LONG" else entry + i * step_px
            lvl_qty = qty / n_levels
            total_qty += lvl_qty
            ladder_rows_txt.append(
                f"  L{i+1}: {_fmt_price(lvl_price)} × {lvl_qty:.4g}")
        ladder_txt = "\n".join(ladder_rows_txt)
        f["ladder"] = [{"price": (entry - i * step_px if side == "LONG" else entry + i * step_px),
                        "qty": qty / n_levels} for i in range(n_levels)]
    else:
        ladder_txt = f"  L1: {_fmt_price(entry)} × {qty:.4f}"
        f["ladder"] = [{"price": entry, "qty": qty}]

    kb_rows = [
        [InlineKeyboardButton("✅ ПОСТАВИТЬ", callback_data="BWS:place"),
         InlineKeyboardButton("✏️ Править уровни", callback_data="BWS:edit_levels")],
        [InlineKeyboardButton("💰 Изменить сумму", callback_data="BWS:edit_bet"),
         InlineKeyboardButton("⚙️ Изменить плечо", callback_data="BWS:edit_lev")],
    ]
    # Кнопки лестницы: 1-15 ордеров на РАЗНЫЕ монеты (портфель сетапов)
    kb_rows.append([InlineKeyboardButton(
        f"🎯 Портфель: {x} ордер(ов) на разные монеты", callback_data=f"BWS:portfolio:{x}")
        for x in (3, 5)])
    kb_rows.append([InlineKeyboardButton(
        f"🎯 Портфель: {x} ордеров на разные монеты", callback_data=f"BWS:portfolio:{x}")
        for x in (8, 15)])
    kb_rows.append([
        InlineKeyboardButton("📊 Новая рекомендация", callback_data="BWS:recommend"),
        InlineKeyboardButton("❌ Отмена", callback_data="BWS:cancel")])
    kb = InlineKeyboardMarkup(kb_rows)
    text = (
        f"📋 <b>ПЛАН СТАВКИ</b>\n\n"
        f"<b>{sym} {side}</b>\n"
        f"🛑 SL: <code>{_fmt_price(sl)}</code> ({(sl-entry)/entry*100:+.2f}%)\n"
        f"🎯 TP: <code>{_fmt_price(tp)}</code> ({(tp-entry)/entry*100:+.2f}%)\n\n"
        f"💰 Маржа: <code>${bet:,.0f}</code> × {lev}x = <code>${notional:,.0f}</code>\n"
        f"📦 Лимиток: <b>{n_levels}</b> (лестница к SL)\n"
        f"<code>{ladder_txt}</code>\n\n"
        f"⚠️ Риск по SL (если все филлятся): <code>${risk_usd:,.0f}</code>\n"
        f"🏆 Профит по TP: <code>${reward_usd:,.0f}</code>\n"
        f"⚖️ R:R: <code>{rr:.2f}</code>\n"
        f"{stale_note}\n"
        f"<i>Лестница = усреднение входа: чем глубже цена, тем больше филлов.\n"
        f"Подтверди или выбери число ордеров.</i>"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def handle_bet_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Текстовый ввод в визарде (символ, суммы, цены)."""
    uid = update.effective_user.id
    f = _FLOWS.get(uid)
    if not f:
        return False
    text = (update.effective_message.text or "").strip()
    step = f.get("step")

    if step == "symbol":
        sym = text.upper().replace("/", "").replace("-", "")
        if not sym.endswith("USDT"):
            sym += "USDT"
        f["symbol"] = sym
        f["step"] = "side"
        await _ask_side(update, sym)
        return True

    if step == "bet_usd":
        try:
            v = float(text.replace("$", "").replace(",", "").replace("%", ""))
        except ValueError:
            await update.effective_message.reply_text("❌ Введи число, например 1000 или 20%")
            return True
        if text.endswith("%"):
            eq = _get_equity()
            v = eq * v / 100 if eq else DEF_BET_USD
        f["bet_usd"] = max(10.0, v)
        f["step"] = "lev"
        await _ask_leverage(update, uid)
        return True

    if step in ("lev", "lev_edit"):
        try:
            v = int(text.rstrip("x").replace("x", ""))
        except ValueError:
            await update.effective_message.reply_text("❌ Введи целое число плеча, например 10")
            return True
        if not 1 <= v <= 50:
            await update.effective_message.reply_text("❌ Плечо 1-50")
            return True
        f["lev"] = v
        f["step"] = "plan"
        await _show_plan(update, uid)
        return True

    if step in ("entry_edit", "sl_edit", "tp_edit"):
        try:
            v = float(text)
        except ValueError:
            await update.effective_message.reply_text("❌ Введи цену числом")
            return True
        f[step.split("_")[0]] = v
        nxt = {"entry_edit": "sl_edit", "sl_edit": "tp_edit", "tp_edit": "plan"}[step]
        if step == "sl_edit":
            f["sl"] = v
        elif step == "entry_edit":
            f["entry"] = v
        elif step == "tp_edit":
            f["tp"] = v
        f["step"] = nxt
        prompts = {"sl_edit": "Шаг — SL (стоп):", "tp_edit": "Шаг — TP (тейк):"}
        if nxt == "plan":
            await _show_plan(update, uid)
        else:
            await update.effective_message.reply_text(f"📍 {prompts[nxt]}")
        return True

    return False


async def bet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопки визарда: BWS:*"""
    q = update.callback_query
    data = q.data or ""
    uid = update.effective_user.id
    await q.answer()
    f = _FLOWS.get(uid)

    # ── Кнопки из PUSH-карточки (bet_push): открыть сетап / скрыть ──
    if data.startswith("BWS:open:"):
        sym = data.split(":", 2)[2]
        r = recommend(sym)
        if not r or not r.get("side"):
            await q.message.reply_text(
                f"⚠️ <b>{sym}: сетап уже не актуален</b> (рынок ушёл из зоны / нет направления).\n"
                f"Жди новый пуш или введи <code>/bet {sym}</code> для свежего расчёта.",
                parse_mode="HTML")
            return
        # Предзаполняем визард уровнями из карточки
        _FLOWS[uid] = {
            "symbol": sym, "side": r["side"], "rec": r,
            "entry": r["entry"], "sl": r["sl"], "tp": r["tp"],
            "bet_usd": DEF_BET_USD, "lev": DEF_LEV,
            "step": "plan",
        }
        await q.message.reply_text(_recommend_text(r), parse_mode="HTML")
        await _show_plan(update, uid)
        return

    if data.startswith("BWS:dismiss:"):
        # Скрыть сетап этого символа на 24ч (записать в push-state cooldown)
        try:
            sym = data.split(":", 2)[2]
            push_state = ROOT / "operations/bet_push_state.json"
            if push_state.exists():
                st = json.loads(push_state.read_text())
                st.setdefault("sent", {})[sym] = time.time()
                tmp = push_state.with_suffix(".tmp")
                tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False))
                tmp.replace(push_state)
        except Exception:
            pass
        try:
            await q.message.edit_text("🗑 Сетап скрыт на 24 часа.")
        except Exception:
            await q.message.reply_text("🗑 Сетап скрыт на 24 часа.")
        return

    # ── Выбор проверенного сетапа кнопкой (шаг 1) ──
    if data.startswith("BWS:pick:"):
        sym = data.split(":", 2)[2]
        setups = _get_verified_setups()
        r = setups.get(sym)
        if not r:
            await q.message.reply_text(
                f"⚠️ <b>{sym}</b>: сетап устарел. Жми «🔄 Сканировать» или введи символ.",
                parse_mode="HTML")
            return
        _FLOWS[uid] = {
            "symbol": sym, "side": r["side"], "rec": r,
            "entry": r["entry"], "sl": r["sl"], "tp": r["tp"],
            "bet_usd": DEF_BET_USD, "lev": DEF_LEV,
            "step": "plan",
        }
        await q.message.reply_text(_recommend_text(r), parse_mode="HTML")
        await _show_plan(update, uid)
        return

    # ── Ввести символ вручную (кнопка) ──
    if data == "BWS:manual_sym":
        _FLOWS[uid] = {"step": "symbol"}
        await q.message.reply_text(
            "🔎 Введи символ вручную (например <code>BTC</code>, <code>SOL</code>):",
            parse_mode="HTML")
        return

    # ── Сканировать рынок сейчас (запустить bet_push) ──
    if data == "BWS:scan":
        await q.message.reply_text("🔄 Сканирую рынок, подожди ~30с...")
        import subprocess as _sp
        try:
            _sp.run(["/usr/bin/python3",
                     "/root/tradingos/telegram_control/bet_push.py"],
                    timeout=120, capture_output=True)
        except Exception:
            pass
        setups = _get_verified_setups()
        if not setups:
            await q.message.reply_text(
                "😴 Проверенных сетапов сейчас нет. Рынок спокоен — жди пуш.",
                parse_mode="HTML")
            return
        rows = []
        for sym in sorted(setups, key=lambda s: -float(setups[s].get("ts", 0)))[:8]:
            r = setups[sym]
            emoji = "🟢" if r["side"] == "LONG" else "🔴"
            rows.append([InlineKeyboardButton(
                f"{emoji} {sym} {r['side']} @{r['entry']:.4g}",
                callback_data=f"BWS:pick:{sym}")])
        rows.append([InlineKeyboardButton("🔎 Другой символ (ввести)", callback_data="BWS:manual_sym")])
        rows.append([InlineKeyboardButton("❌ Отмена", callback_data="BWS:cancel")])
        await q.message.reply_text(
            f"🎲 Найдено {len(setups)} проверенных сетапов:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
        return

    # FIX 2026-09-03: если визард не активен (f=None) — НЕ молчать, а подсказать.
    # Раньше после постановки ставки _FLOWS.pop(uid) очищал состояние, и клик
    # «Новая рекомендация» на старом плане молча игнорировался.
    if not f:
        if data == "BWS:cancel":
            await q.message.reply_text("❌ Визард отменён")
            return
        await q.message.reply_text(
            "⚠️ <b>Визард не активен</b> (ставка уже поставлена или сессия сброшена).\n"
            "Начни заново: <code>/bet SYMBOL</code> или нажми кнопку «🎲 Ставка».",
            parse_mode="HTML")
        return

    if data == "BWS:cancel":
        _FLOWS.pop(uid, None)
        await q.message.reply_text("❌ Визард отменён")
        return

    if data == "BWS:recommend":
        r = recommend(f["symbol"])
        if not r or not r.get("side"):
            await q.message.reply_text(
                f"❌ {f['symbol']}: направленного сетапа нет (D1 MIXED или данных мало).\n"
                f"Введи другой символ: <code>/bet SYMBOL</code>", parse_mode="HTML")
            return
        f["rec"] = r
        f["side"] = r["side"]
        f["entry"] = r["entry"]
        f["sl"] = r["sl"]
        f["tp"] = r["tp"]
        if "bet_usd" not in f:
            f["bet_usd"] = DEF_BET_USD
        if "lev" not in f:
            f["lev"] = DEF_LEV
        f["step"] = "plan"
        await q.message.reply_text(_recommend_text(r), parse_mode="HTML")
        await _show_plan(update, uid)
        return

    if data.startswith("BWS:side:"):
        f["side"] = data.split(":")[2]
        f["step"] = "recommend_or_manual"
        # Auto-recommend for this side
        r = recommend(f["symbol"])
        if r and r.get("side"):
            # Use recommended levels even if side differs
            f["entry"] = r["entry"]
            f["sl"] = r["sl"]
            f["tp"] = r["tp"]
            f["rec"] = r
        if "bet_usd" not in f:
            f["bet_usd"] = DEF_BET_USD
        if "lev" not in f:
            f["lev"] = DEF_LEV
        f["step"] = "bet_usd"
        await _ask_bet_usd(update, uid)
        return

    if data.startswith("BWS:bet:"):
        v = data.split(":")[2]
        if v.startswith("pct"):
            eq = _get_equity()
            pct = 10 if v == "pct10" else 20
            f["bet_usd"] = eq * pct / 100 if eq else DEF_BET_USD
        else:
            f["bet_usd"] = float(v)
        f["step"] = "lev"
        await _ask_leverage(update, uid)
        return

    if data.startswith("BWS:lev:"):
        f["lev"] = int(data.split(":")[2])
        f["step"] = "plan"
        await _show_plan(update, uid)
        return

    if data.startswith("BWS:ladder:"):
        # FIX 2026-09-03: выбор числа лимитных ордеров (1-15) в лестнице
        try:
            n = int(data.split(":")[2])
            f["n_levels"] = max(1, min(15, n))
        except (ValueError, IndexError):
            f["n_levels"] = 1
        f["step"] = "plan"
        await _show_plan(update, uid)
        return

    if data.startswith("BWS:portfolio:"):
        # FIX 2026-09-03: пакетная установка 1-15 лимиток на РАЗНЫЕ монеты
        # (портфель проверенных сетапов). Бюджет делится поровну.
        try:
            n = max(1, min(15, int(data.split(":")[2])))
        except (ValueError, IndexError):
            n = 3
        await _place_portfolio(update, uid, n)
        return

    if data == "BWS:edit_bet":
        f["step"] = "bet_usd"
        await q.message.reply_text("💰 Введи новую сумму маржи (например 1000 или 20%):")
        return

    if data == "BWS:edit_lev":
        f["step"] = "lev_edit"
        await q.message.reply_text("⚙️ Введи плечо числом (например 10):")
        return

    if data == "BWS:edit_levels":
        f["step"] = "entry_edit"
        await q.message.reply_text("📍 Введи цену ВХОДА (лимитка):")
        return

    if data == "BWS:place":
        await _place_bet(update, uid)
        return


async def _place_bet(update: Update, uid: int):
    """Поставить лимитку по плану + зарегистрировать в мониторе."""
    f = _FLOWS.get(uid)
    if not f:
        return
    sym, side = f["symbol"], f["side"]
    entry, sl, tp = f["entry"], f["sl"], f["tp"]
    bet, lev, qty = f["bet_usd"], f["lev"], f.get("qty", 0)
    # FIX 2026-09-03: if qty was never computed (direct from push card or text entry),
    # compute it now so we never send qty=0 to Bybit.
    if qty <= 0:
        try:
            notional = bet * lev
            import httpx as _hx
            from decimal import Decimal, ROUND_DOWN
            with _hx.Client() as c:
                ri = c.get("https://api.bybit.com/v5/market/instruments-info",
                           params={"category": "linear", "symbol": sym}).json()
                ri_list = ri.get("result", {}).get("list", [{}])
                lot = ri_list[0].get("lotSizeFilter", {})
            step = float(lot.get("qtyStep", "0.001") or 0.001)
            min_q = float(lot.get("minOrderQty", "0.001") or 0.001)
            qty_raw = notional / entry
            qty = float(Decimal(str(qty_raw)).quantize(
                Decimal(str(step)).normalize(), rounding=ROUND_DOWN))
            if qty < min_q:
                qty = min_q
        except Exception:
            qty = round((bet * lev) / entry, 4)
    try:
        sys = __import__("sys")
        sys.path.insert(0, "/root")
        sys.path.insert(0, "/root/tradingos")
        from tradingos.signals.auto_limit_placer import place_limit, set_trading_stop
        # leverage
        import urllib.parse
        from tradingos.signals.auto_limit_placer import _signed_post
        body = urllib.parse.urlencode({"category": "linear", "symbol": sym,
                                        "buyLeverage": str(lev), "sellLeverage": str(lev)})
        _signed_post("/v5/position/set-leverage", body)

        # FIX 2026-09-03: LADDER placement — все уровни (1-15) как отдельные
        # PostOnly лимитки. Ошибки одного уровня не отменяют остальные.
        ladder = f.get("ladder") or [{"price": entry, "qty": qty}]
        placed, failed = [], []
        for i, lvl in enumerate(ladder):
            lp, lq = lvl["price"], lvl["qty"]
            r = place_limit(sym, side, lp, lq, sl, tp, f"L{i+1}")
            if r.get("ok"):
                placed.append({"level": i + 1, "price": lp, "qty": lq,
                               "order_id": r.get("order_id")})
            else:
                failed.append({"level": i + 1, "price": lp, "error": r.get("error", "")})
            time.sleep(0.15)  # rate limit
        if not placed:
            err = failed[0].get("error", "") if failed else "unknown"
            kb_fail = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 Обновить рекомендацию (свежая цена)", callback_data="BWS:recommend")],
                [InlineKeyboardButton("✏️ Ввести свою цену входа", callback_data="BWS:edit_levels")],
                [InlineKeyboardButton("❌ Отмена", callback_data="BWS:cancel")],
            ])
            await update.effective_message.reply_text(
                f"❌ <b>Ордера не приняты</b>\n{_esc(err)}\n\n"
                f"<i>Цена ушла, пока ты настраивал ставку. Обнови рекомендацию — "
                f"уровни пересчитаются от текущей цены.</i>",
                parse_mode="HTML", reply_markup=kb_fail)
            return
        oid = placed[0].get("order_id")
        # Register in monitor state (все уровни в одном рекорде)
        st = json.loads(STATE.read_text()) if STATE.exists() else {"active_limits": {}, "filled": {}}
        st.setdefault("active_limits", {})[sym] = {
            "placed_at": time.time(),
            "side": side,
            "sl": sl, "tp": tp,
            "l1_price": placed[0].get("price", entry),
            "l2_price": placed[-1].get("price", 0.0) if len(placed) > 1 else 0.0,
            "l1_order_id": oid,
            "l2_order_id": placed[-1].get("order_id") if len(placed) > 1 else None,
            "qty": sum(p["qty"] for p in placed),
            "owner_bet": True,
            "owner_ladder": placed,  # полный список уровней для монитора
            "n_levels": len(placed),
            "rr_l1": round(abs(tp - entry) / abs(entry - sl), 2) if abs(entry - sl) > 0 else 0,
            "rr_l2": 0.0,
            "note": f"OWNER BET via /bet wizard: ${bet:,.0f} margin {lev}x, {len(placed)}-leg ladder",
        }
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False))
        tmp.replace(STATE)

        notional = sum(p["price"] * p["qty"] for p in placed)
        risk = abs(entry - sl) * sum(p["qty"] for p in placed)
        reward = abs(tp - entry) * sum(p["qty"] for p in placed)
        ladder_report = "\n".join(
            f"  L{p['level']}: {_fmt_price(p['price'])} × {p['qty']:.4g}" for p in placed)
        fail_note = ""
        if failed:
            fail_note = "\n⚠️ <b>Не приняты:</b>\n" + "\n".join(
                f"  L{f_['level']} @ {_fmt_price(f_['price'])}: {_esc(f_['error'][:80])}"
                for f_ in failed)
        await update.effective_message.reply_text(
            f"✅ <b>СТАВКА ПОСТАВЛЕНА: {len(placed)} лимиток</b>\n\n"
            f"{sym} {side}\n"
            f"<code>{ladder_report}</code>\n\n"
            f"Ноушнл (если все филлятся): ${notional:,.0f} (маржа ${bet:,.0f} × {lev}x)\n"
            f"SL {_fmt_price(sl)} → риск ${risk:,.0f}\n"
            f"TP {_fmt_price(tp)} → профит ${reward:,.0f}"
            f"{fail_note}\n\n"
            f"<i>После филла SL/TP прикрепятся автоматически.\n"
            f"Ставка живёт 24ч (structural entry ждёт откат).\n"
            f"+0.7R → частичное закрытие 30% + SL в безубыток.</i>",
            parse_mode="HTML")
        _FLOWS.pop(uid, None)
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Ошибка ставки: {_esc(e)}", parse_mode="HTML")


async def bet_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /betcancel — отменить текущий визард."""
    uid = update.effective_user.id
    _FLOWS.pop(uid, None)
    await update.effective_message.reply_text("❌ Визард ставки отменён")


async def _place_portfolio(update: Update, uid: int, n: int):
    """FIX 2026-09-03: пакетная установка N лимиток (1-15) на РАЗНЫЕ монеты.

    Источник: проверенные сетапы из bet_setups.json (кэш bet_push).
    Бюджет (bet_usd × lev) делится поровну на N ордеров.
    Каждый сетап: PostOnly-лимитка у структуры + SL/TP после филла.
    Ошибки одной монеты не отменяют остальные."""
    f = _FLOWS.get(uid, {})
    setups = _get_verified_setups()
    if not setups:
        await update.effective_message.reply_text(
            "😴 <b>Проверенных сетапов нет.</b>\n"
            "Нажми «🔄 Сканировать рынок сейчас» или жди push.",
            parse_mode="HTML")
        return
    # Топ-N по quality_score (сильные первыми)
    ranked = sorted(setups.values(),
                    key=lambda r: -float(r.get("quality_score", 0)))
    chosen = ranked[:n]
    bet = f.get("bet_usd", DEF_BET_USD)
    lev = int(f.get("lev", DEF_LEV))
    eq = _get_equity()
    # Giant-guard: общий notional ≤ 5% equity, на ордер ≤ 5%/N
    budget = min(bet * lev, MAX_NOTIONAL_USD)
    if eq > 0:
        budget = min(budget, eq * MAX_EQUITY_PCT)
    per_order = budget / len(chosen)

    placed, failed = [], []
    for r in chosen:
        sym, side = r["sym"], r["side"]
        entry, sl, tp = r["entry"], r["sl"], r["tp"]
        # qty из per_order ноушнла
        try:
            import httpx as _hx
            from decimal import Decimal, ROUND_DOWN
            with _hx.Client() as c:
                ri = c.get("https://api.bybit.com/v5/market/instruments-info",
                           params={"category": "linear", "symbol": sym}).json()
                lot = (ri.get("result", {}).get("list", [{}]))[0].get("lotSizeFilter", {})
            step = float(lot.get("qtyStep", "0.001") or 0.001)
            min_q = float(lot.get("minOrderQty", "0.001") or 0.001)
            qty_raw = per_order / entry
            qty = float(Decimal(str(qty_raw)).quantize(
                Decimal(str(step)).normalize(), rounding=ROUND_DOWN))
            if qty < min_q:
                qty = min_q
        except Exception:
            qty = round(per_order / entry, 4)
        # Stale-check: entry с правильной стороны от живой цены
        try:
            from tradingos.signals.auto_limit_placer import get_current_price as _gcp
            cur = _gcp(sym)
        except Exception:
            cur = 0
        if cur > 0 and ((side == "LONG" and entry >= cur) or (side == "SHORT" and entry <= cur)):
            failed.append({"sym": sym, "price": entry,
                           "error": f"цена ушла (cur {cur:.4g}), PostOnly отклонит"})
            continue
        try:
            from tradingos.signals.auto_limit_placer import place_limit, _signed_post
            import urllib.parse as _up
            body = _up.urlencode({"category": "linear", "symbol": sym,
                                  "buyLeverage": str(lev), "sellLeverage": str(lev)})
            _signed_post("/v5/position/set-leverage", body)
            res = place_limit(sym, side, entry, qty, sl, tp, "PORTFOLIO")
            if res.get("ok"):
                placed.append({"sym": sym, "side": side, "price": entry, "qty": qty,
                               "sl": sl, "tp": tp, "order_id": res.get("order_id")})
            else:
                failed.append({"sym": sym, "price": entry, "error": res.get("error", "")})
        except Exception as e:
            failed.append({"sym": sym, "price": entry, "error": str(e)[:80]})
        time.sleep(0.2)  # rate limit

    # Регистрация всех placed в мониторе
    if placed:
        st = json.loads(STATE.read_text()) if STATE.exists() else {"active_limits": {}, "filled": {}}
        for p in placed:
            st.setdefault("active_limits", {})[p["sym"]] = {
                "placed_at": time.time(),
                "side": p["side"],
                "sl": p["sl"], "tp": p["tp"],
                "l1_price": p["price"], "l2_price": 0.0,
                "l1_order_id": p["order_id"], "l2_order_id": None,
                "qty": p["qty"],
                "rr_l1": round(abs(p["tp"] - p["price"]) / abs(p["price"] - p["sl"]), 2)
                         if abs(p["price"] - p["sl"]) > 0 else 0,
                "rr_l2": 0.0,
                "owner_bet": True,
                "note": f"OWNER PORTFOLIO bet: ${per_order:,.0f} margin {lev}x",
            }
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False))
        tmp.replace(STATE)

    # Отчёт
    ok_rows = "\n".join(
        f"  ✅ {p['sym']} {p['side']} @ {_fmt_price(p['price'])} × {p['qty']:.4g}"
        for p in placed)
    fail_rows = "\n".join(
        f"  ❌ {x['sym']} @ {_fmt_price(x['price'])}: {_esc(x['error'][:60])}"
        for x in failed)
    text = f"🎯 <b>ПОРТФЕЛЬ: {len(placed)}/{len(chosen)} лимиток поставлено</b>\n\n"
    if ok_rows:
        text += f"<b>Поставлены:</b>\n<code>{ok_rows}</code>\n\n"
    if fail_rows:
        text += f"<b>Отклонены:</b>\n<code>{fail_rows}</code>\n\n"
    text += (f"💰 На ордер: ${per_order:,.0f} маржа (× {lev}x = ${per_order * lev:,.0f} ношнл)\n"
             f"🛡️ Бюджет портфеля: ${budget:,.0f} (cap 5% equity)\n"
             f"<i>Каждая лимитка: SL/TP после филла, живёт 24ч.</i>")
    await update.effective_message.reply_text(text, parse_mode="HTML")
    _FLOWS.pop(uid, None)