#!/usr/bin/env python3
"""
price_monitor.py — мониторинг рынка и счёта TradingOS.

Режимы:
  python3 price_monitor.py              # полный отчёт (hourly)
  python3 price_monitor.py --fast       # лёгкий отчёт (dedup, каждые 5 мин)

Интерфейс для setup_cards.py:
  pm.CANDIDATES          — список символов для сканирования
  pm.get_price(sym)      — текущая цена (Bybit public)
  pm.get_24h(sym)        — 24h change + volume
  pm.analyze(sym, price, change, volume) -> dict | None
  pm._sym_short(sym)     — короткое имя (BTCUSDT → BTC)
  pm.fmt_px(v)           — форматирование цены
  pm._load_keys()        — (ak, as_) из .bingx.env
  pm.bingx_balance(ak, as_) -> dict с полем "equity"
"""
from __future__ import annotations

import hashlib
import hmac
import httpx
import json
import logging
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/trading_brain_v4")

# ── Конфиг ─────────────────────────────────────────────────────────────
BINGX_BASE = "https://open-api.bingx.com"
BINGX_ENV = Path("/root/tradingos/operations/.bingx.env")
BYBIT_TICKERS = "https://api.bybit.com/v5/market/tickers?category=spot"

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8758713317:AAHExP0TX94dy6xWxVeILh_dDqs5eYMWgyg",
)
TELEGRAM_CHAT = int(os.getenv("TELEGRAM_CHAT_ID", "977966870"))

STATE_PATH = Path("/root/tradingos/operations/price_monitor_state.json")
LOG_PATH = Path("/root/tradingos/research/price_monitor.log")
FAST_LOG = Path("/root/tradingos/research/price_monitor_fast.log")

CANDIDATES: list[str] = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "LINKUSDT", "ADAUSDT", "AVAXUSDT", "LTCUSDT", "DOTUSDT", "MATICUSDT",
    "UNIUSDT", "ATOMUSDT", "ETCUSDT", "XLMUSDT", "ALGOUSDT", "FILUSDT",
    "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "SEIUSDT",
    "TRXUSDT", "RUNEUSDT", "INJUSDT", "RNDRUSDT", "FETUSDT", "IMXUSDT",
    "APEUSDT", "GALAUSDT", "SANDUSDT", "AXSUSDT", "CHZUSDT", "ZECUSDT",
    "DASHUSDT", "ENAAUSDT", "PENDLEUSDT", "TIAUSDT", "CELRUSDT",
    "STRKUSDT", "JUPUSDT", "PYTHUSDT", "WLDUSDT", "ONDOUSDT",
    "PENDLEUSDT", "ENAUSDT", "PEPEUSDT", "1000PEPEUSDT", "BOMEUSDT",
    "ENTRUSTUSDT", "FLOCKUSDT", "ZORAUSDT", "TUSDT", "AKTUSDT",
    "RENDERUSDT", "IOSTUSDT", "GMTUSDT", "APEUSDT", "BLURUSDT",
    "LDOUSDT", "SFPUSDT", "CETUSDT", "SXPUSDT", "ICPUSDT",
    "FLOWUSDT", "ROSEUSDT", "DODOUSDT", "TWTUSDT", "ANKRUSDT",
    "KAVAUSDT", "TOMOUSDT", "KNCUSDT", "ONEUSDT", "ZILUSDT",
    "CHRUSDT", "STXUSDT", "MINAUSDT", "AUDIOUSDT", "CVCUSDT",
    "BADGERUSDT", "FORTHUSDT", "BALUSDT", "CTKUSDT", "IOTAUSDT",
    "HNTUSDT", "CRVUSDT", "TWTUSDT", "HARDUSDT", "SFPUSDT",
    "DODOUSDT", "BTCSTUSDT", "TRBUSDT", "REELEUSDT", "ALICEUSDT",
    "HIVEUSDT", "SUSHUSDT", "RUNEUSDT",
]
# Убираем дубликаты и невалидные символы
CANDIDATES = sorted({s for s in CANDIDATES if s.endswith("USDT") and len(s) < 20})

