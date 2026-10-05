#!/usr/bin/env python3
"""
price_monitor.py — мониторинг рынка и счёта TradingOS.

Режимы:
  python3 price_monitor.py              # полный отчёт (hourly)
  python3 price_monitor.py --fast       # лёгкий отчёт (dedup, каждые 5 мин)

Интеграция:
  Сетапы берутся из TradingOS scanner (manual_scanner.scan_all()) → контур
  MARKET / LIMIT → показываются. NO_TRADE → пропускаются.
  Fallback: простая ATR-аналитика (rrr >= 1.5), если scanner не нашёл ничего.

Интерфейс для setup_cards.py:
  pm.CANDIDATES          — список символов для сканирования
  pm.get_price(sym)      — текущая цена (Binance public)
  pm.get_24h(sym)        — 24h change + volume
  pm.analyze(sym, price, change, volume) -> dict | None  (fallback)
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

sys.path.insert(0, "/root")
sys.path.insert(0, "/root/tradingos")
sys.path.insert(0, "/root/trading_brain_v4")

# ── Конфиг ─────────────────────────────────────────────────────────────
BINGX_BASE = "https://open-api.bingx.com"
BINGX_ENV = Path("/root/tradingos/operations/.bingx.env")
BINANCE_TICKERS = "https://api.binance.com/api/v3/ticker/24hr"
BYBIT_TICKERS_PERP = "https://api.bybit.com/v5/market/tickers?category=linear"
BYBIT_KLINE = "https://api.bybit.com/v5/market/kline"

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "8758713317:AAHExP0TX94dy6xWxVeILh_dDqs5eYMWgyg",
)
TELEGRAM_CHAT = int(os.getenv("TELEGRAM_CHAT_ID", "977966870"))

STATE_PATH = Path("/root/tradingos/operations/price_monitor_state.json")
LOG_PATH = Path("/root/tradingos/research/price_monitor.log")
FAST_LOG = Path("/root/tradingos/research/price_monitor_fast.log")

CANDIDATES: list[str] = sorted({
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT",
    "LINKUSDT", "ADAUSDT", "AVAXUSDT", "LTCUSDT", "DOTUSDT", "UNIUSDT",
    "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT", "SUIUSDT", "TRXUSDT",
    "RUNEUSDT", "INJUSDT", "RNDRUSDT", "FETUSDT", "SANDUSDT",
    "AXSUSDT", "ZECUSDT", "WLDUSDT", "ONDOUSDT", "STRKUSDT",
})

logger = logging.getLogger("price_monitor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")


# ═══════════════════════════════════════════════════════════════════════
# Утилиты
# ═══════════════════════════════════════════════════════════════════════

def _sym_short(sym: str) -> str:
    return sym.replace("USDT", "")


def fmt_px(v: float) -> str:
    v = float(v)
    if abs(v) >= 1000:
        return f"{v:,.2f}"
    if abs(v) >= 1:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    if abs(v) >= 0.01:
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _tv_url(sym: str) -> str:
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


def _bx_get(path: str, params: dict, ak: str, as_: str) -> dict:
    ts = str(int(time.time() * 1000))
    params = {**params, "timestamp": ts}
    qs_raw = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac.new(as_.encode(), qs_raw.encode(), hashlib.sha256).hexdigest()
    qs = urllib.parse.urlencode(params)
    url = f"{BINGX_BASE}{path}?{qs}&signature={sig}"
    r = httpx.get(url, headers={"X-BX-APIKEY": ak}, timeout=10)
    r.raise_for_status()
    data = r.json()
    if data.get("code", 0) != 0:
        raise RuntimeError(f"BingX error {data.get('code')}: {data.get('msg')}")
    return data.get("data", data)


def bingx_balance(ak: str, as_: str) -> dict:
    raw = _bx_get("/openApi/swap/v2/user/balance", {"asset": "USDT"}, ak, as_)
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
# Цены (Binance public — точные % изменения)
# ═══════════════════════════════════════════════════════════════════════

_ticker_cache: dict[str, dict] = {}
_ticker_ts: float = 0.0
_TICKER_TTL = 30.0

_atr_cache: dict[str, float] = {}
_atr_ts: float = 0.0
_ATR_TTL = 60.0


def _get_atr(sym: str, client: httpx.Client) -> float:
    global _atr_ts
    now = time.time()
    if sym in _atr_cache and now - _atr_ts < _ATR_TTL:
        return _atr_cache[sym]
    for category in ("spot", "linear"):
        try:
            r = client.get(BYBIT_KLINE, params={
                "category": category, "symbol": sym, "interval": "60", "limit": 20
            }, timeout=5)
            rows = (r.json().get("result") or {}).get("list") or []
            if len(rows) >= 15:
                recent = rows[-14:]
                closes = [float(c[4]) for c in recent]
                trs = []
                for j in range(1, len(recent)):
                    tr = max(
                        abs(closes[j] - closes[j - 1]),
                        abs(closes[j] - float(recent[j - 1][3])),
                        abs(closes[j] - float(recent[j - 1][2])),
                    )
                    trs.append(tr)
                if trs:
                    atr = sum(trs) / len(trs)
                    _atr_cache[sym] = atr
                    _atr_ts = now
                    return atr
        except Exception:
            continue
    return 0.0


def _bulk_tickers(client: httpx.Client) -> dict[str, dict]:
    global _ticker_ts
    now = time.time()
    if now - _ticker_ts < _TICKER_TTL:
        return _ticker_cache
    try:
        r = client.get(BINANCE_TICKERS, timeout=10)
        r.raise_for_status()
        list_data = r.json()
        cache: dict[str, dict] = {}
        for t in list_data:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            try:
                pct = float(t.get("priceChangePercent", 0) or 0)
            except (ValueError, TypeError):
                pct = 0.0
            cache[sym] = {
                "price": float(t.get("lastPrice", 0) or 0),
                "change": pct,
                "volume": float(t.get("quoteVolume", 0) or 0),
            }
        if cache:
            _ticker_cache.clear()
            _ticker_cache.update(cache)
            _ticker_ts = now
            return _ticker_cache
    except Exception:
        pass

    try:
        r = client.get(BYBIT_TICKERS_PERP, timeout=10)
        r.raise_for_status()
        data = r.json()
        list_data = (data.get("result") or {}).get("list") or []
        cache: dict[str, dict] = {}
        for t in list_data:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            pct_str = t.get("price24hPcnt", "0")
            try:
                pct = float(pct_str) * 100
            except (ValueError, TypeError):
                pct = 0.0
            cache[sym] = {
                "price": float(t.get("lastPrice", 0) or 0),
                "change": pct,
                "volume": float(t.get("volume24h") or 0),
            }
        if cache:
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
# Сканирование с мягкими порогами (для price_monitor)
# ═══════════════════════════════════════════════════════════════════════

def _scan_for_monitor() -> list[dict]:
    """Сканирует CANDIDATES с мягкими порогами и фильтрует до хороших сигналов."""
    from tradingos.signals.manual_scanner import score_symbol
    import httpx

    # Мягкие пороги: ниже ликвидность и ниже score
    low_notional = 50_000   # $50k вместо $200k
    low_score = 55           # вместо 70

    good: list[dict] = []
    with httpx.Client(timeout=20) as client:
        for sym in CANDIDATES:
            try:
                sig = score_symbol(client, sym, min_score=low_score)
                if not sig:
                    continue
                contour = sig.get("contour", "NO_TRADE")
                decision = sig.get("trade_decision", "SKIP")
                skip = sig.get("skip_reason", "")

                # Принимаем: MARKET / LIMIT — всегда
                # NO_TRADE + ALLOW — только если rr >= 1.5 и не блокирован MTF/stoch
                accept = False
                if contour in ("MARKET", "LIMIT"):
                    accept = True
                elif decision == "ALLOW" and skip not in ("MTF_CONFLICT", "STOCH_ZONE_CONFLICT"):
                    rr = sig.get("rr", 0)
                    if rr >= 1.5:
                        accept = True

                if accept:
                    good.append(sig)
            except Exception:
                continue

    # Сортируем: MARKET > LIMIT > ALLOW, по score desc
    contour_order = {"MARKET": 0, "LIMIT": 1, "NO_TRADE": 2}
    good.sort(key=lambda s: (contour_order.get(s.get("contour", "NO_TRADE"), 2), -s.get("score", 0)))
    return good[:3]  # Макс 3

_REGIME_FROM_DESC = {
    "накопление": "ACCUMULATION",
    "дип-откат": "DIP",
    "коррекция": "CORRECTION",
    "импульс": "IMPULSE",
    "всплеск": "IMPULSE",
}
_TTL_FROM_DESC = {
    "ACCUMULATION": 360,
    "DIP": 240,
    "CORRECTION": 180,
    "IMPULSE": 120,
}
_SIDE_FROM_DESC = {
    "Дип-откат": "LONG", "дип-откат": "LONG",
    "Коррекция": "LONG", "коррекция": "LONG",
    "Накопление": "LONG", "накопление": "LONG",
    "Импульс": "SHORT", "импульс": "SHORT",
    "Всплеск": "SHORT", "всплеск": "SHORT",
}


def _desc_from_score(sig: dict) -> str:
    """Описание режима из score-компонентов."""
    parts = sig.get("parts", {})
    h1 = parts.get("h1_trend", 0)
    mom = parts.get("momentum", 0)
    chg = sig.get("chg", 0)
    if chg <= -2:
        return "дип-откат — ловим дно"
    if chg <= -0.5:
        return "коррекция — вход на восстановлении"
    if chg >= 6:
        return "всплеск — шортим перекупленность"
    if chg > 0:
        return "накопление — спокойный рост"
    return "коррекция — вход на восстановлении"


def _scanner_to_card(sig: dict, equity: float, ticker_cache: dict) -> dict | None:
    """Конвертирует сигнал сканера в карточку для price_monitor."""
    sym = sig["symbol"]
    price = sig.get("price", 0)
    if not price:
        return None

    side = sig.get("side", "LONG")
    sl = sig.get("sl", 0)
    tp = sig.get("final_tp", sig.get("raw_tp", 0))
    atr = sig.get("atr", 0)
    rr = sig.get("rr", 0)
    contour = sig.get("contour", "NO_TRADE")
    trade_decision = sig.get("trade_decision", "SKIP")
    skip_reason = sig.get("skip_reason", "")
    dist_e20 = sig.get("dist_e20_pct", 0)
    vol_ratio = sig.get("vol_ratio", 0)

    # Filter: only show MARKET and LIMIT contours, or ALLOW with NO_TRADE but good RR
    if contour == "NO_TRADE" and trade_decision == "SKIP":
        return None
    if contour == "NO_TRADE" and skip_reason in ("MTF_CONFLICT", "STOCH_ZONE_CONFLICT"):
        return None

    # Calculate entry range from ATR
    if atr <= 0:
        return None

    chg = ticker_cache.get(sym, {}).get("change", 0)
    vol = ticker_cache.get(sym, {}).get("volume", 0)

    # Entry zone: use wait_limit_entry if LIMIT, else current-price-adjacent
    wait_entry = sig.get("wait_limit_entry", 0)
    if wait_entry and wait_entry > 0:
        entry_low = wait_entry
        entry_high = wait_entry + atr * 0.3
    elif side == "LONG":
        entry_low = price - atr * 0.8
        entry_high = price + atr * 0.2
    else:
        entry_high = price + atr * 0.8
        entry_low = entry_high - atr * 0.3

    if entry_low <= 0 or entry_high <= 0 or sl <= 0 or tp <= 0:
        return None

    cost = entry_low * 0.002
    risk = abs(entry_low - sl) + cost
    reward = abs(tp - entry_low) - cost
    net_rr = reward / risk if risk > 0 else 0
    if net_rr < 1.2:
        return None

    desc = _desc_from_score(sig)
    regime = _REGIME_FROM_DESC.get(desc.split("—")[0].strip(), "ACCUMULATION")
    ttl = _TTL_FROM_DESC.get(regime, 180)
    now = datetime.now(timezone.utc)
    expires_ts = now.timestamp() + ttl * 60

    # Trailing TP
    if side == "LONG":
        trailing_tp = tp + atr
    else:
        trailing_tp = tp - atr

    # Distance to entry
    if price < entry_low:
        dist = (price - entry_low) / entry_low * 100
    else:
        dist = (price - entry_low) / entry_low * 100

    # SMC hint
    smc_hint = _smc_one_line(sym, price, atr)

    notional = int(equity * 0.1) if equity else 21

    return {
        "symbol": sym,
        "short": _sym_short(sym),
        "side": side,
        "entry_low": round(entry_low, 6),
        "entry_high": round(entry_high, 6),
        "tp": round(tp, 6),
        "sl": round(sl, 6),
        "rrr": round(net_rr, 2),
        "sl_pct": round(abs(entry_low - sl) / entry_low * 100, 1),
        "tp_pct": round(abs(tp - entry_low) / entry_low * 100, 1),
        "hold": f"{ttl // 60}ч" if ttl >= 60 else f"{ttl}мин",
        "desc": desc,
        "chg": round(chg, 1),
        "vol": round(vol, 0),
        "cur": round(price, 6),
        "atr": round(atr, 6),
        "atr_pct": round(atr / price * 100, 1) if price else 0,
        "regime": regime,
        "expires_ts": expires_ts,
        "ttl_min": ttl,
        "trailing_tp": round(trailing_tp, 6),
        "smc_hint": smc_hint,
        "dist_to_entry": round(dist, 1),
        "score": sig.get("score", 0),
        "contour": contour,
        "contour_reasoning": sig.get("contour_reasoning", []),
        "notional": notional,
    }


# ═══════════════════════════════════════════════════════════════════════
# Простая ATR-аналитика (fallback если scanner пустой)
# ═══════════════════════════════════════════════════════════════════════

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


_REGIME_SIDE = {"DIP": "LONG", "CORRECTION": "LONG", "ACCUMULATION": "LONG", "IMPULSE": "SHORT"}
_REGIME_DESC = {
    "DIP": "дип-откат — ловим дно",
    "CORRECTION": "коррекция — вход на восстановлении",
    "ACCUMULATION": "накопление — спокойный рост",
    "IMPULSE": "импульс — вход на коррекции",
}
_REGIME_TTL = {"DIP": 240, "CORRECTION": 180, "ACCUMULATION": 360, "IMPULSE": 120}


def analyze(sym: str, price: float, change: float, volume: float) -> dict | None:
    """Fallback analysis — только при rrr >= 1.5."""
    regime = classify_regime(change, volume)
    if not regime:
        return None

    side = _REGIME_SIDE[regime]
    now = datetime.now(timezone.utc)
    ttl = _REGIME_TTL[regime]
    expires_ts = now.timestamp() + ttl * 60

    atr = _get_atr(sym, httpx.Client(timeout=5))
    if atr <= 0:
        return None

    atr_pct = atr / price * 100 if price else 0

    if side == "LONG":
        entry_low = price - atr * 1.0
        entry_high = entry_low + atr * 0.5
        sl = entry_low - atr * 2.0
        tp = entry_low + atr * 5.0
    else:
        entry_high = price + atr * 1.0
        entry_low = entry_high - atr * 0.5
        sl = entry_high + atr * 2.0
        tp = entry_high - atr * 5.0

    if sl <= 0 or tp <= 0 or entry_low <= 0 or entry_high <= 0:
        return None

    cost = entry_low * 0.002
    risk = abs(entry_low - sl) + cost
    reward = abs(tp - entry_low) - cost
    if risk <= 0:
        return None
    net_rr = reward / risk
    if net_rr < 1.5:  # Стриктнее чем у scanner — только хорошие
        return None

    if side == "LONG":
        trailing_tp = tp + atr
    else:
        trailing_tp = tp - atr

    dist = (price - entry_low) / entry_low * 100
    smc_hint = _smc_one_line(sym, price, atr)

    return {
        "symbol": sym,
        "short": _sym_short(sym),
        "side": side,
        "entry_low": round(entry_low, 6),
        "entry_high": round(entry_high, 6),
        "tp": round(tp, 6),
        "sl": round(sl, 6),
        "rrr": round(net_rr, 2),
        "sl_pct": round(abs(entry_low - sl) / entry_low * 100, 1),
        "tp_pct": round(abs(tp - entry_low) / entry_low * 100, 1),
        "hold": f"{ttl // 60}ч" if ttl >= 360 else f"{ttl}мин",
        "desc": _REGIME_DESC[regime],
        "chg": round(change, 1),
        "vol": round(volume, 0),
        "cur": round(price, 6),
        "atr": round(atr, 6),
        "atr_pct": round(atr_pct, 1),
        "regime": regime,
        "expires_ts": expires_ts,
        "ttl_min": ttl,
        "trailing_tp": round(trailing_tp, 6),
        "smc_hint": smc_hint,
        "dist_to_entry": round(dist, 1),
        "score": 0,
        "contour": "FALLBACK",
        "contour_reasoning": [],
        "notional": 0,
    }


def _smc_one_line(sym: str, price: float, atr: float) -> str:
    candidates = []
    for mult in [1, 10, 100, 1000, 10000]:
        rounded = round(price / mult) * mult
        if rounded == price:
            continue
        dist = abs(rounded - price) / price * 100
        if dist < 1.5:
            candidates.append((dist, rounded, "круглая цена"))

    for offset_mult in [0.3, 0.5, 0.8, 1.0, 1.5, 2.0]:
        lvl = price * (1 - offset_mult * atr / price) if atr else 0
        if lvl <= 0:
            continue
        dist = abs(lvl - price) / price * 100
        if 0.3 < dist < 3.0:
            label = "поддержка" if lvl < price else "сопротивление"
            candidates.append((dist, lvl, label))

    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    dist, lvl, label = candidates[0]
    near = "рядом" if dist < 0.5 else f"на дистанции {dist:.1f}%"
    if label == "круглая цена":
        return f"Круглая цена {fmt_px(lvl)} {near} — рынок на распутье"
    elif lvl < price:
        return f"Поддержка {fmt_px(lvl)} держит снизу"
    else:
        return f"Сопротивление {fmt_px(lvl)} прямо перед ценой"


def _smc_section(ticker_cache: dict) -> str:
    parts = []
    for sym in ["BTCUSDT", "ETHUSDT"]:
        price = ticker_cache.get(sym, {}).get("price", 0)
        if not price:
            continue
        with httpx.Client(timeout=5) as c:
            atr = _get_atr(sym, c)
        if atr <= 0:
            continue
        if atr / price * 100 < 0.5:
            parts.append(sym.replace("USDT", ""))
    if parts:
        return f"⚡ Рынок стоит ({', '.join(parts)}) — жди пробития, новых входов пока нет"
    return ""


# ═══════════════════════════════════════════════════════════════════════
# Сборка отчёта
# ═══════════════════════════════════════════════════════════════════════

def _btc_line(client: httpx.Client) -> str:
    cache = _bulk_tickers(client)
    btc = cache.get("BTCUSDT", {})
    price = btc.get("price", 0)
    chg = btc.get("change", 0)
    arrow = "🟢▲" if chg >= 0 else "🔴▼"
    return f"BTC {fmt_px(price)} {arrow} {chg:+.1f}%"


def _now_msk() -> str:
    try:
        import zoneinfo
        return datetime.now(zoneinfo.ZoneInfo("Europe/Moscow")).strftime("%H:%M")
    except Exception:
        return datetime.now(timezone.utc).astimezone().strftime("%H:%M")


def _pos_lines(p: dict, equity: float) -> list[str]:
    sym = p["symbol"]
    side = p["side"]
    lev = p["leverage"]
    entry = p["entry"]
    mark = p["mark"]
    upnl = p["upnl"]
    liq = p["liq"]
    pnl_pct = (mark - entry) / entry * 100 if side == "LONG" else (entry - mark) / entry * 100
    liq_dist = abs(liq - mark) / mark * 100 if liq and mark else 0
    pnl_emoji = "🟢" if upnl >= 0 else "🔴"
    liq_emoji = "🟢" if liq_dist > 50 else "🟡" if liq_dist > 20 else "🔴"
    return [
        f"▶ <code>{sym}</code> {side} x{lev}  <code>{fmt_px(entry)}</code>→<code>{fmt_px(mark)}</code>",
        f"   PnL {pnl_emoji}<code>${upnl:+.2f}</code> ({pnl_pct:+.1f}%)  ·  ликв {liq_emoji}<code>{liq_dist:.0f}%</code>",
    ]


def _order_line(o: dict, cur: float) -> str:
    sym = o["symbol"]
    side = o["side"].upper()
    price = o["price"]
    if not price or not cur:
        return f"   {sym} 📥 лимит <code>{fmt_px(price)}</code> ❓"
    side_icon = "📥" if side == "BUY" else "📤"
    tv = _tv_url(sym)
    if side == "BUY":
        dist = (cur - price) / price * 100
        icon = "✅" if dist <= 0 else "❌"
    else:
        dist = (price - cur) / price * 100
        icon = "✅" if dist <= 0 else "❌"
    return f"   {sym} {side_icon} лимит <code>{fmt_px(price)}</code>  <a href=\"{tv}\">📊</a> {icon}"


def _setup_line(c: dict) -> str:
    sym = c["short"]
    side_ru = "ЛОНГ" if c["side"] == "LONG" else "ШОРТ"
    side_emoji = "🟢" if c["side"] == "LONG" else "🔴"
    tv = _tv_url(c["symbol"])
    dist = c["dist_to_entry"]
    dist_emoji = "🟢" if dist <= 0.2 else ("🟡" if dist <= 1.0 else "⚪")

    elapsed = time.time() - (c["expires_ts"] - c["ttl_min"] * 60)
    remaining = max(0, int(c["ttl_min"] - elapsed / 60))

    lines = [
        f"{side_emoji} <b><code>{sym}</code></b> {side_ru}  ·  {c['desc']}",
        f"🔵 Вход:  <code>{fmt_px(c['entry_low'])}</code>–<code>{fmt_px(c['entry_high'])}</code>",
        f"🟢 Тейк: <code>{fmt_px(c['tp'])}</code>  (+{c['tp_pct']}%)",
        f"🔴 Стоп: <code>{fmt_px(c['sl'])}</code>  (-{c['sl_pct']}%)",
        f"⚙️ Плечо: 20  ·  Размер: ${c.get('notional', 21)}(10%)",
        f"📍 до цены входа {dist_emoji}<b>{dist:+.1f}%</b>  ·  24ч <b>{c['chg']:+.1f}%</b>",
        f"📊 Волатильность {c['atr_pct']}% за час",
        "   Рынок затаился — жди резкого движения",
        f"   Трейлинг: если растёт → тейк поднимется до {fmt_px(c['trailing_tp'])}",
        f"⏳ Активен ещё {remaining} мин  ·  <a href=\"{tv}\">📊</a>",
        "   ⚠️ Это идея, а не сигнал — жди подхода цены",
    ]
    if c.get("smc_hint"):
        lines.append(f"   {c['smc_hint']}")
    return "\n".join(lines)


def _collect_setups(
    scanner_sigs: list[dict],
    ticker_cache: dict,
    equity: float,
) -> list[dict]:
    """Собираем карточки из scanner + fallback."""
    cards: list[dict] = []

    for sig in scanner_sigs:
        card = _scanner_to_card(sig, equity, ticker_cache)
        if card:
            cards.append(card)

    # Fallback: если scanner не дал результатов — простая ATR-аналитика
    if not cards:
        for sym in CANDIDATES:
            t = ticker_cache.get(sym, {})
            p, chg, vol = t.get("price", 0), t.get("change", 0), t.get("volume", 0)
            if not p or p <= 0:
                continue
            card = analyze(sym, p, chg, vol)
            if card:
                cards.append(card)

    cards.sort(key=lambda c: (-c.get("score", c["rrr"]), -c["rrr"]))
    return cards


def _build_report(positions: list[dict], equity: float, free: float,
                  scanner_sigs: list[dict], next_check_min: int) -> str:
    ak, as_ = _load_keys()
    with httpx.Client(timeout=15) as client:
        btc = _btc_line(client)
        cache = _bulk_tickers(client)
        orders = bingx_open_orders(ak, as_) if ak and as_ else []

        total_pnl = sum(p["upnl"] for p in positions)
        unrealised = equity - free if equity > 0 else 0
        risk_usd = equity * 0.01 if equity else 0
        pnl_emoji = "🟢" if total_pnl >= 0 else "🔴"
        equity_emoji = "🟢" if unrealised >= 0 else "🔴"
        now_msk = _now_msk()

        lines = [
            f"⏰ <b>{now_msk} MSK</b>  ·  <b>{btc}</b>",
            "",
            f"💰 <b>Баланс:</b> ${fmt_px(equity)}  ·  свободно ${fmt_px(free)}  ·  {equity_emoji}<b>${unrealised:+.2f}</b>",
            f"⚠️ <b>Риск:</b> 1% = ${fmt_px(risk_usd)}  ·  ⏱ через <b>{next_check_min} мин</b>",
            "",
        ]

        # Positions
        lines.append(f"📂 <b>Позиции ({len(positions)})</b>")
        for p in positions:
            for pline in _pos_lines(p, equity):
                lines.append(pline)
        lines.append("")

        # Orders
        ENTRY_TYPES = {"LIMIT", "STOP_LIMIT", "TAKE_PROFIT_LIMIT"}
        entry_orders = [o for o in orders if o.get("type", "").upper() in ENTRY_TYPES]
        if entry_orders:
            lines.append(f"📋 <b>Ордера ({len(entry_orders)})</b>")
            for o in entry_orders:
                cur = cache.get(o["symbol"], {}).get("price", 0)
                lines.append(_order_line(o, cur))
            lines.append("")

        # Setups from scanner + fallback
        setups = _collect_setups(scanner_sigs, cache, equity)
        if setups:
            lines.append("🎯 <b>СЕТАПЫ ДЛЯ ВХОДА</b>")
            lines.append("")
            for c in setups[:8]:
                lines.append(_setup_line(c))
                lines.append("")

        # SMC bottom
        smc = _smc_section(cache)
        if smc:
            lines.append(f"  {smc}")
            lines.append("")

        lines.append(f"{pnl_emoji} <b>Итого PnL ${total_pnl:+.2f}</b>  ·  ⏱ следующая проверка через <b>{next_check_min} мин</b>")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════
# Main
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


def _log_msg(path: Path, msg: str) -> None:
    try:
        with open(path, "a") as f:
            f.write(msg + "\n")
    except Exception as e:
        logger.error(f"log write error: {e}")


def send_tg(text: str) -> bool:
    safe = text.replace("&", "&amp;")
    for tag in ("b", "code", "i", "u", "s", "a", "blockquote"):
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
                 "--resolve", f"api.telegram.org:443:149.154.167.220",
                 "-X", "POST", f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                 "-H", "Content-Type: application/json",
                 "-d", payload],
                capture_output=True, timeout=20,
            )
            if '"ok":true' in proc.stdout.decode():
                return True
            logger.warning(f"TG attempt {attempt+1} failed: {proc.stdout.decode()[:200]}")
        except Exception as e:
            logger.warning(f"TG attempt {attempt+1} error: {e}")
        time.sleep(1.5)
    return False


def main() -> None:
    fast = "--fast" in sys.argv
    try:
        ak, as_ = _load_keys()
        with httpx.Client(timeout=15) as client:
            # Scanner
            try:
                scanner_sigs = _scan_for_monitor()
                logger.info(f"scanner: {len(scanner_sigs)} good signals")
            except Exception as e:
                logger.warning(f"scanner error: {e}")
                scanner_sigs = []

            # Account
            positions = bingx_positions(ak, as_) if ak and as_ else []
            equity = 0.0
            free = 0.0
            if ak and as_:
                try:
                    bal = bingx_balance(ak, as_)
                    equity = bal.get("equity", 0)
                    free = bal.get("free", 0)
                except Exception:
                    pass

            next_check = 5 if fast else 5
            msg = _build_report(positions, equity, free, scanner_sigs, next_check)

        if fast:
            fp = _fingerprint(msg)
            st = _load_state()
            if fp == st.get("fingerprint", ""):
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
        else:
            logger.info("build full report")
            ok = send_tg(msg)
            _log_msg(LOG_PATH, msg)
            _log_msg(LOG_PATH, f"sent: {'ok' if ok else 'FAILED'}")
            if ok:
                logger.info("report sent")
            else:
                logger.error("TG send failed")
    except Exception as e:
        logger.error(f"main error: {e}", exc_info=True)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
