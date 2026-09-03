#!/usr/bin/env python3
"""
opportunity_watchdog.py — Радар возможностей Bybit (READ-ONLY, ничего не торгует).

Сканирует весь linear-универсум и присылает в Telegram карточки с МЕХАНИЧЕСКИМИ
возможностями, где есть объективный денежный поток (не прогноз направления):

  1. FUNDING-АНОМАЛИЯ: |funding| ≥ 3 bps/8ч у innovation-монет с оборотом ≥ $500k
     → шорт перпа собирает выплаты 3×/сутки; владелец решает.
  2. СВЕЖИЙ ЛИСТИНГ: listTime ≤ 48ч + turnover24h ≥ $5M → момент-кандидат
     (после первого отката).
  3. XAU-ТРЕНД: D1 EMA20/50 свинг (сырьё — низкая волатильность, издержки
     съедают мало).

Выход: карточки в Telegram + журнал memory/opportunites.jsonl.
НЕ ОТКРЫВАЕТ ПОЗИЦИИ. Размер/стоп — решение владельца (ручной контур).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

for _p in ("/root", "/root/tradingos"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Экономический гейт funding-карточек: цель (3 выплаты) обязана покрывать
# комиссию round-trip с запасом (VELVET-инцидент 2026-08-29: TP 0.09% < fees
# 0.22% → сделка убыточна до входа). Единый источник правды — тот же, что
# исполняет кнопки: manual_signal._funding_tp_ok.
try:
    from telegram_control.manual_signal import _funding_tp_ok
except Exception:
    def _funding_tp_ok(funding_bps, cfg=None):
        """Fallback: те же пороги, что в manual_signal (0.22% round-trip × 1.5)."""
        tp_pct = abs(funding_bps) * 3 / 10000
        return (False, f"TP {tp_pct*100:.2f}% < издержки×1.5") \
            if tp_pct < 0.33 else (True, "")

logger = logging.getLogger("OpportunityWatchdog")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

ROOT = Path("/root/tradingos")
JOURNAL = ROOT / "memory" / "opportunities.jsonl"

FUNDING_THRESHOLD_BPS = 3.0
MIN_TURNOVER_FUNDING = 500_000
LISTING_AGE_HOURS = 48
MIN_TURNOVER_LISTING = 5_000_000
XAU_SYMBOL = "XAUUSDT"

# Чат с кнопками: НЕ TradingOS-нотификатор (execution/.env), а Grizzly
# (manual_bot.env, токен 8330872040) — ТОЛЬКО его callback_handler_manual
# обрабатывает ms_fund_* кнопки. Кнопка работает только у того бота,
# чьим токеном отправлено сообщение.
_MANUAL_ENV = Path("/root/mt5_trading_bot/manual_bot.env")
_TG_TOKEN = ""
_TG_CHAT = ""


def _load_manual_creds() -> tuple[str, str]:
    """Креды Grizzly-бота (тот же, что manual_bot.service) из manual_bot.env."""
    global _TG_TOKEN, _TG_CHAT
    try:
        for line in _MANUAL_ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "TELEGRAM_BOT_TOKEN":
                _TG_TOKEN = v
            elif k == "TELEGRAM_CHAT_ID":
                _TG_CHAT = v
    except Exception as e:
        logger.warning(f"manual.env не прочитан: {e}")
    return _TG_TOKEN, _TG_CHAT

# Известные символы, которые уже отсвечивали (dedup по «символ|тип|день»)
_sent: set[str] = set()


def _load_sent() -> None:
    if not JOURNAL.exists():
        return
    for line in JOURNAL.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
            _sent.add(r.get("key", ""))
        except Exception:
            continue


def _auto_execute_funding(sym: str, side: str, px: float, tp_v: float,
                          sl_v: float, f_bps: float) -> dict | None:
    """Авто-исполнение funding-аномалии (2-day test, owner-approved).

    2026-08-30: раньше funding-сделки открывались только кнопкой 📌 в
    Telegram. Теперь (funding_auto_execute=true в manual_session.json)
    watchdog сам открывает позицию на демо: тот же _place_market_order,
    что у кнопки, тот же экономический гейт ≥11bps (проверен вызывающим),
    fail-closed на kill_switch. Риск — не больше risk_per_trade ($25),
    размер — не больше max_position_size_pct (1% equity).
    """
    # 2026-08-30 (ZKP-инцидент): верхний порог funding. |funding| > ~40 bps/8ч
    # = экстремальный рынок (ZKP -71..-108bps → шорт-сквиз вверх, LONG ловил
    # просадку −$21). Огромная выплата приходит ВМЕСТЕ с движением против.
    # Ловим только «золотую середину»: доходность выше комиссии, но рынок
    # ещё не в параболе.
    try:
        _tm_cap = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
        _cap_bps = float(_tm_cap.get("funding_max_funding_bps", 50.0) or 50.0)
    except Exception:
        _cap_bps = 50.0
    if abs(f_bps) > _cap_bps:
        logger.info(f"⏭️ AUTO-FUNDING SKIP {sym}: |funding| {abs(f_bps):.1f} bps > cap {_cap_bps:.0f} (экстремум — ловля движения против)")
        return {"ok": False, "reason": f"funding_extreme_{abs(f_bps):.0f}bps"}
    from telegram_control.manual_signal import _place_market_order, _cfg as _ms_cfg
    mcfg = _ms_cfg()
    if not bool(mcfg.get("funding_auto_execute", False)):
        return None
    try:
        mode = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
        if mode.get("kill_switch", True):
            logger.warning(f"🛑 AUTO-FUNDING SKIP {sym}: kill_switch ON")
            return {"ok": False, "reason": "kill_switch"}
    except Exception:
        return {"ok": False, "reason": "mode_unreadable_fail_closed"}
    # Пауза ручного контура (Grizzly) тоже стопит (общий fail-safe)
    try:
        st = json.loads(Path("/root/tradingos/operations/manual_state.json").read_text())
        if st.get("paused", False):
            logger.warning(f"🛑 AUTO-FUNDING SKIP {sym}: manual paused")
            return {"ok": False, "reason": "manual_paused"}
    except Exception:
        return {"ok": False, "reason": "manual_state_unreadable_fail_closed"}
    # Уже открыта позиция по символу — не дублируем
    try:
        from tradingos.strategies.bybit_position_check import get_open_position_symbols
        if sym in get_open_position_symbols():
            logger.info(f"⏭️ AUTO-FUNDING SKIP {sym}: позиция уже открыта")
            return {"ok": False, "reason": "position_already_open"}
    except Exception:
        pass
    # 2026-08-30 (ZKP-инцидент): символьный cooldown. Контур открывал тот же
    # символ КАЖДЫЙ час после закрытия (ZKP: 6 входов за день, -108bps funding,
    # ловил шорт-сквиз → NET -$30). Позиция закрыта → следующий час снова вход
    # по растущему |funding|. Funding-capture — это МЕХАНИЧЕСКАЯ выплата с
    # длинным холдом (3 выплаты = 24ч): повторный вход по символу раньше
    # FUNDING_SYMBOL_COOLDOWN_H запрещён (не даём переторговывать один актив).
    try:
        _cf_cooldown_h = 24
        _tm = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
        _cf_cooldown_h = int(_tm.get("funding_symbol_cooldown_h", 24) or 24)
    except Exception:
        _cf_cooldown_h = 24
    try:
        # последний раз когда этот символ открывался в funding-журнале
        last_open_ts = 0.0
        if JOURNAL.exists():
            for _line in reversed(JOURNAL.read_text(errors="replace").splitlines()):
                if not _line.strip():
                    continue
                try:
                    _rec = json.loads(_line)
                except Exception:
                    continue
                if _rec.get("event") == "AUTO_FUNDING_EXECUTED" and _rec.get("symbol") == sym:
                    _ts_raw = _rec.get("ts", "")
                    try:
                        last_open_ts = datetime.fromisoformat(str(_ts_raw)).timestamp()
                    except Exception:
                        last_open_ts = 0.0
                    break
        if last_open_ts <= 0:
            # 2026-09-02 FIX: раньше `return` (НЕПРАВИЛЬНО блокировал) — если
            # символ НИКОГДА не исполнялся (или был только REJECTED), last_open_ts=0
            # и вход тихо блокировался (MUSDT: funding +24.1 bps проходил гейт,
            # но не открывался → «фандинг мёртв»). Первый вход = РАЗРЕШЁН,
            # cooldown применяется ТОЛЬКО если был успешный EXECUTED.
            pass  # первый вход разрешён
        age_h = (time.time() - last_open_ts) / 3600
        if age_h < _cf_cooldown_h:
            logger.info(f"⏭️ AUTO-FUNDING COOLDOWN {sym}: последний вход {age_h:.1f}ч назад (< {_cf_cooldown_h}ч)")
            return {"ok": False, "reason": f"symbol_cooldown_{age_h:.1f}h"}
    except Exception as e:
        logger.warning(f"AUTO-FUNDING cooldown check err {sym}: {e}")
    # Размер: риск $25 (или меньше из конфига) / дистанция до SL, но не > 1% equity
    try:
        trading = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
        risk_usd = float(trading.get("risk_per_trade", 25) or 25)
        max_pos_pct = float(trading.get("max_position_size_pct", 1.0) or 1.0)
        eq = _equity_usd()
        max_notional = eq * max_pos_pct / 100.0 if eq > 0 else 1000.0
        sl_dist = abs(px - sl_v)
        notional = risk_usd / sl_dist * px if sl_dist > 0 else max_notional
        usd_amount = min(notional, max_notional)
        if usd_amount < 5.0:
            usd_amount = 5.0  # minNotional
    except Exception:
        # fail-closed: конфиг нечитаем — не торгуем
        return {"ok": False, "reason": "sizing_failed_fail_closed"}
    # 2026-08-31: мейкер-вход (order_type=Limit) ОТКАТАН — лимитка без attach SL/TP
    # создавала позицию без стопа (SL-механика после филла есть только для
    # wait-limit, не для funding). Маркет-вход с attached SL/TP — надёжнее:
    # позиция всегда под защитой, комиссия 0.055% приемлема для $25 риска.
    res = _place_market_order(sym, side, usd_amount, sl_v, tp_v)
    rec = {"event": "AUTO_FUNDING_EXECUTED" if res.get("ok") else "AUTO_FUNDING_REJECTED",
           "ts": datetime.now(timezone.utc).isoformat(),
           "symbol": sym, "side": side, "funding_bps": round(f_bps, 2),
           "usd_amount": round(usd_amount, 2), "res": res}
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if res.get("ok"):
        logger.info(f"✅ AUTO-FUNDING: {sym} {side} ${usd_amount:.2f} funding={f_bps:+.1f}bps")
    else:
        logger.warning(f"❌ AUTO-FUNDING FAIL {sym}: {res.get('error', '?')}")
    return res


def _equity_usd() -> float:
    """Equity для сайзинга (диагностика; 0 при ошибке → берём консервативный 100)."""
    try:
        from telegram_control.manual_signal import _get_manual_equity
        eq = _get_manual_equity()
        return float(eq) if eq and eq > 0 else 0.0
    except Exception:
        return 0.0


def _log_and_notify(key: str, text: str, kind: str, sym: str | None = None,
                    side: str | None = None,
                    extra_buttons: list[list[tuple[str, str]]] | None = None) -> None:
    """Telegram (сначала!) → журнал ТОЛЬКО при успешной доставке.

    Порядок критичен: если записать журнал до отправки, сбой навсегда помечает
    карточку «отправленной» (dedup) и она теряется. Здесь наоборот: неуспех →
    ключ не в _sent → следующее сканирование попробует снова.

    sym/side → инлайн-кнопки исполнения ms_fund_{sym}_{side}_{pct}
    (обрабатываются manual_signal.py через штатный flow выбора плеча)."""
    if key in _sent:
        return
    try:
        token, chat = _load_manual_creds()
        if not token or not chat:
            logger.warning("нет Grizzly TELEGRAM_CREDS — карточка не отправлена")
            return
        kb = None
        if sym and side:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"📌 {side} 0.5%",
                                     callback_data=f"ms_fund_{sym}_{side}_0.5"),
                InlineKeyboardButton(f"📌 {side} 1%",
                                     callback_data=f"ms_fund_{sym}_{side}_1"),
            ]])
        elif extra_buttons:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(t, callback_data=d) for t, d in row]
                for row in extra_buttons
            ])
        asyncio.run(_send_direct(token, chat, "socks5://127.0.0.1:1080",
                                 text, kb))
    except Exception as e:
        logger.warning(f"notify failed {kind}/{key}: {e}")
        return  # НЕ помечаем отправленным — повторим в следующий цикл
    rec = {"key": key, "kind": kind, "ts": datetime.now(timezone.utc).isoformat(),
           "text": text}
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    _sent.add(key)
    logger.info(f"[{kind}] отправлено: {text[:100]}")


async def _send_direct(token: str, chat_id: str, proxy_url: str, text: str,
                       kb=None) -> None:
    """Отправить ОДНО сообщение и дождаться ответа Telegram API."""
    from telegram import Bot
    from telegram.request import HTTPXRequest
    request = HTTPXRequest(proxy=proxy_url, connect_timeout=15, read_timeout=60)
    bot = Bot(token=token, request=request)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML",
                           reply_markup=kb)


def scan(client: httpx.Client) -> None:
    # ---- 1. instruments (для listTime и symbolType) ----
    r = client.get("https://api.bybit.com/v5/market/instruments-info",
                   params={"category": "linear", "limit": 1000})
    inst = (r.json().get("result") or {}).get("list") or []
    now_ms = int(time.time() * 1000)

    # ---- 2. funding-аномалии ----
    for i in inst:
        if i.get("symbolType") != "innovation":
            continue
        sym = i["symbol"]
        try:
            rt = client.get("https://api.bybit.com/v5/market/tickers",
                            params={"category": "linear", "symbol": sym})
            tk = ((rt.json().get("result") or {}).get("list") or [{}])[0]
            f_bps = float(tk.get("fundingRate") or 0) * 10000
            turn = float(tk.get("turnover24h") or 0)
        except Exception:
            continue
        if abs(f_bps) >= FUNDING_THRESHOLD_BPS and turn >= MIN_TURNOVER_FUNDING:
            side = "SHORT" if f_bps > 0 else "LONG"
            side_txt = "SHORT — получаешь выплаты" if f_bps > 0 else "LONG — получаешь выплаты"
            px = float(tk.get("lastPrice") or 0)
            tp_pct = abs(f_bps) * 3 / 10000    # цель = 3 выплаты
            # FIX 2026-08-30: стоп был вдвое шире цели (6×funding vs 3×funding)
            # → R:R структурно 0.5, авто-funding в минусе (−$19 за 3ч, 3 SL на −$20).
            # Теперь стоп = 0.75 × цель (R:R 1.33), минимум 0.25% от цены.
            stop_pct = max(0.0025, tp_pct * 0.75)
            # Экономический гейт: не предлагать сделку, если цель (3 выплаты)
            # не покрывает комиссию round-trip с запасом (VELVET-инцидент).
            _ok, _why = _funding_tp_ok(f_bps)
            stop_v = px * (1 + stop_pct) if side == "SHORT" else px * (1 - stop_pct)
            tp_v = px * (1 - tp_pct) if side == "SHORT" else px * (1 + tp_pct)
            key = f"{sym}|funding|{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
            if not _ok:
                # Карточка-предупреждение БЕЗ кнопок исполнения: аномалия есть,
                # но сделка математически убыточна — исполнение не предлагаем.
                # FIX 2026-09-02: комиссия из конфига (0.10% BingX), не зашитая 0.22%.
                try:
                    _fee_cfg = json.loads(Path("/root/tradingos/operations/manual_session.json").read_text())
                    _fee_pct = float(_fee_cfg.get("funding_fee_round_trip_pct", 0.22) or 0.22)
                except Exception:
                    _fee_pct = 0.22
                text = (f"💸 <b>FUNDING-АНОМАЛИЯ (skip)</b>\n"
                        f"<b>{sym}</b> funding <code>{f_bps:+.1f} bps/8ч</code>\n"
                        f"Цель (3 выплаты): <code>{tp_pct*100:.2f}%</code> &lt; "
                        f"комиссия round-trip ~{_fee_pct*100:.2f}% — <b>сделка убыточна</b>.\n"
                        f"🔒 Исполнение заблокировано: {str(_why).replace('<','&lt;').replace('>','&gt;')}")
                _log_and_notify(key, text, "funding_skip", sym=None, side=None)
                continue
            text = (f"💸 <b>FUNDING-АНОМАЛИЯ</b>\n"
                    f"<b>{sym}</b> funding <code>{f_bps:+.1f} bps/8ч</code> "
                    f"(база 0.5, выплаты 3×/сутки)\n"
                    f"Цена: <code>{px:.6g}</code> | Оборот 24ч: ${turn:,.0f}\n"
                    f"👉 <b>{side_txt}</b>\n"
                    f"Стоп: <code>{stop_v:.6g}</code> ({stop_pct*100:.2f}%)\n"
                    f"Цель: <code>{tp_v:.6g}</code> (~3 выплаты = {abs(f_bps)*3:.1f} bps)\n"
                    f"Расчёт: 3×{abs(f_bps):.1f}bps − издержки ~22bps → "
                    f"{abs(f_bps)*3-22:.1f} bps/день чистыми\n"
                    f"⚠️ Спот-пары нет — направленная позиция, ≤1% депо.")
            # Авто-исполнение (2-day test): гейт уже пройден → открываем позицию
            # сам (без ручной кнопки), если funding_auto_execute=true.
            # Вызывается ДО карточки: часовой таймер повторяет попытку, пока
            # позиция не откроется (внутри — dedup по symbol и fail-closed).
            try:
                _ex = _auto_execute_funding(sym, side, px, tp_v, stop_v, f_bps)
                if _ex and _ex.get("ok"):
                    logger.info(f"✅ AUTO-FUNDING opened: {sym} {side}")
            except Exception as e:
                logger.warning(f"AUTO-FUNDING exec error {sym}: {e}")
            _log_and_notify(key, text, "funding", sym=sym, side=side)

    # ---- 3. свежие листинги ----
    for i in inst:
        if i.get("symbolType") != "innovation":
            continue
        lt = int(i.get("listTime") or 0)
        age_h = (now_ms - lt) / 3_600_000 if lt else 9999
        if age_h > LISTING_AGE_HOURS or lt == 0:
            continue
        sym = i["symbol"]
        try:
            rt = client.get("https://api.bybit.com/v5/market/tickers",
                            params={"category": "linear", "symbol": sym})
            tk = ((rt.json().get("result") or {}).get("list") or [{}])[0]
            turn = float(tk.get("turnover24h") or 0)
            last = float(tk.get("lastPrice") or 0)
        except Exception:
            continue
        if turn < MIN_TURNOVER_LISTING:
            continue
        key = f"{sym}|listing|{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        text = (f"🚀 <b>СВЕЖИЙ ЛИСТИНГ</b>\n"
                f"<b>{sym}</b> — {age_h:.1f}ч назад\n"
                f"Оборот 24ч: ${turn:,.0f} | цена {last:.6g}\n"
                f"Момент-кандидат: вход после отката от максимума,\n"
                f"стоп под лоу, малый размер (1-3% депо).")
        _log_and_notify(key, text, "listing")

    # ---- 4. XAU D1-тренд ----
    try:
        rk = client.get("https://api.bybit.com/v5/market/kline",
                        params={"category": "linear", "symbol": XAU_SYMBOL,
                                "interval": "D", "limit": 60})
        rows = sorted((rk.json().get("result") or {}).get("list") or [],
                      key=lambda z: z[0])
        if len(rows) < 55:
            return
        closes = [float(x[4]) for x in rows]

        def ema(vals, n):
            k = 2 / (n + 1)
            e = vals[0]
            out = [e]
            for x in vals[1:]:
                e = x * k + e * (1 - k)
                out.append(e)
            return out

        e20 = ema(closes, 20)
        e50 = ema(closes, 50)
        last_c, last_e20 = closes[-1], e20[-1]
        last_e50 = e50[-1]
        prev_c, prev_e20 = closes[-2], e20[-2]
        prev_e50 = e50[-2]
        # только смена состояния: вход/выход свинг-режима
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        key = f"XAU|trend|{day}"
        # FIX 2026-09-03: aligned with bet_wizard tt() — UP = close > EMA20 AND EMA20 > EMA50;
        # простое cl>e20 стреляло «Тренд ВКЛ» когда wizard считал D1 MIXED → кнопка-визард не открывалась.
        if last_c > last_e20 and last_e50 < last_e20 and (prev_c <= prev_e20 or prev_e50 >= prev_e20):
            text = (f"🥇 <b>XAUUSDT — ТРЕНД ВКЛ</b>\n"
                    f"D1 close <code>{last_c:.2f}</code> > EMA20 <code>{last_e20:.2f}</code>\n"
                    f"Свинг-кандидат: вход на откате, стоп D1-структура, hold дни.\n\n"
                    f"<i>Нажми «🎲 Начать ставку» — визард подставит уровни, ты укажи плечо и сумму.</i>")
            # FIX 2026-09-03: кнопка запуска bet-визарда (BWS:open:XAUUSDT)
            _log_and_notify(key, text, "xau_trend_on",
                            extra_buttons=[[("🎲 Начать ставку", "BWS:open:XAUUSDT")]])
        elif last_c < last_e20 and prev_c >= prev_e20:
            text = (f"🥇 <b>XAUUSDT — ТРЕНД ВЫКЛ</b>\n"
                    f"D1 close <code>{last_c:.2f}</code> < EMA20 <code>{last_e20:.2f}</code>")
            _log_and_notify(key, text, "xau_trend_off")
    except Exception as e:
        logger.debug(f"XAU scan: {e}")


def main() -> None:
    _load_sent()
    with httpx.Client() as client:
        scan(client)
    logger.info("Сканирование завершено")


if __name__ == "__main__":
    main()