# R:R параметры для classify_regime
SETUP_PARAMS = {
    "DIP":         {"entry": 0.97,  "sl": 0.96,  "tp": 1.12,  "ttl": 240},
    "CORRECTION":  {"entry": 0.985, "sl": 0.97,  "tp": 1.10,  "ttl": 180},
    "ACCUMULATION":{"entry": 0.99,  "sl": 0.975, "tp": 1.08,  "ttl": 360},
    "IMPULSE":     {"entry": 0.995, "sl": 0.98,  "tp": 1.06,  "ttl": 120},
}
COST_PCT = 0.002  # 0.2% entry + 0.2% exit


logger = logging.getLogger("price_monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


# ═══════════════════════════════════════════════════════════════════════
# Утилиты
# ═══════════════════════════════════════════════════════════════════════

def _sym_short(sym: str) -> str:
    """BTCUSDT → BTC, ETHUSDT → ETH."""
    return sym.replace("USDT", "")


def fmt_px(v: float) -> str:
    v = float(v)
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _tv_url(sym: str) -> str:
    """BINANCE:<SYM>USDT.P"""
    base = sym.replace("USDT", "")
    return f"https://www.tradingview.com/chart/?symbol=BINANCE:{base}USDT.P"


# ═══════════════════════════════════════════════════════════════════════
# BingX API (signed)
# ═══════════════════════════════════════════════════════════════════════

def _load_keys() -> tuple[str, str]:
    ak, as_ = "", ""
    try:
        with open(BINGX_ENV) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip()
                if k == "BINGX_API_KEY":
                    ak = v
                elif k in ("BINGX_SECRET_KEY", "BINGX_API_SECRET", "SECRET_KEY"):
                    as_ = v
    except FileNotFoundError:
        pass
    return ak, as_


def _bx_sign(params: dict, secret: str) -> str:
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()


def _bx_get(path: str, params: dict, ak: str, as_: str) -> dict:
    ts = str(int(time.time() * 1000))
    params = {**params, "timestamp": ts}
    sig = _bx_sign(params, as_)
    qs = urllib.parse.urlencode(params)
    url = f"{BINGX_BASE}{path}?{qs}&signature={sig}"
    r = httpx.get(url, headers={"X-BX-APIKEY": ak}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(f"BingX error {data.get('code')}: {data.get('msg')}")
    return data.get("data", data)


def bingx_balance(ak: str, as_: str) -> dict:
    """Возвращает {equity, free, ...}. Для fallback в setup_cards."""
    raw = _bx_get("/openApi/swap/v2/user/balance", {"asset": "USDT"}, ak, as_)
    # BingX returns: {"balance": {"equity": "...", "freezedMargin": "..."}}
    if isinstance(raw, dict):
        bal = raw.get("balance") or {}
        return {
            "equity": float(bal.get("equity", 0) or 0),
            "free": float(bal.get("availableMargin", 0) or bal.get("free", 0) or 0),
        }
    return {"equity": 0.0, "free": 0.0}


def bingx_positions(ak: str, as_: str) -> list[dict]:
    raw = _bx_get("/openApi/swap/v2/user/positions", {"category": "linear", "symbol": ""}, ak, as_)
    positions = raw.get("positions", []) if isinstance(raw, dict) else (raw or [])
    out = []
    for p in positions:
        amt = float(p.get("positionAmt", 0) or 0)
        if abs(amt) < 1e-10:
            continue
        side_raw = str(p.get("positionSide", "") or "").upper()
        side = "LONG" if side_raw == "LONG" else "SHORT"
        out.append({
            "symbol": p.get("symbol", "").replace("-", ""),
            "side": side,
            "qty": abs(amt),
            "entry": float(p.get("avgPrice", 0) or 0),
            "mark": float(p.get("markPrice", 0) or 0),
            "leverage": int(float(p.get("leverage", 1) or 1)),
            "liq": float(p.get("liquidationPrice", 0) or 0),
            "upnl": float(p.get("unRealizedProfit", 0) or p.get("unrealizedProfit", 0) or 0),
        })
    return out


def bingx_open_orders(ak: str, as_: str) -> list[dict]:
    raw = _bx_get("/openApi/swap/v2/trade/openOrders", {"category": "linear"}, ak, as_)
    if isinstance(raw, dict):
        orders = raw.get("orders")
        if isinstance(orders, dict):
            orders = orders.get("orders", [])
        orders = orders or []
    else:
        orders = raw or []
    return [
        {
            "symbol": o.get("symbol", "").replace("-", ""),
            "side": o.get("side", ""),
            "price": float(o.get("price", 0) or 0),
            "qty": float(o.get("qty", 0) or o.get("origQty", 0) or 0),
            "type": o.get("type", "LIMIT"),
        }
        for o in orders if float(o.get("qty", 0) or o.get("origQty", 0) or 0) > 0
    ]


# ═══════════════════════════════════════════════════════════════════════
# Цены (Bybit public, без ключей)
# ═══════════════════════════════════════════════════════════════════════

_ticker_cache: dict[str, dict] = {}
_ticker_ts: float = 0.0
_TICKER_TTL = 30.0  # сек


def _bulk_tickers(client: httpx.Client) -> dict[str, dict]:
    """Кэш тикеров Bybit на 30 сек."""
    global _ticker_ts
    now = time.time()
    if now - _ticker_ts < _TICKER_TTL:
        return _ticker_cache
    try:
        r = client.get(BYBIT_TICKERS, timeout=10)
        r.raise_for_status()
        data = r.json()
        list_data = (data.get("result") or {}).get("list") or []
        cache: dict[str, dict] = {}
        for t in list_data:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            cache[sym] = {
                "price": float(t.get("lastPrice", 0) or 0),
                "change": float(t.get("priceChangePercent", 0) or 0),
                "volume": float(t.get("volume24h") or t.get("turnover24h") or 0),
            }
        _ticker_cache.clear()
        _ticker_cache.update(cache)
        _ticker_ts = now
    except Exception as e:
        logger.warning(f"bulk tickers error: {e}")
    return _ticker_cache


def get_price(sym: str) -> float | None:
    sym = sym.upper().replace("-", "")
    if not sym.endswith("USDT"):
        sym += "USDT"
    with httpx.Client() as client:
        cache = _bulk_tickers(client)
    return cache.get(sym, {}).get("price")


def get_24h(sym: str) -> dict:
    sym = sym.upper().replace("-", "")
    if not sym.endswith("USDT"):
        sym += "USDT"
    with httpx.Client() as client:
        cache = _bulk_tickers(client)
    return cache.get(sym, {"change": 0.0, "volume": 0.0})


# ═══════════════════════════════════════════════════════════════════════
# Анализ сетапов
# ═══════════════════════════════════════════════════════════════════════

def _atr_1h(sym: str, client: httpx.Client) -> float:
    """Быстрая оценка ATR(14) на 1h."""
    try:
        r = client.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "spot", "symbol": sym, "interval": "60", "limit": 20},
            timeout=5,
        )
        rows = (r.json().get("result") or {}).get("list") or []
        if len(rows) < 15:
            return 0.0
        closes = [float(row[4]) for row in rows[-14:]]
        trs = []
        for i in range(1, len(closes)):
            tr = max(
                closes[i] - closes[i - 1],
                abs(closes[i] - closes[i - 1]),
                closes[i] - min(closes[:i + 1]),
            )
            trs.append(tr)
        if not trs:
            return 0.0
        return sum(trs) / len(trs)
    except Exception:
        return 0.0


