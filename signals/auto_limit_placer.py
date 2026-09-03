"""
auto_limit_placer.py — Автоматический постановщик LIMIT-ордеров на откате.

Стратегия: 2 уровня входа (L1/L2) для каждого сигнала.
  L1: entry ± ATR×0.3 (лёгкий откат, частый филл)
  L2: entry ± ATR×0.6 (глубокий откат, реже филл, лучше R:R)

Поток:
  1. Scan → сигнал ≥70, R:R≥1.0
  2. Place L1 + L2 PostOnly LIMIT на Bybit DEMO
  3. Monitor → если L1 filled → cancel L2, attach SL/TP
  4. Expire → через 2ч отмена, если не филл
  5. Cancel → если цена ушла >0.5% от зоны без филла
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys_path = ["/root", "/root/tradingos"]
for _p in sys_path:
    if _p not in __import__("sys").path:
        __import__("sys").path.insert(0, _p)

from tradingos.signals.manual_scanner import score_symbol, load_config

ROOT = Path("/root/tradingos")
STATE_FILE = ROOT / "operations/auto_limit_state.json"
JOURNAL = ROOT / "memory/auto_limit_signals.jsonl"

# ─── Config ──────────────────────────────────────────────────────────
SCAN_INTERVAL_S = 300          # 5 min between scans
MAX_POSITIONS = 4             # max concurrent symbols with active limits
OWNER_BET_EXPIRY_H = 24       # owner bets (bet_wizard) live 24h, not 2h — structural entries wait longer
RISK_PER_TRADE_USD = 25.0     # risk $ per signal (from trading_mode)
MIN_RR = 1.0                   # min R:R for limit entry
L1_ATR_MULT = 0.3              # L1 offset from signal price
L2_ATR_MULT = 0.6              # L2 offset from signal price
EXPIRY_MIN = 120               # cancel unfilled after 2h
CANCEL_THRESHOLD_PCT = 0.5     # cancel if price moved >0.5% away
# FIX 2026-09-03 (ночная посадка -$99 за 2ч): 3 коррелированных SHORT
# (XRP/DOGE/SOL) стопнулись одновременно при market-wide пампе. Без
# same-side капа лимитки собирают 3-4× экспоуз в одно направление.
MAX_SAME_SIDE = 2              # max SHORT-or-LONG concurrently (limits+positions)
MAX_PORTFOLIO_RISK_USD = 75.0  # суммарный открытый риск ≤ 3×$25 (limits+positions)
# Ночная статистика 02-03.09: BE на +1R при SL 1.5-1.7% = защита приходит
# слишком поздно для мемкоинов (ATR 2%/час). Ранний BE: +0.7R, частичка 30%.
BE_TRIGGER_R = 0.7             # move SL to breakeven at +0.7R (было 1.0)
PARTIAL_CLOSE_PCT = 30         # close 30% at BE trigger (было 50%)
# Adaptive exits (owner FARTCOIN-инцидент): статический TP=4×ATR недостижим
# для волатильных мемкоинов (8%+ за 4h hold).
ADAPTIVE_EXITS_ENABLED = True
TP_ATR_MULT = 4.0              # static TP multiplier (unchanged)


def get_position_pnl_pct(symbol: str, side: str) -> tuple[float, float, float]:
    """Return (uPnL_pct, entry, current) for an open position."""
    res = _signed_get("/v5/position/list", f"category=linear&symbol={symbol}")
    if res.get("retCode") != 0:
        return 0.0, 0.0, 0.0
    for p in res.get("result", {}).get("list", []):
        if float(p.get("size", 0)) != 0:
            entry = float(p.get("avgPrice", 0))
            cur = get_current_price(symbol)
            if entry <= 0 or cur <= 0:
                return 0.0, entry, cur
            pnl_pct = (cur - entry) / entry * 100 if side in ("LONG", "BUY") \
                else (entry - cur) / entry * 100
            return pnl_pct, entry, cur
    return 0.0, 0.0, 0.0


def apply_adaptive_exit(sym: str, side: str, entry: float, sl: float,
                        tp: float, qty: float) -> None:
    """Smart exit management after fill:
    +0.7R → закрываем 30% (partial) и переставляем SL в безубыток.
    Вызывается из monitor_fills после установки базовых SL/TP.
    FIX 2026-09-03: BE на +1R слишком поздно для мемкоинов (ATR 2%/час,
    SL 1.5-1.7% = цена успевает развернуться до защиты). Ранний BE 0.7R."""
    if not ADAPTIVE_EXITS_ENABLED:
        return
    try:
        pnl_pct, entry_real, cur = get_position_pnl_pct(sym, side)
        if entry_real <= 0 or sl == entry_real:
            return
        risk_dist = abs(entry_real - sl)
        if risk_dist <= 0:
            return
        r_now = abs(cur - entry_real) / risk_dist
        if r_now < BE_TRIGGER_R:
            return  # ещё не достигли +1R
        # 1. Partial close 50% (market reduce-only)
        import urllib.parse
        close_side = "Sell" if side in ("LONG", "BUY") else "Buy"
        half_qty = float(f"{qty * PARTIAL_CLOSE_PCT / 100:.8g}")
        body = urllib.parse.urlencode({
            "category": "linear", "symbol": sym, "side": close_side,
            "orderType": "Market", "qty": str(half_qty),
            "reduceOnly": "true", "positionIdx": "0",
        })
        pres = _signed_post("/v5/order/create", body)
        if pres.get("retCode") == 0:
            log_event("PARTIAL_CLOSE", {"symbol": sym, "qty": half_qty,
                                          "r_multiple": round(r_now, 2)})
            print(f"  💰 {sym}: PARTIAL CLOSE {half_qty} @ +{r_now:.1f}R")
        # 2. SL → breakeven (entry ± небольшая защита)
        be = entry_real  # безубыток = цена входа
        bres = set_trading_stop(sym, side, be, tp)
        if bres.get("ok"):
            log_event("BE_SL_SET", {"symbol": sym, "be": be})
            print(f"  🛡️ {sym}: SL → BE @ {be:.6g}")
    except Exception as e:
        log_event("ADAPTIVE_EXIT_ERR", {"symbol": sym, "error": str(e)})

# ─── State helpers ─────────────────────────────────────────────────

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"active_limits": {}, "filled": {}}


def save_state(state: dict) -> None:
    """FIX 2026-09-02 (audit LOW): атомарная запись через tmp+rename."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(STATE_FILE)


