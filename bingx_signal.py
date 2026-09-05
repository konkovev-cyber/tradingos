#!/usr/bin/env python3
"""
bingx_signal.py — ручной BingX сигнальный контур (2026-08-30, owner).

Поток: пользователь присылает сигнал (например: "RUNE LONG 0.4813 0.457 0.55 x20")
       → preflight-анализ (R:R, ликвидация vs SL, текущая цена, размер 20% equity)
       → подтверждение → лимитка на BingX → SL/TP (STOP_MARKET/TAKE_PROFIT_MARKET)
       → лог. Дальнейшую защиту (BE/Partial/Tight/Trail) берёт bingx_guardian
       автоматически (читает те же ключи из /opt/ubot_bingx/.env).

Использование:
  python3 bingx_signal.py analyze RUNE LONG 0.4813 0.457 0.55 x20
  python3 bingx_signal.py place  RUNE LONG 0.4813 0.457 0.55 x20 [--force]
  python3 bingx_signal.py stops  RUNE LONG 0.457 0.55
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

ENV = "/opt/ubot_bingx/.env"
LOG = "/root/tradingos/logs/bingx_signal.jsonl"

logger = logging.getLogger("bingx_signal")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

SIZE_PCT = 20.0          # % от equity на позицию (ноушнл)
MIN_RR = 1.5             # min R:R
MAX_LEVERAGE = 20        # жёсткий потолок плеча
SL__MIN_PCT = 2.0        # min дистанция SL от входа (%)
TP_MAX_PCT = 30.0        # max дистанция TP от входа (%)


def _fmt(symbol: str) -> str:
    """BTCUSDT → BTC-USDT (BingX формат)."""
    s = symbol.upper().replace("/", "").replace("-", "")
    return f"{s[:-4]}-USDT" if s.endswith("USDT") else s


def _client():
    """Рабочий BingX-клиент из trading_brain_v4 (подпись/форматы уже валидны)."""
    import sys as _sys
    if "/root/trading_brain_v4" not in _sys.path:
        _sys.path.insert(0, "/root/trading_brain_v4")
    from exchange.bingx.client import BingXClient
    ak = as_ = ""
    for l in open(ENV):
        l = l.strip()
        if l.startswith("BINGX_API_KEY="):
            ak = l.split("=", 1)[1].strip()
        elif l.startswith("BINGX_API_SECRET="):
            as_ = l.split("=", 1)[1].strip()
    return BingXClient(api_key=ak, api_secret=as_)


def get_balance() -> dict:
    with _client() as c:
        return c.get_wallet_balance()


def get_ticker(symbol: str) -> dict:
    with _client() as c:
        return c.get_ticker(_fmt(symbol))


def parse_signal(tokens) -> dict | None:
    """Парсит: SYMBOL SIDE ENTRY SL TP [xLEV]. Пример: RUNE LONG 0.4813 0.457 0.55 x20"""
    if len(tokens) < 5:
        return None
    sym = tokens[0].upper().replace("-", "").replace("/", "")
    if not sym or "USDT" not in sym:
        sym = sym + "USDT"
    side = "LONG" if tokens[1].upper() in ("LONG", "BUY", "L") else "SHORT"
    try:
        entry = float(tokens[2]); sl = float(tokens[3]); tp = float(tokens[4])
    except ValueError:
        return None
    lev = 10
    if len(tokens) >= 6:
        lv = tokens[5].lower().replace("x", "")
        try:
            lev = int(float(lv))
        except ValueError:
            pass
    return {"symbol": sym, "side": side, "entry": entry, "sl": sl, "tp": tp, "lev": lev}


def analyze(sig: dict) -> dict:
    """Preflight: риск, R:R, ликвидация vs SL, текущая цена. Ничего не исполняет."""
    entry, sl, tp, lev = sig["entry"], sig["sl"], sig["tp"], sig["lev"]
    is_long = sig["side"] == "LONG"

    sl_pct = (entry - sl) / entry * 100 if is_long else (sl - entry) / entry * 100
    tp_pct = (tp - entry) / entry * 100 if is_long else (entry - tp) / entry * 100
    rr = tp_pct / sl_pct if sl_pct > 0 else 0

    liq = entry * (1 - 1 / lev) if is_long else entry * (1 + 1 / lev)
    sl_crosses_liq = (sl < liq) if is_long else (sl > liq)
    gap_to_liq_pct = (sl - liq) / entry * 100 if is_long else (liq - sl) / entry * 100

    cur = None
    try:
        t = get_ticker(sig["symbol"])
        if isinstance(t, dict):
            cur = float(t.get("lastPrice") or t.get("price") or 0) or None
    except Exception:
        cur = None

    issues = []
    if rr < MIN_RR:
        issues.append(f"R:R {rr:.2f} < {MIN_RR}")
    if lev > MAX_LEVERAGE:
        issues.append(f"плечо {lev}x > max {MAX_LEVERAGE}x")
    if sl_crosses_liq:
        issues.append(f"SL {sl} ЗА ликвидацией {liq:.4f} — ликвидация раньше стопа!")
    elif gap_to_liq_pct < 1.0:
        issues.append(f"SL в {gap_to_liq_pct:.2f}% от ликвидации — очень тесно")
    if sl_pct < SL__MIN_PCT:
        issues.append(f"SL дистанция {sl_pct:.2f}% < {SL__MIN_PCT}%")
    if tp_pct > TP_MAX_PCT:
        issues.append(f"TP дистанция {tp_pct:.2f}% > {TP_MAX_PCT}%")
    if cur and (abs(cur - entry) / entry * 100) > 2.0:
        issues.append(f"текущая {cur} далеко от лимитки {entry} ({(abs(cur-entry)/entry*100):.2f}%)")

    equity = 0.0
    try:
        bal = get_balance()
        equity = float((bal.get("balance") or {}).get("equity", 0) or 0)
    except Exception:
        pass
    notional = equity * SIZE_PCT / 100

    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "symbol": sig["symbol"], "side": sig["side"],
        "entry": entry, "sl": sl, "tp": tp, "lev": lev,
        "sl_pct": round(sl_pct, 2), "tp_pct": round(tp_pct, 2), "rr": round(rr, 2),
        "liq": round(liq, 4), "liq_pct": round(abs(1/lev) * 100, 2),
        "gap_to_liq_pct": round(gap_to_liq_pct, 2),
        "cur": cur,
        "equity": round(equity, 2), "notional": round(notional, 2),
    }


def place(sig: dict, force: bool = False) -> dict:
    """Лимитка + SL/TP на BingX (реальный счёт)."""
    a = analyze(sig)
    if not a["ok"] and not force:
        return {"ok": False, "errors": a["issues"], "analysis": a}
    sym = sig["symbol"]
    bn_side = "BUY" if a["side"] == "LONG" else "SELL"
    close_side = "SELL" if bn_side == "BUY" else "BUY"

    try:
        with _client() as c:
            # 1. Плечо
            c.set_leverage(_fmt(sym), a["lev"])
            # 2. Лимитка (вход)
            qty = round(a["notional"] / a["entry"], 6)
            if qty <= 0:
                return {"ok": False, "error": "qty<=0", "analysis": a}
            order = c.create_order(
                symbol=sym, side=bn_side, quantity=qty,
                order_type="LIMIT", price=a["entry"],
                leverage=a["lev"], time_in_force="GTC",
            )
    except Exception as e:
        return {"ok": False, "error": str(e), "analysis": a}

    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "evt": "PLACED", "symbol": sym, "side": a["side"],
        "entry": a["entry"], "sl": a["sl"], "tp": a["tp"], "lev": a["lev"],
        "qty": qty, "notional": a["notional"], "order": order,
    }
    _log(rec)
    logger.info(f"📌 BINGX LIMIT: {sym} {a['side']} qty={qty} @ {a['entry']} SL={a['sl']} TP={a['tp']} x{a['lev']}")
    return {"ok": True, "analysis": a, "order": order}


def attach_stops(symbol: str, side: str, sl: float, tp: float) -> dict:
    """SL/TP через conditional orders (STOP_MARKET / TAKE_PROFIT_MARKET)."""
    close_side = "SELL" if side.upper() == "LONG" else "BUY"
    try:
        with _client() as c:
            ok = c.set_trading_stop(_fmt(symbol), close_side, sl, take_profit=tp)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    _log({"ts": datetime.now(timezone.utc).isoformat(), "evt": "STOPS_ATTACHED",
          "symbol": symbol, "side": side, "sl": sl, "tp": tp, "ok": ok})
    return {"ok": ok}


def _log(rec: dict):
    with open(LOG, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "digest":
        for d in position_digest_all():
            print(d["text"])
            print("—" * 40)
        return
    sig = parse_signal(sys.argv[2:])
    if not sig:
        print("❌ Не распознан сигнал. Формат: SYMBOL SIDE ENTRY SL TP [xLEV]\n"
              "   Пример: RUNE LONG 0.4813 0.457 0.55 x20")
        return
    if cmd == "analyze":
        a = analyze(sig)
        print(f"{'✅ OK' if a['ok'] else '❌ ПРОБЛЕМЫ'}: {a['symbol']} {a['side']} x{a['lev']}")
        print(f"  Вход {a['entry']} | SL {a['sl']} ({a['sl_pct']}%) | TP {a['tp']} ({a['tp_pct']}%) | R:R {a['rr']}")
        print(f"  Ликвидация ~{a['liq']} ({a['liq_pct']}% от входа) | SL до ликвидации: {a['gap_to_liq_pct']:+.2f}%")
        print(f"  Текущая цена: {a['cur']} | Equity ${a['equity']} → ноушнл {SIZE_PCT}% = ${a['notional']}")
        for i in a["issues"]:
            print(f"  ⚠️ {i}")
    elif cmd == "place":
        force = "--force" in sys.argv
        r = place(sig, force=force)
        if r.get("ok"):
            print("✅ Лимитка размещена. После филла: stops SYMBOL SIDE SL TP")
        else:
            print("❌", json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "stops":
        if len(sys.argv) < 6:
            print("Формат: stops SYMBOL SIDE SL TP")
            return
        r = attach_stops(sys.argv[2].upper(), sys.argv[3].upper(),
                         float(sys.argv[4]), float(sys.argv[5]))
        print(json.dumps(r, ensure_ascii=False, indent=2))


def get_positions_raw() -> list[dict]:
    """Все открытые BingX-позиции (normalized)."""
    with _client() as c:
        items = c.get_positions()
    out = []
    for p in items:
        amt = float(p.get("positionAmt", 0) or 0)
        if amt == 0:
            continue
        # FIX 2026-09-01 (side bug): BingX в однонаправленном режиме возвращает
        # positionAmt всегда ПОЛОЖИТЕЛЬНЫМ, реальная сторона — в positionSide
        # ("LONG"/"SHORT"). Раньше side определяли по знаку amt → SHORT-позиция
        # BNB (7.82) помечалась LONG → дайджест показывал «+0.18% uPnL −$9.62»
        # (цена выше входа у шорта = убыток, но формула считала как лонг).
        _ps = str(p.get("positionSide", "") or "").strip().upper()
        _side = "LONG" if _ps == "LONG" else "SHORT" if _ps == "SHORT" else None
        if _side is None:
            _side = "LONG" if amt > 0 else "SHORT"  # fallback по знаку (hedge-mode)
        out.append({
            "symbol": _our(p.get("symbol", "")),
            "side": _side,
            "qty": abs(amt),
            "entry": float(p.get("avgPrice", 0) or 0),
            "mark": float(p.get("markPrice", 0) or 0),
            "upnl": float(p.get("unRealizedProfit", 0) or 0) or float(p.get("unrealizedProfit", 0) or 0),
            "lev": float(p.get("leverage", 0) or 0),
            "liq": float(p.get("liquidationPrice", 0) or 0),
        })
    return out


def _our(bingx_symbol: str) -> str:
    return bingx_symbol.replace("-", "").replace("/", "")


def _close_side(side: str) -> str:
    return "SELL" if side == "LONG" else "BUY"


def position_digest_all() -> list[dict]:
    """Дайджест всех открытых BingX-позиций: статус + рекомендация (15-мин мониторинг)."""
    digests = []
    # SL/TP у BingX = отдельные conditional-ордера (STOP_MARKET / TAKE_PROFIT_MARKET)
    # на том же символе. Читаем их с биржи РАЗ на цикл — иначе digest врёт
    # «SL None | TP None», когда позиция открыта напрямую (не через наш журнал).
    exch_sl_tp: dict[str, tuple[float | None, float | None]] = {}
    try:
        with _client() as c:
            open_orders = c.get_open_orders() if hasattr(c, "get_open_orders") else []
        for o in open_orders or []:
            sym = _our(o.get("symbol", ""))
            otype = str(o.get("type", "")).upper()
            stop = float(o.get("stopPrice") or 0) or float(o.get("price") or 0)
            if stop <= 0:
                continue
            cur_sl, cur_tp = exch_sl_tp.get(sym, (None, None))
            if "STOP_MARKET" in otype:
                cur_sl = stop if cur_sl is None else cur_sl
            elif "TAKE_PROFIT" in otype:
                cur_tp = stop if cur_tp is None else cur_tp
            exch_sl_tp[sym] = (cur_sl, cur_tp)
    except Exception as _e:
        log.warning(f"exchange SL/TP fetch failed: {_e}")

    for p in get_positions_raw():
        sym, side = p["symbol"], p["side"]
        entry, mark, qty = p["entry"], p["mark"], p["qty"]
        liq = p.get("liq") or 0
        # Сначала биржевые conditional-ордера (источник правды), журнал — fallback
        sl, tp = exch_sl_tp.get(sym, (None, None))
        if not sl and not tp:
            try:
                if os.path.exists(LOG):
                    for l in reversed(open(LOG).readlines()):
                        try:
                            r = json.loads(l)
                        except Exception:
                            continue
                        if r.get("symbol") == sym and r.get("evt") == "PLACED":
                            sl = r.get("sl") if sl is None else sl
                            tp = r.get("tp") if tp is None else tp
                            break
            except Exception:
                pass
        # PnL в R и %
        notional = entry * qty
        pnl_pct = (mark - entry) / entry * 100 if side == "LONG" else (entry - mark) / entry * 100
        r_mult = 0.0
        if sl and entry and abs(entry - sl) > 0:
            r_dist = abs(entry - sl)
            r_mult = (mark - entry) / r_dist if side == "LONG" else (entry - mark) / r_dist
        # Рекомендация
        rec, rec_icon, actions = _suggest(side, pnl_pct, r_mult, mark, entry, sl, tp, liq, sym)
        digests.append({
            "symbol": sym, "side": side, "entry": entry, "mark": mark,
            "qty": qty, "upnl": p["upnl"], "lev": p["lev"],
            "liq": liq, "sl": sl, "tp": tp,
            "pnl_pct": round(pnl_pct, 2), "r_mult": round(r_mult, 2),
            "actions": actions,
            "text": (
                f"{rec_icon} <b>{sym} {side}</b> x{int(p['lev'] or 0)}  qty={qty:g}\n"
                f"Вход <code>{entry}</code> → сейчас <code>{mark}</code>\n"
                f"PnL: <b>{pnl_pct:+.2f}%</b> ({r_mult:+.2f}R)  uPnL ${p['upnl']:+.2f}\n"
                f"SL {sl} | TP {tp} | Ликвидация {liq}\n"
                f"💡 <b>Рекомендация:</b> {rec}"
            ),
        })
    return digests


def _suggest(side, pnl_pct, r_mult, mark, entry, sl, tp, liq, sym) -> tuple[str, str, list]:
    """Логика рекомендаций для 15-мин дайджеста. Возвращает (текст, иконка, actions).

    actions — список ключей кнопок, которые применимы к ситуации.
    Кнопки реализуются в Telegram-слое (см. manual_signal._bx_action).
    """
    if sl and liq and ((side == "LONG" and sl <= liq) or (side == "SHORT" and sl >= liq)):
        return ("СТОП НА ЛИКВИДАЦИИ — сдвинь SL выше ликвидации или снизь плечо",
                "🚨", ["sl_to_liq", "close"])
    if r_mult >= 1.5 and tp:
        return (f"У цели (TP {tp}) — держи, трейли SL за ценой", "🎯", ["trail", "hold"])
    if r_mult >= 1.0:
        return ("В плюсе >1R — перенеси SL в безубыток, дай цене идти к TP", "🟢",
                ["be", "trail", "hold"])
    if r_mult >= 0.5:
        return ("В плюсе — держи, стоп на месте", "✅", ["trail", "hold"])
    if pnl_pct <= -2.5:
        return ("В минусе >2.5% — рассмотри закрытие или перенос SL ближе", "🔴",
                ["close", "sl_tighten", "hold"])
    if pnl_pct <= -1.0:
        return ("В минусе — следи, стоп на месте", "🟠", ["sl_tighten", "hold"])
    return ("Всё ок, ждём движения к TP/SL", "⚪", ["hold"])


_ACTION_LABELS = {
    "hold": "⏸ Держать",
    "be": "🟢 SL в безубыток",
    "trail": "📈 Трейли SL",
    "sl_to_liq": "⬆️ SL выше ликвидации",
    "sl_tighten": "🔍 SL ближе",
    "close": "❌ Закрыть",
}


def action_label(key: str) -> str:
    return _ACTION_LABELS.get(key, key)


def bx_action(symbol: str, side: str, key: str) -> dict:
    """Выполнить действие рекомендации на BingX (реальный счёт).

    key: hold (no-op) | be | trail | sl_to_liq | sl_tighten | close.
    Возвращает {"ok": bool, "msg": str, "symbol":..., "side":...}.
    """
    sym = _fmt(symbol)
    bn_close = "SELL" if side.upper() == "LONG" else "BUY"
    # Текущая позиция (для qty и mark)
    with _client() as c:
        items = c.get_positions(sym)
    pos = None
    qty = 0.0
    mark = 0.0
    entry = 0.0
    for p in items:
        amt = float(p.get("positionAmt", 0) or 0)
        if amt != 0:
            pos = p
            qty = abs(amt)
            mark = float(p.get("markPrice", 0) or 0)
            entry = float(p.get("avgPrice", 0) or 0)
    if not pos or qty <= 0:
        return {"ok": False, "msg": f"нет открытой позиции {symbol}", "symbol": symbol, "side": side}

    # SL/TP из журнала
    sl = tp = None
    try:
        if os.path.exists(LOG):
            for l in reversed(open(LOG).readlines()):
                try:
                    r = json.loads(l)
                except Exception:
                    continue
                if r.get("symbol") == symbol.replace("-", "") and r.get("evt") == "PLACED":
                    sl, tp = r.get("sl"), r.get("tp")
                    break
    except Exception:
        pass

    is_long = side.upper() == "LONG"
    risk = abs(entry - sl) if (entry and sl) else entry * 0.02

    if key == "hold":
        return {"ok": True, "msg": "Оставляем как есть", "symbol": symbol, "side": side}
    if key == "close":
        try:
            with _client() as c:
                # FIX (2026-08-30 CRITICAL): reduce_only=True — без него маркет
                # против позиции РАЗВОРАЧИВАЕТ её (LONG→SHORT той же qty).
                c.create_order(symbol=symbol, side=bn_close, quantity=qty,
                               order_type="MARKET", reduce_only=True)
            _log({"ts": datetime.now(timezone.utc).isoformat(), "evt": "CLOSED",
                  "symbol": symbol, "side": side, "qty": qty, "reason": "action_recommendation"})
            return {"ok": True, "msg": f"Позиция закрыта ({qty:g})", "symbol": symbol, "side": side}
        except Exception as e:
            return {"ok": False, "msg": f"ошибка закрытия: {e}", "symbol": symbol, "side": side}
    if key == "be":
        # SL = вход (на стороне прибыли) — для LONG ниже entry? Нет: SL в 0 = entry
        new_sl = entry
        try:
            with _client() as c:
                ok = c.set_trading_stop(sym, bn_close, new_sl, take_profit=tp)
            _log({"ts": datetime.now(timezone.utc).isoformat(), "evt": "SL_BE",
                  "symbol": symbol, "side": side, "sl": new_sl})
            return {"ok": ok, "msg": f"SL → безубыток {new_sl:.6g}", "symbol": symbol, "side": side}
        except Exception as e:
            return {"ok": False, "msg": f"ошибка: {e}", "symbol": symbol, "side": side}
    if key == "trail":
        # Трейлинг: SL = mark ∓ 0.5R (фиксируем половину прибыли)
        trail = 0.5 * risk
        new_sl = mark - trail if is_long else mark + trail
        try:
            with _client() as c:
                ok = c.set_trading_stop(sym, bn_close, new_sl, take_profit=tp)
            _log({"ts": datetime.now(timezone.utc).isoformat(), "evt": "SL_TRAIL",
                  "symbol": symbol, "side": side, "sl": new_sl})
            return {"ok": ok, "msg": f"Трейлинг: SL → {new_sl:.6g} (mark ∓ 0.5R)",
                    "symbol": symbol, "side": side}
        except Exception as e:
            return {"ok": False, "msg": f"ошибка: {e}", "symbol": symbol, "side": side}
    if key == "sl_to_liq":
        # SL чуть выше ликвидации (LONG: выше; SHORT: ниже)
        try:
            new_sl = liq_price_of(pos) * 1.005 if is_long else liq_price_of(pos) * 0.995
        except Exception:
            new_sl = entry * 0.99 if is_long else entry * 1.01
        try:
            with _client() as c:
                ok = c.set_trading_stop(sym, bn_close, new_sl, take_profit=tp)
            _log({"ts": datetime.now(timezone.utc).isoformat(), "evt": "SL_ABOVE_LIQ",
                  "symbol": symbol, "side": side, "sl": new_sl})
            return {"ok": ok, "msg": f"SL → {new_sl:.6g} (выше ликвидации)",
                    "symbol": symbol, "side": side}
        except Exception as e:
            return {"ok": False, "msg": f"ошибка: {e}", "symbol": symbol, "side": side}
    if key == "sl_tighten":
        # Подтянуть SL к mark на стороне убытка: для LONG SL = mark*0.99; SHORT = mark*1.01
        new_sl = mark * 0.99 if is_long else mark * 1.01
        try:
            with _client() as c:
                ok = c.set_trading_stop(sym, bn_close, new_sl, take_profit=tp)
            _log({"ts": datetime.now(timezone.utc).isoformat(), "evt": "SL_TIGHTEN",
                  "symbol": symbol, "side": side, "sl": new_sl})
            return {"ok": ok, "msg": f"SL подтянут → {new_sl:.6g}", "symbol": symbol, "side": side}
        except Exception as e:
            return {"ok": False, "msg": f"ошибка: {e}", "symbol": symbol, "side": side}
    return {"ok": False, "msg": f"неизвестное действие {key}", "symbol": symbol, "side": side}


def liq_price_of(pos: dict) -> float:
    try:
        return float(pos.get("liquidationPrice", 0) or 0)
    except Exception:
        return 0.0


# ═══════════════════════════════════════════════════════════════
# УВЕДОМЛЕНИЯ НА ПРОБОЙ УРОВНЕЙ (2026-09-01, owner request)
# ═══════════════════════════════════════════════════════════════
LEVEL_ALERTS_PATH = "/root/tradingos/operations/level_alerts.json"


def _load_level_alerts() -> list[dict]:
    """Загрузить уровни из level_alerts.json."""
    try:
        if os.path.exists(LEVEL_ALERTS_PATH):
            with open(LEVEL_ALERTS_PATH) as f:
                cfg = json.load(f)
            return cfg.get("levels", [])
    except Exception as e:
        logger.error(f"load level_alerts: {e}")
    return []


def _save_level_alerts(levels: list[dict]) -> None:
    """Сохранить обновлённые уровни (после срабатывания)."""
    try:
        with open(LEVEL_ALERTS_PATH, "w") as f:
            json.dump({"levels": levels}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"save level_alerts: {e}")


def check_level_breaks() -> list[str]:
    """
    Проверить цену против настроенных уровней. Вызывается каждые 15 мин.
    Возвращает список сообщений для отправки в Telegram (пусто если ничего).

    break="up": цена >= level → сработало (для шортов = риск).
    break="down": цена <= level → сработало (для шортов = цель).
    active=true + не сработало ранее → нотификация. auto_disable → деактивация.
    """
    levels = _load_level_alerts()
    if not levels:
        return []
    # Собираем уникальные символы
    syms = {lv["symbol"] for lv in levels if lv.get("active")}
    prices: dict[str, float] = {}
    for sym in syms:
        try:
            import httpx
            _t = httpx.get(
                "https://open-api.bingx.com/openApi/swap/v2/quote/ticker",
                params={"symbol": _fmt(sym)}, timeout=8,
            ).json()
            _data = _t.get("data")
            # Один символ → dict {"lastPrice": ...}; список → первый элемент
            if isinstance(_data, dict):
                prices[sym] = float(_data.get("lastPrice") or 0)
            elif isinstance(_data, list) and _data:
                prices[sym] = float(_data[0].get("lastPrice") or 0)
        except Exception as e:
            logger.debug(f"level check price {sym}: {e}")
    msgs: list[str] = []
    changed = False
    for lv in levels:
        if not lv.get("active"):
            continue
        sym = lv["symbol"]
        px = prices.get(sym)
        if not px:
            continue
        level = float(lv["level"])
        direction = lv.get("break", "up")
        label = lv.get("label", "")
        hit = (px >= level) if direction == "up" else (px <= level)
        if hit:
            side_txt = "ВВЕРХ" if direction == "up" else "ВНИЗ"
            emoji = "🔴" if direction == "up" else "🟢"
            msgs.append(
                f"{emoji} <b>ПРОБОЙ УРОВНЯ</b> {sym}\n"
                f"Цена <code>{px}</code> пробила {side_txt} уровень <code>{level}</code>\n"
                f"📝 {label}"
            )
            lv["last_notified_ts"] = datetime.now(timezone.utc).isoformat()
            if lv.get("auto_disable", True):
                lv["active"] = False
            changed = True
    if changed:
        _save_level_alerts(levels)
    return msgs


if __name__ == "__main__":
    main()