def classify_regime(chg: float, vol: float) -> str:
    if chg <= -2.0 and vol > 1e7:
        return "DIP"
    if -6.0 < chg <= -2.0 and vol > 5e6:
        return "CORRECTION"
    if -1.5 <= chg <= 1.5 and vol > 3e6:
        return "ACCUMULATION"
    if chg > 6.0 and vol > 5e7:
        return "IMPULSE"
    return ""


def analyze(sym: str, price: float, change: float, volume: float) -> dict | None:
    """
    Классифицирует рыночный режим и строит карточку сетапа.
    Возвращает dict с полями: symbol, short, side, entry, tp, sl, rrr,
      sl_pct, tp_pct, hold, desc, chg, vol, cur.
    Возвращает None если нет сетапа.
    """
    regime = classify_regime(change, volume)
    if not regime:
        return None

    params = SETUP_PARAMS[regime]
    is_long = change < 0  # дип/коррекция → LONG; импульс → SHORT
    side = "LONG" if is_long else "SHORT"

    if is_long:
        entry = price * params["entry"]
        sl = price * params["sl"]
        tp = price * params["tp"]
    else:
        entry = price * (2 - params["entry"])  # mirror для шорта
        sl = price * (2 - params["sl"])
        tp = price * (2 - params["tp"])

    # R:R с учётом costs
    cost = entry * COST_PCT
    risk = abs(entry - sl) + cost
    reward = abs(tp - entry) - cost
    if risk <= 0:
        return None
    net_rr = reward / risk
    if net_rr < 1.2:
        return None

    # Классификация
    if net_rr >= 1.5:
        eligibility = "AUTO"
    elif net_rr >= 1.2:
        eligibility = "MANUAL"
    else:
        return None

    hold_map = {"DIP": "24ч", "CORRECTION": "3ч", "ACCUMULATION": "6ч", "IMPULSE": "2ч"}
    desc_map = {
        "DIP": "сильный откат — ловим дно",
        "CORRECTION": "умеренный откат — вход с докупом",
        "ACCUMULATION": "спокойный рост — накопление",
        "IMPULSE": "импульс — вход на коррекции",
    }

    ttl = params["ttl"]
    now = datetime.now(timezone.utc)
    expires_ts = (now.timestamp() + ttl * 60)

    return {
        "symbol": sym,
        "short": _sym_short(sym),
        "side": side,
        "entry": round(entry, 6),
        "tp": round(tp, 6),
        "sl": round(sl, 6),
        "rrr": round(net_rr, 2),
        "eligibility": eligibility,
        "sl_pct": round(abs(entry - sl) / entry * 100, 2),
        "tp_pct": round(abs(tp - entry) / entry * 100, 2),
        "hold": hold_map.get(regime, ""),
        "desc": desc_map.get(regime, ""),
        "chg": round(change, 1),
        "vol": round(volume, 0),
        "cur": round(price, 6),
        "regime": regime,
        "expires_ts": expires_ts,
        "ttl_min": ttl,
    }