def log_event(event_type: str, data: dict) -> None:
    rec = {"ts": time.time(), "iso": datetime.now(timezone.utc).isoformat(),
           "event": event_type, **data}
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ─── API helpers ─────────────────────────────────────────────────────

def _load_creds() -> tuple[str, str]:
    """Читать ключи НАПРЯМУЮ из файла, игнорируя окружение процесса.

    FIX 2026-09-03: load_dotenv(override=False) НЕ перезаписывает уже
    существующие переменные окружения. Если в окружении бота/systemd уже
    есть BYBIT_API_KEY (другой/старый), os.getenv возвращал его → retCode
    10003 "API key is invalid". Читаем файл напрямую — всегда правильный ключ.
    """
    try:
        env = {}
        for line in Path("/root/.bybit_executor.env").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
        return env.get("BYBIT_API_KEY", ""), env.get("BYBIT_API_SECRET", "")
    except Exception:
        return os.getenv("BYBIT_API_KEY", ""), os.getenv("BYBIT_API_SECRET", "")


def _signed_post(path: str, body: str) -> dict:
    ak, sec = _load_creds()
    if not ak or not sec:
        return {"retCode": -1, "retMsg": "no creds"}
    ts = str(int(time.time() * 1000))
    payload = f"{ts}{ak}5000{body}"
    sig = __import__("hmac").new(sec.encode(), payload.encode(),
                                 __import__("hashlib").sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    r = httpx.post(f"https://api-demo.bybit.com{path}",
                   content=body, headers=headers, timeout=10)
    return r.json()


def _signed_get(path: str, query: str) -> dict:
    ak, sec = _load_creds()
    if not ak or not sec:
        return {"retCode": -1, "retMsg": "no creds"}
    ts = str(int(time.time() * 1000))
    sig = __import__("hmac").new(sec.encode(),
                                 (ts + ak + "5000" + query).encode(),
                                 __import__("hashlib").sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sig,
    }
    r = httpx.get(f"https://api-demo.bybit.com{path}?{query}",
                  headers=headers, timeout=10)
    return r.json()


# ─── Level calculation ───────────────────────────────────────────────

def calculate_levels(sig: dict) -> dict | None:
    """Compute L1/L2 limit prices and R:R from a scanner signal."""
    side = sig["side"]
    price = float(sig.get("price", 0))   # closed_last
    atr = float(sig.get("atr", 0))
    sl = float(sig.get("sl", 0))
    raw_tp = float(sig.get("raw_tp", 0))

    if atr <= 0 or price <= 0 or sl == 0 or raw_tp == 0:
        return None

    if side == "LONG":
        l1 = price - atr * L1_ATR_MULT
        l2 = price - atr * L2_ATR_MULT
        # sanity: both must be > SL and < current price
        if l1 <= sl or l2 <= sl or l1 >= price or l2 >= price:
            return None
    else:  # SHORT
        l1 = price + atr * L1_ATR_MULT
        l2 = price + atr * L2_ATR_MULT
        if l1 >= sl or l2 >= sl or l1 <= price or l2 <= price:
            return None

    rr_l1 = round(abs(raw_tp - l1) / abs(l1 - sl), 2) if abs(l1 - sl) > 0 else 0
    rr_l2 = round(abs(raw_tp - l2) / abs(l2 - sl), 2) if abs(l2 - sl) > 0 else 0

    if rr_l1 < MIN_RR and rr_l2 < MIN_RR:
        return None

    return {
        "l1": round(l1, 8),
        "l2": round(l2, 8),
        "rr_l1": rr_l1,
        "rr_l2": rr_l2,
        "sl": round(sl, 8),
        "tp": round(raw_tp, 8),
        "atr": round(atr, 8),
        "signal_price": round(price, 8),
    }


# ─── Order placement ─────────────────────────────────────────────────

def place_limit(symbol: str, side: str, price: float, qty: float,
                sl: float, tp: float, level: str) -> dict:
    order_side = "Buy" if side in ("LONG", "BUY") else "Sell"

    # FIX 2026-09-03 (MSFT bug): PostOnly SELL @510.94 при цене 510.5 →
    # EC_PostOnlyWillTakeLiquidity, мгновенный Cancel (2 попытки подряд).
    # Решение: если цена УЖЕ в/за зоной — ордер не слать, вернуть понятную
    # ошибку (владелец решает: переставить или ждать), а не молчаливый Cancel.
    cur = get_current_price(symbol)
    if cur > 0:
        if order_side == "Sell" and price <= cur:
            return {"ok": False, "retCode": -1,
                    "error": f"PostOnly SELL {price} уже marketable (цена {cur}) — ордер пересёк бы книгу. Переставь ниже цены или жди откат."}
        if order_side == "Buy" and price >= cur:
            return {"ok": False, "retCode": -1,
                    "error": f"PostOnly BUY {price} уже marketable (цена {cur}) — ордер пересёк бы книгу. Переставь выше цены или жди откат."}

    order_link = f"autolim-{symbol}-{level}-{int(time.time())}"

    import urllib.parse
    body = {
        "category": "linear", "symbol": symbol,
        "side": order_side, "orderType": "Limit",
        "qty": str(qty), "price": str(price),
        "timeInForce": "PostOnly",
        "orderLinkId": order_link,
        "positionIdx": "0",
    }
    # FIX 2026-09-03: прикреплять SL/TP НЕПОСРЕДСТВЕННО к лимитке, чтобы
    # защита была видна и активна ещё ДО филла (иначе ордер без SL/TP).
    # (Владелец: «у ордера нет сл и тп»). tpslMode=Full — оба уровня.
    if sl and tp:
        body["stopLoss"] = str(sl)
        body["takeProfit"] = str(tp)
        body["tpslMode"] = "Full"

    body_qs = urllib.parse.urlencode(body)
    res = _signed_post("/v5/order/create", body_qs)
    if res.get("retCode") == 0:
        oid = (res.get("result") or {}).get("orderId", "")
        return {"ok": True, "order_id": oid, "order_link_id": order_link,
                "cur_price": cur}
    # FIX 2026-09-03: понятное сообщение на PostOnly-отклонение
    msg = res.get("retMsg", "unknown")
    if res.get("retCode") == 10001 and "PostOnly" in msg:
        msg = f"PostOnly отклонён биржей (цена ушла в зону): {msg}. Переставь цену."
    return {"ok": False, "error": msg,
            "retCode": res.get("retCode")}


def cancel_limit(symbol: str, order_id: str) -> dict:
    import urllib.parse
    body = urllib.parse.urlencode({
        "category": "linear", "symbol": symbol, "orderId": order_id,
    })
    res = _signed_post("/v5/order/cancel", body)
    if res.get("retCode") == 0:
        return {"ok": True}
    return {"ok": False, "error": res.get("retMsg", "unknown")}


def get_current_price(symbol: str) -> float:
    try:
        r = httpx.get("https://api.bybit.com/v5/market/tickers",
                      params={"category": "linear", "symbol": symbol},
                      timeout=10)
        return float((((r.json().get("result") or {}).get("list") or [{}])[0]).get("lastPrice", 0))
    except Exception:
        return 0.0


def get_open_orders(symbol: str) -> list[dict]:
    res = _signed_get("/v5/order/realtime",
                      f"category=linear&symbol={symbol}&orderFilter=order")
    if res.get("retCode") == 0:
        return res.get("result", {}).get("list", [])
    return []


def get_open_position_symbols() -> list[str]:
    """Return symbols with open positions on demo account."""
    res = _signed_get("/v5/position/list", "category=linear&settleCoin=USDT")
    if res.get("retCode") != 0:
        return []
    syms = []
    for p in res.get("result", {}).get("list", []):
        if float(p.get("size", 0)) != 0:
            syms.append(p.get("symbol", ""))
    return syms


# ─── SL/TP attach after fill ───────────────────────────────────────

def set_trading_stop(symbol: str, side: str, sl: float, tp: float) -> dict:
    """Attach SL/TP to an open position after limit fill.
    
    FIX 2026-09-02: positionIdx НЕ передаём — Unified Trading Account (UTA)
    использует positionIdx=0 (не hedge mode), а positionIdx=1/2 работает
    только в hedge mode. Удаление positionIdx решает ошибку 10001.
    
    Также фикс: для SHORT SL ДОЛЖЕН быть > TP (цена растёт = стоп).
    Если TP > SL — API возвращает ошибку 30030.
    """
    import urllib.parse
    # Validate SL/TP direction
    if side in ("LONG", "BUY"):
        if sl >= tp:
            return {"ok": False, "error": f"LONG: SL ({sl}) must be < TP ({tp})"}
    else:  # SHORT/SELL
        if tp >= sl:
            return {"ok": False, "error": f"SHORT: SL ({sl}) must be > TP ({tp})"}

    body = urllib.parse.urlencode({
        "category": "linear", "symbol": symbol,
        "stopLoss": str(sl), "takeProfit": str(tp),
    })
    res = _signed_post("/v5/position/trading-stop", body)
    if res.get("retCode") == 0:
        return {"ok": True}
    # Retry with positionIdx=0 (fallback for some account types)
    if res.get("retCode") == 10001:
        body = urllib.parse.urlencode({
            "category": "linear", "symbol": symbol,
            "positionIdx": "0",
            "stopLoss": str(sl), "takeProfit": str(tp),
        })
        res = _signed_post("/v5/position/trading-stop", body)
        if res.get("retCode") == 0:
            return {"ok": True}
    return {"ok": False, "error": res.get("retMsg", "unknown")}


# ─── Core logic ──────────────────────────────────────────────────────

def qty_from_risk(risk_usd: float, entry: float, sl: float, symbol: str) -> float:
    """Compute qty from risk $, entry price and SL."""
    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0
    raw_qty = risk_usd / sl_dist

    # Fetch lot filters
    try:
        r = httpx.get("https://api.bybit.com/v5/market/instruments-info",
                      params={"category": "linear", "symbol": symbol},
                      timeout=10)
        lot = (((r.json().get("result") or {}).get("list") or [{}])[0]).get("lotSizeFilter", {})
        qty_step = float(lot.get("qtyStep", "0.001") or 0.001)
        min_qty = float(lot.get("minOrderQty", "0.001") or 0.001)
        max_qty = float(lot.get("maxOrderQty", "1e12") or "1e12")
        min_not = float(lot.get("minNotionalValue", "5") or 5)
    except Exception:
        qty_step, min_qty, max_qty, min_not = 0.001, 0.001, 1e12, 5.0

    qty = int(raw_qty / qty_step) * qty_step
    qty = float(f"{qty:.8g}")
    if qty < min_qty:
        qty = min_qty
    if qty * entry < min_not:
        qty = (int(raw_qty / qty_step) + 1) * qty_step
        qty = float(f"{qty:.8g}")
    if qty > max_qty:
        qty = max_qty
    if qty <= 0 or qty * entry < min_not:
        return 0.0
    return qty


def scan_and_place():
    cfg = load_config()
    # Top 10 symbols by volume (or first 10 from config)
    symbols = cfg.get("symbols", [])[:10]
    min_score = int(cfg.get("min_signal_score", 70))

    state = load_state()
    now = time.time()

    # 1. Expire old limits
    # FIX 2026-09-03: owner_bet (визард /bet) живёт 24ч — структурный вход ждёт
    # откат часами, 2h-экспирация убивала ставку владельца до откатa.
    expired = []
    for sym, lim in list(state.get("active_limits", {}).items()):
        expiry_min = OWNER_BET_EXPIRY_H * 60 if lim.get("owner_bet") else EXPIRY_MIN
        if now - lim.get("placed_at", 0) > expiry_min * 60:
            expired.append(sym)
            for lvl in ("l1", "l2"):
                oid = lim.get(f"{lvl}_order_id")
                if oid:
                    # FIX 2026-09-02 (audit HIGH): проверяем результат cancel —
                    # иначе orphan order остаётся на бирже без мониторинга
                    cres = cancel_limit(sym, oid)
                    if not cres.get("ok"):
                        log_event("EXPIRY_CANCEL_FAIL",
                                  {"symbol": sym, "level": lvl, "oid": oid,
                                   "error": cres.get("error", "")})
                        print(f"  ⚠️  {sym}: expiry cancel {lvl} failed: {cres.get('error')}")
    for sym in expired:
        del state["active_limits"][sym]
        log_event("CANCELLED_EXPIRED", {"symbol": sym})

    # FIX 2026-09-02 (audit HIGH): garbage-collect filled{} (unbounded growth)
    gc_cutoff = now - 7 * 86400
    stale_filled = [s for s, v in state.get("filled", {}).items()
                    if v.get("filled_at", 0) < gc_cutoff]
    for s in stale_filled:
        del state["filled"][s]
    if stale_filled:
        log_event("FILLED_GC", {"symbols": stale_filled})

    # 2. Count active (limits + real positions)
    real_positions = get_open_position_symbols()
    active_symbols = set(state.get("active_limits", {}).keys()) | set(real_positions)
    if len(active_symbols) >= MAX_POSITIONS:
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] "
              f"Max positions: {len(active_symbols)}/{MAX_POSITIONS}")
        save_state(state)
        return

    # FIX 2026-09-03: portfolio risk + same-side caps.
    # Ночь 02-03.09: 3 коррелированных SHORT (XRP/DOGE/SOL) стопнулись
    # одновременно (-$99 за 2ч) — лимитки не имели ни side-капа, ни
    # портфельного риск-лимита. Считаем живые риски по позициям + лимиткам.
    open_risk = 0.0
    side_counts = {"LONG": 0, "SHORT": 0}
    res_pl = _signed_get("/v5/position/list", "category=linear&settleCoin=USDT")
    if res_pl.get("retCode") == 0:
        for p in res_pl.get("result", {}).get("list", []):
            if float(p.get("size", 0)) == 0:
                continue
            entry = float(p.get("avgPrice", 0) or 0)
            slv = float(p.get("stopLoss", 0) or 0)
            if entry > 0 and slv > 0:
                open_risk += abs(entry - slv) * float(p.get("size", 0))
            ps = p.get("side")
            side_counts["LONG" if ps == "Buy" else "SHORT"] += 1
    # лимитки в ожидании тоже рискуют
    for lim in state.get("active_limits", {}).values():
        lv = lim.get("side")
        side_counts["LONG" if lv in ("LONG", "BUY") else "SHORT"] += 1

    # 3. Scan
    with httpx.Client() as client:
        for sym in symbols:
            if sym in active_symbols:
                continue
            try:
                sig = score_symbol(client, sym, min_score)
                if not sig:
                    continue
                if sig.get("trade_decision") != "ALLOW":
                    continue

                # Same-side cap: не более MAX_SAME_SIDE одного направления
                # (лимитки + позиции вместе). XRP/DOGE/SOL-инцидент.
                sig_side = "LONG" if sig["side"] in ("LONG", "BUY") else "SHORT"
                if side_counts[sig_side] >= MAX_SAME_SIDE:
                    log_event("SKIP_SAME_SIDE_CAP",
                              {"symbol": sym, "side": sig_side,
                               "count": side_counts[sig_side]})
                    continue
                # Portfolio risk cap: суммарный открытый риск
                if open_risk + RISK_PER_TRADE_USD > MAX_PORTFOLIO_RISK_USD:
                    log_event("SKIP_PORTFOLIO_RISK_CAP",
                              {"symbol": sym, "open_risk": round(open_risk, 2),
                               "cap": MAX_PORTFOLIO_RISK_USD})
                    continue

                levels = calculate_levels(sig)
                if not levels:
                    continue

                qty = qty_from_risk(RISK_PER_TRADE_USD, levels["l1"],
                                    levels["sl"], sym)
                if qty <= 0:
                    continue

                # FIX 2026-09-02 (audit HIGH): save state СРАЗУ после L1 —
                # crash между placement и save не потеряет ордер
                if res1.get("ok"):
                    state["active_limits"][sym] = {
                        "placed_at": now,
                        "side": sig["side"],
                        "sl": levels["sl"],
                        "tp": levels["tp"],
                        "l1_price": levels["l1"],
                        "l2_price": levels["l2"],
                        "l1_order_id": res1.get("order_id"),
                        "l2_order_id": None,  # ставится после res2
                        "qty": qty,
                        "rr_l1": levels["rr_l1"],
                        "rr_l2": levels["rr_l2"],
                    }
                    save_state(state)  # checkpoint после L1
                    # FIX 2026-09-03: инкремент капов в ЭТОМ же цикле, чтобы
                    # два сигнала в одном проходе не прошли side/risk-капы вместе
                    side_counts["LONG" if sig["side"] in ("LONG", "BUY") else "SHORT"] += 1
                    open_risk += RISK_PER_TRADE_USD

                # Place L2 (if R:R good enough)
                if levels["rr_l2"] >= MIN_RR:
                    res2 = place_limit(sym, sig["side"], levels["l2"], qty,
                                       levels["sl"], levels["tp"], "L2")
                    if res2.get("ok") and sym in state["active_limits"]:
                        state["active_limits"][sym]["l2_order_id"] = res2.get("order_id")
                        save_state(state)  # фиксируем L2 сразу
                    elif sym in state["active_limits"]:
                        state["active_limits"][sym]["l2_order_id"] = None

                if res1.get("ok"):
                    log_event("LIMITS_PLACED", {
                        "symbol": sym, "side": sig["side"],
                        "l1": levels["l1"], "l2": levels["l2"],
                        "sl": levels["sl"], "tp": levels["tp"],
                        "rr_l1": levels["rr_l1"], "rr_l2": levels["rr_l2"],
                        "l1_oid": res1.get("order_id"),
                        "l2_oid": state["active_limits"].get(sym, {}).get("l2_order_id"),
                        "qty": qty,
                    })
                    print(f"  📌 {sym} {sig['side']}: "
                          f"L1={levels['l1']:.4f} RR={levels['rr_l1']:.2f}, "
                          f"L2={levels['l2']:.4f} RR={levels['rr_l2']:.2f}, "
                          f"qty={qty:.4f}")

            except Exception as e:
                print(f"  ⚠️  {sym}: scan error {e}")

    save_state(state)


