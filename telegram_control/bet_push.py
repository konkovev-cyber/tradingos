"""
bet_push.py — Проактивный поиск 100% сетапов и пуш в Telegram.

Каждые 30 мин сканирует вселенную bet_finder-критериями:
  1. Тренд H1+H4+D1 в одну сторону (3 таймфрейма согласны)
  2. Ликвидность ≥ $100k/час
  3. SL-дистанция ≤ 1.5%
  4. TP (3R) внутри 30D диапазона + ≥10 касаний (живая цель)
  5. RSI не в экстремуме против входа

Найденный сетап → карточка в Telegram с кнопкой «🎲 Начать ставку».
Кнопка запускает визард /bet с ПРЕДЗАПОЛНЕННЫМИ уровнями — владелец
остаётся с плечом и суммой, потом ставит.

Dedup: один символ не чаще раза в 4 часа (state: operations/bet_push_state.json).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/tradingos")

import httpx

from telegram_control.bet_wizard import recommend, _recommend_text

ROOT = Path("/root/tradingos")
STATE = ROOT / "operations/bet_push_state.json"
# Кэш проверенных сетапов: bet_push сохраняет PASS-сетапы, /bet визард
# показывает их КНОПКАМИ на шаге1 (без текстового ввода).
SETUPS_CACHE = ROOT / "operations/bet_setups.json"
COOLDOWN_H = 4

# Большой список: крипто majors + альтернативы + мемкоины + токенизированные акции (2026-09-03)
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT',
        'ADAUSDT', 'AVAXUSDT', 'LINKUSDT', 'DOTUSDT', 'LTCUSDT', 'UNIUSDT',
        'NEARUSDT', 'SUIUSDT', 'APTUSDT', 'OPUSDT', 'ARBUSDT', 'TIAUSDT',
        'ATOMUSDT', 'INJUSDT', 'SEIUSDT', 'FILUSDT', 'AAVEUSDT', 'MKRUSDT',
        'CRVUSDT', 'LDOUSDT', 'PENDLEUSDT', 'WLDUSDT', 'ONDOUSDT', 'PEPEUSDT',
        'WIFUSDT', 'BONKUSDT', 'FLOKIUSDT', 'SHIBUSDT', 'ORDIUSDT', 'SATSUSDT',
        'TRXUSDT', 'TONUSDT', 'ETCUSDT', 'XLMUSDT', 'HBARUSDT', 'ALGOUSDT',
        'VETUSDT', 'ICPUSDT', 'RNDRUSDT', 'FETUSDT', 'AGIXUSDT', 'GRTUSDT',
        'SANDUSDT', 'MANAUSDT', 'AXSUSDT', 'GALAUSDT', 'IMXUSDT', 'BLURUSDT',
        'JUPUSDT', 'WUSDT', 'ENAUSDT', 'ETHFIUSDT', 'ALTUSDT', 'PYTHUSDT',
        'JTOUSDT', 'TIAUSDT', 'STRKUSDT', 'ZKUSDT', 'ZROUSDT', 'LQTYUSDT',
        'NVDAUSDT', 'TSLAUSDT', 'AAPLUSDT', 'MSFTUSDT', 'GOOGLUSDT', 'AMZNUSDT',
        'METAUSDT', 'NFLXUSDT', 'INTCUSDT', 'AMDUSDT', 'COINUSDT', 'PLTRUSDT',
        'MSTRUSDT', 'MARUSDT', 'SOXLUSDT', 'TQQQUSDT', 'XAUUSDT', 'XAGUSDT',
        'SPCXUSDT', 'TEMUSDT', 'BBXUSDT', 'CLUSUSDT', 'GPROUSDT', 'MARAUSDT']

MAX_SL_PCT = 2.0      # relaxed: 2% SL allows more symbols
MIN_TOUCHES = 5       # relaxed: 5 touches minimum

# ─── AUTO-PORTFOLIO (2026-09-03, owner: «автоматизируй этот контур») ───
# После каждого скана ставим лимитки на топ-N проверенных сетапов (разные монеты).
PORTFOLIO_ENABLED = True
PORTFOLIO_N = 5            # ордеров на скан
PORTFOLIO_MARGIN_USD = 500 # маржа на ВСЁ портфель (делится на N)
PORTFOLIO_LEV = 10
PORTFOLIO_EQ_CAP = 0.05    # жёсткий кап: notional портфеля ≤ 5% equity
PORTFOLIO_MAX_SL_PCT = 2.0 # не ставим ордер с SL > 2%
PORTFOLIO_MIN_QUALITY = 60 # только качественные сетапы


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"sent": {}}


def _save_state(st: dict) -> None:
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False))
    tmp.replace(STATE)


def _tg_env() -> tuple[str, str]:
    env = {}
    for line in open("/root/mt5_trading_bot/manual_bot.env"):
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.strip().split("=", 1)
            env[k.strip()] = v.strip()
    return env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")


def send_bet_card(r: dict) -> bool:
    """Карточка сетапа + кнопка запуска визарда (уровни предзаполнятся)."""
    token, chat = _tg_env()
    if not token or not chat:
        return False
    side_emoji = "🟢 LONG" if r["side"] == "LONG" else "🔴 SHORT"
    sl_pct = r.get("sl_pct", 0)
    tp_pct = r.get("tp_pct", 0)
    text = (
        f"🎯 <b>СЕТАП 100%: {r['sym']} {r['side']}</b>\n\n"
        f"Тренд: <code>{r['trend']}</code> | RSI <code>{r['rsi']:.0f}</code>\n"
        f"Структура: <code>{r['struct_ref']}</code>\n\n"
        f"📍 Вход: <code>{r['entry']:.4f}</code> ({(r['entry']-r['price'])/r['price']*100:+.2f}% от текущей)\n"
        f"🛑 SL: <code>{r['sl']:.4f}</code> (-{sl_pct:.2f}%)\n"
        f"🎯 TP (3R): <code>{r['tp']:.4f}</code> (+{tp_pct:.2f}%)\n"
        f"{'✅' if r['tp_ok'] else '⚠️'} Цель в 30D диапазоне, касаний: {r['touches']}\n\n"
        f"<i>Нажми «Начать ставку» — уровни подставятся, останется плечо и сумма.</i>"
    )
    kb = {
        "inline_keyboard": [[
            {"text": "🎲 Начать ставку", "callback_data": f"BWS:open:{r['sym']}"},
        ], [
            {"text": "❌ Скрывать этот сетап 24ч", "callback_data": f"BWS:dismiss:{r['sym']}"},
        ]]
    }
    try:
        resp = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": int(_chat_id_safe()), "text": text,
                                "parse_mode": "HTML", "reply_markup": json.dumps(kb)},
                          timeout=10, proxy="socks5://127.0.0.1:1080")
        return resp.status_code == 200
    except Exception as e:
        print(f"TG send fail: {e}")
        return False


def _chat_id_safe() -> str:
    _, chat = _tg_env()
    return chat


def setup_passes(r: dict) -> bool:
    """Строгие критерии '100% сетапа'."""
    if not r or not r.get("side"):
        return False
    if r.get("tp_ok") is not True:
        return False
    if r.get("touches", 0) < MIN_TOUCHES:
        return False
    if r.get("sl_pct", 99) > MAX_SL_PCT:
        return False
    # Quality/momentum: must be strong enough
    if r.get("quality_score", 100) < 55:
        return False
    # Тренд-согласие: H1/H4 должны совпадать с D1 (или reversal с подтверждением)
    trend = r.get("trend", "")
    parts = dict(p.split("=") for p in trend.split() if "=" in p)
    side_dir = "UP" if r["side"] == "LONG" else "DOWN"
    reversal = r.get("reversal", False)
    if reversal:
        # Counter-trend: require H1/H4 confirmation of reversal direction (at least one matches)
        h1_dir = parts.get("H1", "")
        h4_dir = parts.get("H4", "")
        # If H1 and H4 both still original trend, reversal is too early
        if h1_dir == h4_dir and h1_dir != side_dir:
            return False
    else:
        # Trend-following: H1/H4 must align with D1
        if not all(parts.get(t) == side_dir for t in ("H1", "H4") if parts.get(t)):
            return False
    # RSI: reversal entries — extreme; trend entries — not extreme
    if reversal:
        if r["side"] == "LONG" and r.get("rsi", 50) > 50:  # reversal LONG = RSI oversold
            return False
        if r["side"] == "SHORT" and r.get("rsi", 50) < 50:  # reversal SHORT = RSI overbought
            return False
    else:
        if r["side"] == "LONG" and r.get("rsi", 50) > 72:
            return False
        if r["side"] == "SHORT" and r.get("rsi", 50) < 28:
            return False
    return True


def _read_env_float(path: str, key: str, default: float) -> float:
    try:
        for line in open(path):
            if line.startswith(f"{key}="):
                return float(line.split("=", 1)[1].strip())
    except Exception:
        pass
    return default


def _portfolio_equity() -> float:
    try:
        st = json.loads((ROOT / "operations/deposit_guard_state.json").read_text())
        v = st.get("last_equity")
        if v and float(v) > 0:
            return float(v)
    except Exception:
        pass
    return 0.0


def _portfolio_tg(token: str, chat: str, text: str) -> None:
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": int(chat), "text": text, "parse_mode": "HTML"},
                   timeout=10, proxy="socks5://127.0.0.1:1080")
    except Exception as e:
        print(f"TG report fail: {e}")


def place_portfolio(setups: dict) -> int:
    """AUTO: лимитки на топ-N разных монет из проверенных сетапов.

    Бюджет: PORTFOLIO_MARGIN_USD × lev, cap 5% equity. На ордер — равную долю.
    Фильтры: quality ≥ 60, SL ≤ 2%, entry с правильной стороны живой цены
    (иначе PostOnly отклонит). Возвращает число поставленных."""
    if not PORTFOLIO_ENABLED or not setups:
        return 0
    import sys
    sys.path.insert(0, "/root")
    sys.path.insert(0, "/root/tradingos")
    from tradingos.signals.auto_limit_placer import (
        place_limit, _signed_post, get_current_price,
        get_open_position_symbols,
    )
    from decimal import Decimal, ROUND_DOWN
    import urllib.parse

    ranked = sorted(setups.values(), key=lambda r: -float(r.get("quality_score", 0)))
    eq = _portfolio_equity()
    budget = PORTFOLIO_MARGIN_USD * PORTFOLIO_LEV
    if eq > 0:
        budget = min(budget, eq * PORTFOLIO_EQ_CAP)
    # Не ставить туда, где уже есть позиция/лимитка
    busy = set(get_open_position_symbols())
    try:
        al_state = json.loads((ROOT / "operations/auto_limit_state.json").read_text())
        busy |= set(al_state.get("active_limits", {}).keys())
    except Exception:
        pass

    placed, skipped, failed = [], [], []
    per_order = budget / max(PORTFOLIO_N, 1)
    # FIX 2026-09-04: квоты диверсификации — не 5 коррелированных LONG подряд
    # (ночной XRP/DOGE/SOL-кластер: 3 SHORT стопнулись одновременно).
    # MAX одинаковых сторон + минимум 1 слот reversal, если он есть в топе.
    MAX_SAME_SIDE = 3   # макс 3 LONG или 3 SHORT на портфель 5
    side_counts = {"LONG": 0, "SHORT": 0}
    reversal_slots = 1  # хотя бы 1 reversal-сетап, если проходит фильтры
    placed_reversal = 0
    for r in ranked:
        if len(placed) >= PORTFOLIO_N:
            break
        sym, side = r["sym"], r["side"]
        is_rev = bool(r.get("reversal"))
        # Квота стороны (reversal не считается в сторону — он контр-тренд)
        if not is_rev and side_counts.get(side, 0) >= MAX_SAME_SIDE:
            skipped.append(f"{sym}: квота {side} ({MAX_SAME_SIDE}) исчерпана")
            continue
        # Reversal-слот: максимум reversal_slots реверсалов
        if is_rev and placed_reversal >= reversal_slots:
            skipped.append(f"{sym}: reversal-слоты исчерпаны ({reversal_slots})")
            continue
        if sym in busy:
            continue
        if r.get("sl_pct", 99) > PORTFOLIO_MAX_SL_PCT:
            skipped.append(f"{sym}: SL {r.get('sl_pct', 0):.1f}% > {PORTFOLIO_MAX_SL_PCT}%")
            continue
        if float(r.get("quality_score", 0)) < PORTFOLIO_MIN_QUALITY:
            skipped.append(f"{sym}: quality {r.get('quality_score', 0)} < {PORTFOLIO_MIN_QUALITY}")
            continue
        entry, sl, tp = r["entry"], r["sl"], r["tp"]
        cur = get_current_price(sym)
        if cur > 0 and ((side == "LONG" and entry >= cur) or (side == "SHORT" and entry <= cur)):
            skipped.append(f"{sym}: цена ушла (cur {cur:.4g})")
            continue
        try:
            with httpx.Client() as c:
                ri = c.get("https://api.bybit.com/v5/market/instruments-info",
                           params={"category": "linear", "symbol": sym}, timeout=10).json()
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
        try:
            body = urllib.parse.urlencode({"category": "linear", "symbol": sym,
                                           "buyLeverage": str(PORTFOLIO_LEV),
                                           "sellLeverage": str(PORTFOLIO_LEV)})
            _signed_post("/v5/position/set-leverage", body)
            res = place_limit(sym, side, entry, qty, sl, tp, "AUTO-PORT")
            if res.get("ok"):
                placed.append({"sym": sym, "side": side, "price": entry, "qty": qty,
                               "sl": sl, "tp": tp, "order_id": res.get("order_id"),
                               "is_rev": is_rev})
                if is_rev:
                    placed_reversal += 1
                else:
                    side_counts[side] = side_counts.get(side, 0) + 1
                busy.add(sym)
            else:
                failed.append(f"{sym}: {res.get('error', '')[:60]}")
        except Exception as e:
            failed.append(f"{sym}: {str(e)[:60]}")
        time.sleep(0.2)

    if not placed:
        return 0
    # Регистрация в мониторе (owner_bet=True → 24ч TTL, не отменяются по 0.5%)
    st_path = ROOT / "operations/auto_limit_state.json"
    try:
        st = json.loads(st_path.read_text()) if st_path.exists() else {"active_limits": {}, "filled": {}}
        for p in placed:
            # FIX 2026-09-04: place_meta для fill-rate телеметрии (gap до цены)
            try:
                from tradingos.signals.auto_limit_placer import attach_place_meta
                meta = attach_place_meta(p["sym"], p["price"],
                                         get_current_price(p["sym"]),
                                         quality=None)
            except Exception:
                meta = {"gap_pct": None, "quality": None}
            st.setdefault("active_limits", {})[p["sym"]] = {
                "placed_at": time.time(),
                "side": p["side"], "sl": p["sl"], "tp": p["tp"],
                "l1_price": p["price"], "l2_price": 0.0,
                "l1_order_id": p["order_id"], "l2_order_id": None,
                "qty": p["qty"],
                "rr_l1": round(abs(p["tp"] - p["price"]) / abs(p["price"] - p["sl"]), 2)
                         if abs(p["price"] - p["sl"]) > 0 else 0,
                "owner_bet": True,
                "reversal": p.get("is_rev", False),
                "place_meta": meta,
                "note": f"AUTO-PORTFOLIO: ${per_order:,.0f} margin {PORTFOLIO_LEV}x",
            }
        tmp = st_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False))
        tmp.replace(st_path)
    except Exception as e:
        print(f"portfolio state write fail: {e}")

    # TG-отчёт
    token, chat = _tg_env()
    if token and chat:
        ok_rows = "\n".join(
            f"  ✅ {p['sym']} {p['side']}{' 🔄' if p.get('is_rev') else ''} @ {p['price']:.4g} × {p['qty']:.4g}"
            for p in placed)
        notes = ""
        if skipped:
            notes += "\n\n<b>Пропущены:</b>\n" + "\n".join(f"  ⏭ {s}" for s in skipped[:6])
        if failed:
            notes += "\n\n<b>Отклонены:</b>\n" + "\n".join(f"  ❌ {x}" for x in failed[:6])
        # FIX 2026-09-04: fill-rate отчёт по бакетам (в портфель-сообщении)
        fr_note = ""
        try:
            from tradingos.signals.auto_limit_placer import fillrate_report
            fr = fillrate_report()
            if fr and fr.get("outcomes"):
                oc = fr["outcomes"]
                fr_rows = []
                for b in ("0-0.3%", "0.3-0.6%", "0.6-1%", "1-2%", "2%+"):
                    if fr.get(f"{b}_n"):
                        fr_rows.append(f"  {b}: {fr.get(b, 0)}% (n={fr[f'{b}_n']})")
                if fr_rows:
                    fr_note = ("\n\n📊 <b>Fill-rate по отдалению:</b>\n"
                               + "\n".join(fr_rows)
                               + f"\n  всего: {oc.get('FILLED', 0)}✅ / "
                                 f"{oc.get('EXPIRED', 0)}⏰ / {oc.get('CANCELLED_MISSED', 0)}↗")
        except Exception:
            pass
        _portfolio_tg(token, chat,
                      f"🤖 <b>AUTO-ПОРТФЕЛЬ: {len(placed)} лимиток поставлено</b>\n\n"
                      f"<code>{ok_rows}</code>\n\n"
                      f"💰 ${per_order:,.0f} на ордер ({PORTFOLIO_LEV}x) | бюджет ${budget:,.0f}"
                      f"{notes}{fr_note}\n\n<i>SL/TP после филла, лимитки живут 24ч. "
                      f"Править: /limits или /bet SYMBOL.</i>")
    print(f"  🎯 AUTO-PORTFOLIO: placed {len(placed)}, skipped {len(skipped)}, failed {len(failed)}")
    return len(placed)


def scan_and_push():
    st = _load_state()
    now = time.time()
    pushed = 0
    for sym in SYMS:
        try:
            r = recommend(sym)
        except Exception:
            continue
        if not setup_passes(r):
            continue
        last = st["sent"].get(sym, 0)
        if now - last < COOLDOWN_H * 3600:
            continue
        if send_bet_card(r):
            st["sent"][sym] = now
            pushed += 1
            print(f"  📨 PUSH {sym} {r['side']} entry={r['entry']:.4f}")
        time.sleep(1.5)  # TG rate limit
    # FIX 2026-09-03: сохранить ПОЛНЫЙ список проверенных сетапов в кэш,
    # чтобы /bet визард показывал их кнопками на шаге1.
    try:
        setups = {}
        for sym in SYMS:
            try:
                r = recommend(sym)
            except Exception:
                continue
            if setup_passes(r):
                r["ts"] = time.time()
                setups[sym] = r
        tmp = SETUPS_CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(setups, indent=2, ensure_ascii=False))
        tmp.replace(SETUPS_CACHE)
    except Exception as e:
        print(f"setup cache write fail: {e}")
    # AUTO-PORTFOLIO: ставим лимитки на топ-N сетапов автоматически
    try:
        if setups:
            place_portfolio(setups)
    except Exception as e:
        print(f"auto-portfolio fail: {e}")
    # GC: чистим старше 7 дней
    st["sent"] = {s: t for s, t in st["sent"].items() if now - t < 7 * 86400}
    _save_state(st)
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M')}] scan done, pushed {pushed}")


if __name__ == "__main__":
    scan_and_push()