# ═══════════════════════════════════════════════════════════════════════
# SMC-анализ (ликвидационные свипы, консолидация, ключевые уровни)
# ═══════════════════════════════════════════════════════════════════════

def _smc_summary(sym: str, client: httpx.Client) -> str:
    """Один абзац SMC-контекста для символа. Быстро, без исключений."""
    try:
        r = client.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "spot", "symbol": sym, "interval": "60", "limit": 50},
            timeout=5,
        )
        rows = (r.json().get("result") or {}).get("list") or []
        if len(rows) < 20:
            return ""
        # Найти ближайший round number
        last_close = float(rows[-1][4])
        for mult in [1, 10, 100, 1000]:
            rounded = round(last_close / mult) * mult
            if rounded == last_close:
                continue
            dist = abs(rounded - last_close) / last_close * 100
            if dist < 1.0:
                direction = "выше" if rounded > last_close else "ниже"
                return f"💡 Круглая цена {fmt_px(rounded)} {direction} на дистанции {dist:.1f}%"
        return ""
    except Exception:
        return ""


def _build_smc_section(candidates: list[str], client: httpx.Client) -> str:
    """Сборка SMC-секции — один абзац по BTC + хинты по кандидатным."""
    lines = []
    # Сначала BTC
    btc_hint = _smc_summary("BTCUSDT", client)
    if btc_hint:
        lines.append(btc_hint)
    # Затем кандидаты с сильными сигналами
    for sym in candidates[:5]:
        hint = _smc_summary(sym, client)
        if hint:
            lines.append(hint)
    if not lines:
        lines.append("Рынок стоит — жди пробития")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Сборка отчётов
