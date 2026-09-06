#!/usr/bin/env python3
"""Каскад-сторож owner-лимиток (2026-09-06).

Если BTC падает быстрее порога — все LONG-лимитки вот-вот начнут заливать
одновременно (DCA-лестницы усредняют против тренда). Шлём мгновенный алерт
с кнопкой «Отменить все owner-лимитки» — решение за владельцем, но в один тап.

Таймер: каждые 2 минуты. Только чтение + алерт; ордера не трогает сам.
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path("/root/tradingos")
STATE = ROOT / "operations/auto_limit_state.json"
SENT_FLAG = Path("/tmp/cascade_alert_ts")
THROTTLE_MIN = 60  # не спамить чаще раза в час
DROP_ALERT_PCT = 1.5   # −1.5% за час = жёлтый
DROP_CRIT_PCT = 3.0    # −3% за час = красный

sys.path.insert(0, str(ROOT))
sys.path.insert(0, "/root/tradingos_lab")
try:
    from telegram_control.manual_signal import _load_credentials, _api_base  # noqa
except Exception:
    _load_credentials = None
    _api_base = lambda: "https://api.bybit.com"  # noqa


def _signed_get(path: str, query: str) -> dict:
    import hashlib
    import hmac
    import urllib.parse
    ak, as_ = _load_credentials()
    if not ak:
        return {}
    ts = str(int(time.time() * 1000))
    payload = f"{ts}{ak}5000{query}"
    sign = hmac.new(as_.encode(), payload.encode(), hashlib.sha256).hexdigest()
    r = httpx.get(_api_base() + path + "?" + query,
                  headers={"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                           "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sign},
                  timeout=10)
    return r.json()


def _kline_1h(symbol: str):
    r = httpx.get("https://api.bybit.com/v5/market/kline",
                  params={"category": "linear", "symbol": symbol, "interval": "60", "limit": 3},
                  timeout=10)
    try:
        lst = r.json()["result"]["list"]  # newest-first: [0]=текущий час
        return float(lst[1][4]), float(lst[0][4])  # close час назад, close текущего
    except Exception:
        return 0.0, 0.0


def _send_tg(text: str, kb=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("TG missing:\n" + text)
        return
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML",
               "disable_web_page_preview": True}
    if kb:
        payload["reply_markup"] = {"inline_keyboard": kb}
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json=payload, timeout=15)
    except Exception as e:
        print(f"send failed: {e}")


def check():
    if not STATE.exists():
        return
    st = json.loads(STATE.read_text())
    limits = st.get("active_limits", {})
    owner_long = [(s, l) for s, l in limits.items()
                  if l.get("owner_bet") and l.get("side") in ("LONG", "BUY")]
    if not owner_long:
        return
    btc_prev, btc_now = _kline_1h("BTCUSDT")
    if not btc_prev:
        return
    drop = (btc_now / btc_prev - 1) * 100
    if drop > -DROP_ALERT_PCT:
        return
    # throttle
    now = time.time()
    if SENT_FLAG.exists() and now - SENT_FLAG.stat().st_mtime < THROTTLE_MIN * 60:
        return
    total_risk = sum(abs(l.get("l1_price", 0) - l.get("sl", 0)) * float(l.get("qty", 0) or 0)
                     for _, l in owner_long if l.get("l1_price") and l.get("sl"))
    lvl = "🚨 КРИТИЧНО" if drop <= -DROP_CRIT_PCT else "⚠️ ВНИМАНИЕ"
    text = (f"{lvl} КАСКАД ЛИМИТОК\n"
            f"BTC {drop:+.2f}% за час ({btc_prev:.0f} → {btc_now:.0f})\n"
            f"Висит owner-лимиток LONG: <b>{len(owner_long)}</b>\n"
            f"Риск при полном заливе: <b>${total_risk:,.0f}</b>\n\n"
            f"<i>Падение тянет цену к уровням лимиток — начнут заливаться каскадом.</i>")
    kb = [[{"text": "🛑 ОТМЕНИТЬ ВСЕ owner-лимитки", "callback_data": "CASCADE:cancelall"}],
          [{"text": "Оставить как есть", "callback_data": "CASCADE:ignore"}]]
    _send_tg(text, kb)
    SENT_FLAG.write_text(str(now))


def cancel_all_owner_limits() -> int:
    """Отменить все owner-лимитки (вызывается из TG-callback CASCADE:cancelall)."""
    import hashlib
    import hmac
    import urllib.parse
    if not STATE.exists():
        return 0
    st = json.loads(STATE.read_text())
    ak, as_ = _load_credentials()
    if not ak:
        return 0
    cancelled = 0
    for sym, lim in list(st.get("active_limits", {}).items()):
        if not lim.get("owner_bet"):
            continue
        for oid_key in ("l1_order_id", "l2_order_id"):
            oid = lim.get(oid_key)
            if not oid:
                continue
            body = urllib.parse.urlencode({"category": "linear", "symbol": sym, "orderId": str(oid)})
            ts = str(int(time.time() * 1000))
            payload = f"{ts}{ak}5000{body}"
            sign = hmac.new(as_.encode(), payload.encode(), hashlib.sha256).hexdigest()
            try:
                r = httpx.post(_api_base() + "/v5/order/cancel",
                               content=body,
                               headers={"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                                        "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sign,
                                        "Content-Type": "application/x-www-form-urlencoded"},
                               timeout=10).json()
                if r.get("retCode") == 0 or "not exists" in str(r.get("retMsg", "")):
                    cancelled += 1
            except Exception:
                pass
        # убрать из state
        del st["active_limits"][sym]
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False))
    tmp.replace(STATE)
    return cancelled


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cancel":
        n = cancel_all_owner_limits()
        print(f"cancelled {n}")
    else:
        check()