def monitor_fills():
    state = load_state()
    now = time.time()

    for sym, lim in list(state.get("active_limits", {}).items()):
        try:
            orders = get_open_orders(sym)
            l1_oid = lim.get("l1_order_id")
            l2_oid = lim.get("l2_order_id")

            l1_filled = False
            l2_filled = False
            l1_open = False
            l2_open = False

            for o in orders:
                oid = o.get("orderId")
                st = o.get("orderStatus", "")
                if oid == l1_oid:
                    l1_filled = st in ("Filled", "PartiallyFilled")
                    l1_open = st in ("New", "PartiallyFilled")
                if oid == l2_oid:
                    l2_filled = st in ("Filled", "PartiallyFilled")
                    l2_open = st in ("New", "PartiallyFilled")

            # FIX 2026-09-02 (audit CRITICAL#3): L2 fill при ещё открытом L1 (gap).
            # L2 ушёл из open-orders, но L1 ещё висит — значит L2 ФИЛЛИЛСЯ.
            # Позиция есть, SL/TP надо ставить СЕЙЧАС, а L1 отменить.
            position_exists = sym in get_open_position_symbols()
            if position_exists and not (l1_filled or l2_filled):
                # Один из уровней филлится между запусками (gap/stealth).
                if l1_open and not l2_open and l2_oid:
                    # L2 исчез из open-orders → он филлится → отменяем L1 (OCO)
                    cres = cancel_limit(sym, l1_oid)
                    log_event("L2_STEALTH_L1_CANCEL",
                              {"symbol": sym, "cancel_ok": cres.get("ok", False)})
                elif l2_open and not l1_open and l1_oid:
                    # L1 исчез → филлится L2 → отменяем L2... нет, он уже open.
                    # Это значит L1 филлится. Отменяем L2.
                    cres = cancel_limit(sym, l2_oid)
                    log_event("L1_STEALTH_L2_CANCEL",
                              {"symbol": sym, "cancel_ok": cres.get("ok", False)})
                stp_res = set_trading_stop(sym, lim["side"], lim["sl"], lim["tp"])
                state["filled"][sym] = {
                    "entry": lim["l1_price"], "side": lim["side"],
                    "sl": lim["sl"], "tp": lim["tp"],
                    "qty": lim["qty"], "filled_at": now, "level": "STEALTH_FILL",
                    "sltp_ok": stp_res.get("ok", False),
                    "sltp_error": stp_res.get("error", ""),
                }
                del state["active_limits"][sym]
                if stp_res.get("ok"):
                    log_event("STEALTH_FILL", {"symbol": sym, "entry": lim["l1_price"],
                                                 "sl": lim["sl"], "tp": lim["tp"]})
                    print(f"  ✅ {sym}: STEALTH FILL → SL/TP set")
                    apply_adaptive_exit(sym, lim["side"], lim["l1_price"],
                                        lim["sl"], lim["tp"], lim["qty"])
                else:
                    log_event("STEALTH_FILL_SLTP_FAIL", {"symbol": sym, "entry": lim["l1_price"],
                                                            "error": stp_res.get("error", "")})
                    print(f"  ⚠️  {sym}: STEALTH FILL → SL/TP FAILED: {stp_res.get('error')} (manual fix required)")
                continue

            # Case 1: L1 filled → cancel L2, attach SL/TP
            if l1_filled:
                # FIX 2026-09-02 (audit CRITICAL#1): проверяем cancel — если L2
                # успел филлиться между snapshot и cancel → double position.
                l2_cancel = {"ok": True}
                if l2_oid:
                    l2_cancel = cancel_limit(sym, l2_oid)
                    if not l2_cancel.get("ok"):
                        # L2 мог филлиться. Проверяем позицию: qty > ожидаемого = double fill
                        log_event("L1_FILLED_L2_CANCEL_FAIL",
                                  {"symbol": sym, "error": l2_cancel.get("error", "")})
                # Attach SL/TP to position
                stp_res = set_trading_stop(sym, lim["side"], lim["sl"], lim["tp"])
                # ALWAYS remove from active_limits — position exists, L2 cancelled
                state["filled"][sym] = {
                    "entry": lim["l1_price"], "side": lim["side"],
                    "sl": lim["sl"], "tp": lim["tp"],
                    "qty": lim["qty"], "filled_at": now, "level": "L1",
                    "l2_cancel_ok": l2_cancel.get("ok", False),
                    "sltp_ok": stp_res.get("ok", False),
                    "sltp_error": stp_res.get("error", ""),
                }
                del state["active_limits"][sym]
                if stp_res.get("ok"):
                    log_event("L1_FILLED", {"symbol": sym, "entry": lim["l1_price"],
                                              "sl": lim["sl"], "tp": lim["tp"]})
                    print(f"  ✅ {sym}: L1 FILLED → SL/TP set")
                    apply_adaptive_exit(sym, lim["side"], lim["l1_price"],
                                        lim["sl"], lim["tp"], lim["qty"])
                else:
                    log_event("L1_FILLED_SLTP_FAIL", {"symbol": sym, "entry": lim["l1_price"],
                                                        "error": stp_res.get("error", "")})
                    print(f"  ⚠️  {sym}: L1 FILLED → SL/TP FAILED: {stp_res.get('error')} (manual fix required)")
                continue

            # Case 2: L2 filled (L1 never filled)
            # FIX 2026-09-02 (audit CRITICAL#2): ОБЯЗАТЕЛЬНО отменяем L1 —
            # иначе orphan L1 остаётся и может филлиться позже (double position).
            if l2_filled:
                l1_cancel = {"ok": True}
                if l1_oid:
                    l1_cancel = cancel_limit(sym, l1_oid)
                    if not l1_cancel.get("ok"):
                        log_event("L2_FILLED_L1_CANCEL_FAIL",
                                  {"symbol": sym, "error": l1_cancel.get("error", "")})
                stp_res = set_trading_stop(sym, lim["side"], lim["sl"], lim["tp"])
                state["filled"][sym] = {
                    "entry": lim["l2_price"], "side": lim["side"],
                    "sl": lim["sl"], "tp": lim["tp"],
                    "qty": lim["qty"], "filled_at": now, "level": "L2",
                    "l1_cancel_ok": l1_cancel.get("ok", False),
                    "sltp_ok": stp_res.get("ok", False),
                    "sltp_error": stp_res.get("error", ""),
                }
                del state["active_limits"][sym]
                if stp_res.get("ok"):
                    log_event("L2_FILLED", {"symbol": sym, "entry": lim["l2_price"],
                                            "sl": lim["sl"], "tp": lim["tp"]})
                    print(f"  ✅ {sym}: L2 FILLED → SL/TP set (L1 cancelled)")
                    apply_adaptive_exit(sym, lim["side"], lim["l2_price"],
                                        lim["sl"], lim["tp"], lim["qty"])
                else:
                    log_event("L2_FILLED_SLTP_FAIL", {"symbol": sym, "entry": lim["l2_price"],
                                                        "error": stp_res.get("error", "")})
                    print(f"  ⚠️  {sym}: L2 FILLED → SL/TP FAILED: {stp_res.get('error')} (manual fix required)")
                continue

            # Case 3: price moved away → cancel both
            # FIX 2026-09-03: для owner_bet (ручная ставка владельца) НЕ отменяем
            # по уходу цены — владелец сам решает, ордер висит до экспирации.
            # Авто-лимитки (не owner_bet) по-прежнему отменяются при уходе >0.5%.
            if not lim.get("owner_bet"):
                current = get_current_price(sym)
                if current > 0:
                    side = lim["side"]
                    l1_price = lim["l1_price"]
                    if side == "LONG":
                        if current > l1_price * (1 + CANCEL_THRESHOLD_PCT / 100):
                            if l1_oid and l1_open:
                                cancel_limit(sym, l1_oid)
                            if l2_oid and l2_open:
                                cancel_limit(sym, l2_oid)
                            del state["active_limits"][sym]
                            log_event("CANCELLED_MISSED", {"symbol": sym, "current": current})
                            print(f"  ❌ {sym}: price moved UP past zone → cancelled")
                    else:  # SHORT
                        if current < l1_price * (1 - CANCEL_THRESHOLD_PCT / 100):
                            if l1_oid and l1_open:
                                cancel_limit(sym, l1_oid)
                            if l2_oid and l2_open:
                                cancel_limit(sym, l2_oid)
                            del state["active_limits"][sym]
                            log_event("CANCELLED_MISSED", {"symbol": sym, "current": current})
                            print(f"  ❌ {sym}: price moved DOWN past zone → cancelled")

        except Exception as e:
            print(f"  ⚠️  {sym}: monitor error {e}")

    save_state(state)


if __name__ == "__main__":
    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] "
          f"Auto Limit Placer — scan + monitor")
    scan_and_place()
    monitor_fills()