# ═══════════════════════════════════════════════════════════════════════

def _btc_info(client: httpx.Client) -> str:
    """BTC price + 24h change для шапки."""
    cache = _bulk_tickers(client)
    btc = cache.get("BTCUSDT", {})
    price = btc.get("price", 0)
    chg = btc.get("change", 0)
    arrow = "▲" if chg >= 0 else "▼"
    return f"BTC ${fmt_px(price)} {arrow} {chg:+.1f}%"


def _format_pos_line(p: dict) -> str:
    side_ru = "LONG" if p["side"] == "LONG" else "SHORT"
    emoji = "🟢" if p["upnl"] >= 0 else "🔴"
    pnl_pct = (p["mark"] - p["entry"]) / p["entry"] * 100 if p["side"] == "LONG" else (p["entry"] - p["mark"]) / p["entry"] * 100
    liq_dist = abs(p["liq"] - p["mark"]) / p["mark"] * 100 if p["liq"] and p["mark"] else 0
    return (
        f"{emoji} *{p['symbol']}* {side_ru}  PnL *{p['upnl']:+.2f}*  "
        f"плюс *{pnl_pct:+.1f}%*  ликв. {liq_dist:.1f}%"
    )


def _format_order_line(o: dict, current_price: float) -> str:
    side_ru = "🟢" if o["side"].upper() == "BUY" else "🔴"
    dist = (current_price - o["price"]) / o["price"] * 100 if o["price"] else 0
    if dist <= 0:
        status = "✅ скоро"
    elif dist < 5:
        status = f"⏳ +{dist:.1f}%"
    else:
        status = f"📍 +{dist:.1f}%"
    return f"{side_ru} *{o['symbol']}* ${fmt_px(current_price)}  лимит ${fmt_px(o['price'])}  {status}"


def _fmt_setup(card: dict) -> str:
    side_ru = "ЛОНГ" if card["side"] == "LONG" else "ШОРТ"
    badge = "✅AUTO" if card["eligibility"] == "AUTO" else "✋MANUAL"
    dist = (card["entry"] - card["cur"]) / card["cur"] * 100 if card["cur"] else 0
    tv = _tv_url(card["symbol"])
    return (
        f"🟢 *{card['short']}* {side_ru} [{badge}]\n"
        f"  вход  *{fmt_px(card['entry'])}*\n"
        f"  цель  *{fmt_px(card['tp'])}*  (+{card['tp_pct']:.1f}%)\n"
        f"  стоп  *{fmt_px(card['sl'])}*  (-{card['sl_pct']:.1f}%)\n"
        f"  риск/прибыль 1:{card['rrr']}  ·  24ч {card['chg']:+.1f}%  ·  объём ${card['vol']/1e6:.0f}M\n"
        f"  причина: {card['desc']}\n"
        f"  {tv}"
    )


def build() -> str:
    """Полный hourly отчёт."""
    ak, as_ = _load_keys()
    with httpx.Client(timeout=15) as client:
        btc = _btc_info(client)
        # Позиции
        positions = bingx_positions(ak, as_) if ak and as_ else []
        # Ордеры
        orders = bingx_open_orders(ak, as_) if ak and as_ else []
        # Тикеры
        cache = _bulk_tickers(client)
        # Equity
        equity = 0.0
        free = 0.0
        if ak and as_:
            try:
                bal = bingx_balance(ak, as_)
                equity = bal.get("equity", 0)
                free = bal.get("free", 0)
            except Exception:
                pass
        total_pnl = sum(p["upnl"] for p in positions)

        lines = [
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}  💰 *BingX Отчёт*",
            "",
            f"📈 *{btc}* за 24ч",
            "",
            f"💼 *Баланс счёта*",
            f"   Equity: *${equity:.2f}* USDT",
            f"   Свободно: ${free:.2f}  ·  Занято в позициях: ${equity - free:.2f}",
            f"   Нереализованный PnL: *{total_pnl:+.4f} USDT*",
            "",
        ]

        if positions:
            lines.append("📊 *Открытые позиции:*")
            for p in positions:
                lines.append(f"   {_format_pos_line(p)}")
            lines.append("")

        ENTRY_TYPES = {"LIMIT", "STOP_LIMIT", "TAKE_PROFIT_LIMIT"}
        if orders:
            lines.append("📋 *Активные лимит-ордера:*")
            for o in orders:
                cur = cache.get(o["symbol"], {}).get("price", 0)
                lines.append(f"   {_format_order_line(o, cur)}")
            lines.append("")

        # Сетапы
        setups: list[dict] = []
        for sym in CANDIDATES:
            price = cache.get(sym, {}).get("price", 0)
            change = cache.get(sym, {}).get("change", 0)
            volume = cache.get(sym, {}).get("volume", 0)
            if not price or price <= 0:
                continue
            card = analyze(sym, price, change, volume)
            if card:
                setups.append(card)
        setups.sort(key=lambda c: -c["rrr"])

        if setups:
            lines.append("🎯 *Рекомендации к входу:*")
            for card in setups[:8]:
                lines.append("")
                lines.append(_fmt_setup(card))
            lines.append("")

        lines.append(f"📊 Итого PnL: {total_pnl:+.2f}U")
        lines.append("⏳ След. проверка через 1 час")

    msg = "\n".join(lines)
    logger.info(f"build done, {len(setups)} setups")
    return msg


def _build_position_report(positions: list[dict]) -> str:
    """Быстрый отчёт для fast-режима (positions + orders)."""
    ak, as_ = _load_keys()
    with httpx.Client(timeout=15) as client:
        cache = _bulk_tickers(client)
        btc = _btc_info(client)
        orders = bingx_open_orders(ak, as_) if ak and as_ else []

        equity = 0.0
        if ak and as_:
            try:
                equity = bingx_balance(ak, as_)["equity"]
            except Exception:
                pass
        total_pnl = sum(p["upnl"] for p in positions)

        lines = [
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}  💰 *BingX Отчёт*",
            "",
            f"📈 *{btc}*",
            "",
            f"💼 *Equity: ${equity:.2f}*  PnL нереал. *{total_pnl:+.4f}U*",
            "",
        ]

        shown: set[str] = set()
        if positions:
            lines.append("📊 *Позиции:*")
            for p in positions:
                sym = p["symbol"]
                if sym in shown:
                    continue
                shown.add(sym)
                lines.append(f"   {_format_pos_line(p)}")
            lines.append("")

        ENTRY_TYPES = {"LIMIT", "STOP_LIMIT", "TAKE_PROFIT_LIMIT"}
        orders_filtered = [o for o in orders if o.get("type", "") in ENTRY_TYPES]
        if orders_filtered:
            lines.append("📋 *Лимит-ордера:*")
            for o in orders_filtered:
                cur = cache.get(o["symbol"], {}).get("price", 0)
                lines.append(f"   {_format_order_line(o, cur)}")
            lines.append("")

        # Сетапы (только top-5 по R:R)
        setups: list[dict] = []
        for sym in CANDIDATES:
            price = cache.get(sym, {}).get("price", 0)
            change = cache.get(sym, {}).get("change", 0)
            volume = cache.get(sym, {}).get("volume", 0)
            if not price or price <= 0:
                continue
            card = analyze(sym, price, change, volume)
            if card:
                setups.append(card)
        setups.sort(key=lambda c: -c["rrr"])

        if setups:
            lines.append("🎯 *Сетапы:*")
            for card in setups[:5]:
                lines.append(f"   {_fmt_setup(card)}")
            lines.append("")

        lines.append(f"📊 Итого PnL: {total_pnl:+.2f}U")
        lines.append("⏳ След. через 5 мин")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Telegram
# ═══════════════════════════════════════════════════════════════════════

TG_URL = "https://api.telegram.org/bot{token}/sendMessage"
TG_IP = "149.154.167.220"


def send_tg(text: str) -> bool:
    """Отправить сообщение в Telegram через curl с IP-resolve."""
    safe = text.replace("&", "&amp;")
    # Восстанавливаем HTML-теги после экранирования
    for tag in ("b", "code", "i", "u", "s", "inlineurl"):
        safe = safe.replace(f"&lt;{tag}&gt;", f"<{tag}>").replace(f"&lt;/{tag}&gt;", f"</{tag}>")
        safe = safe.replace(f"&lt;{tag} ", f"<{tag} ")
    payload = json.dumps({
        "chat_id": TELEGRAM_CHAT,
        "text": safe,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }, ensure_ascii=False)
    for attempt in range(3):
        try:
            import subprocess
            proc = subprocess.run(
                ["curl", "-s", "--max-time", "15",
                 "--resolve", f"api.telegram.org:443:{TG_IP}",
                 "-X", "POST", TG_URL.format(token=TELEGRAM_TOKEN),
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, timeout=20,
            )
            resp = proc.stdout.decode()
            if '"ok":true' in resp:
                return True
            logger.warning(f"TG attempt {attempt+1} failed: {resp[:200]}")
        except Exception as e:
            logger.warning(f"TG attempt {attempt+1} error: {e}")
        time.sleep(1.5)
    return False


# ═══════════════════════════════════════════════════════════════════════
# Dедупликация (fast mode)
# ═══════════════════════════════════════════════════════════════════════

def _fingerprint(msg: str) -> str:
    return hashlib.md5(msg.encode()).hexdigest()[:8]


def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception:
        pass
    return {}


def _save_state(st: dict) -> None:
    STATE_PATH.write_text(json.dumps(st, ensure_ascii=False))


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def _log_msg(path: Path, msg: str) -> None:
    try:
        with open(path, "a") as f:
            f.write(msg + "\n")
    except Exception as e:
        logger.error(f"log write error: {e}")


def main() -> None:
    fast = "--fast" in sys.argv

    try:
        if fast:
            msg = build()  # всё ещё build(), но внутри есть оптимизации
            # Fast режим: компактный report только позиций и ордеров
            ak, as_ = _load_keys()
            positions = bingx_positions(ak, as_) if ak and as_ else []
            msg = _build_position_report(positions)

            fp = _fingerprint(msg)
            st = _load_state()
            prev_fp = st.get("fingerprint", "")

            if fp == prev_fp:
                logger.info(f"fast: fingerprint unchanged ({fp}), skipping")
                _log_msg(FAST_LOG, f"fast: fingerprint unchanged ({fp}), skipping")
                return

            ok = send_tg(msg)
            st["fingerprint"] = fp
            st["last_ts"] = datetime.now(timezone.utc).isoformat()
            _save_state(st)

            if ok:
                logger.info(f"fast: sending ({fp})")
                _log_msg(FAST_LOG, f"fast: sending ({fp})")
                _log_msg(FAST_LOG, msg)
            else:
                logger.error(f"fast: TG failed ({fp})")
                _log_msg(FAST_LOG, f"fast: TG failed ({fp})")
        else:
            msg = build()
            logger.info("build full report")
            ok = send_tg(msg)
            _log_msg(LOG_PATH, msg)
            _log_msg(LOG_PATH, f"sent: {'ok' if ok else 'FAILED'}")
            if ok:
                logger.info("report sent successfully")
            else:
                logger.error("TG send failed after retries")
    except Exception as e:
        logger.error(f"main error: {e}", exc_info=True)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
