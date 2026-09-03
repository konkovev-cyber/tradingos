"""
guardian/reality_guardian.py
Reality Guardian — LIVE profit protection for Reality positions.
Polls Bybit positions every 30s, applies Guardian rules:
- MFE >= 0.8R  → move SL to breakeven (entry)
- MFE >= 1.0R  → move SL to entry + 0.5*ATR (partial lock)
- MFE >= 1.5R  → move SL to entry + 1.0*ATR (tighter lock)
- HOLD_HOURS > 48 → timeout alert (no SL change)

Calls BybitAdapter.set_trading_stop() — LIVE modifications on exchange.
Persists state to guardian/reality_state.json (BE, partial flags).
Logs every action to profit_alerts.jsonl.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("reality_guardian")

# Paths
GUARDIAN_STATE_PATH = Path("/root/tradingos/guardian/reality_state.json")
PROFIT_ALERTS_PATH = Path("/root/tradingos/guardian/profit_alerts.jsonl")
TIMEOUT_ALERTS_PATH = Path("/root/tradingos/guardian/timeout_alerts.jsonl")
RECOVERY_EVENTS_PATH = Path("/root/tradingos/memory/recovery_events.jsonl")
INTERVENTION_LOG_PATH = Path("/root/tradingos/memory/process_interventions.jsonl")
ENV_PATH = "/root/trading_brain_v4/research/execution/.env"

# 2026-08-27: Demo account switch — private endpoints route to api-demo.bybit.com
# when BYBIT_DEMO=true in ENV_PATH. Public market data (tickers) stays on mainnet.
def _demo_enabled() -> bool:
    v = (os.environ.get("BYBIT_DEMO", "") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    try:
        with open(ENV_PATH) as f:
            for l in f:
                l = l.strip()
                if l.startswith("BYBIT_DEMO="):
                    return l.split("=", 1)[1].strip().lower() in ("1", "true", "yes", "on")
    except FileNotFoundError:
        pass
    return False

_API_BASE = "https://api-demo.bybit.com" if _demo_enabled() else "https://api.bybit.com"

# Guardian config
POLL_INTERVAL = 30  # seconds
MAX_HOLD_HOURS = 48
BE_THRESHOLD = 0.8   # R multiple to move SL to breakeven (2026-09-01: 0.6 срабатывал при MFE 0.2-0.5R ночью — BE запирал позицию в минус; 0.8 = только при реальном движении)
BE_MIN_PRICE_PCT = 1.5   # ИЛИ +1.5% цены от входа (было 0.5% — срабатывало раньше R-порога на широких стопах и выбивало в минус: ROSE +0.28R MFE → −$0.92)
BE_LOCK_FRACTION = 0.3   # 2026-08-31: BE переносит SL НЕ на вход, а на вход + 30% от дохода (не в ноль!)
PARTIAL_THRESHOLD = 1.0  # R multiple to move SL to entry + 0.5*ATR
TIGHT_THRESHOLD = 1.5  # R multiple to move SL to entry + 1.0*ATR

# ─── Soft-SL Recovery (Rule 3.5a, MANUAL-first, за флагом soft_sl_recovery) ──
# Когда цена касается sl_soft и выполняется строгое условие отката — даём окно
# RECOVERY_BARS на восстановление вместо мгновенного закрытия. Пол = FLOOR_BUFFER_R
# ниже цены arm'а (cap downside). Данные: MAE > MFE структурно → recovery строго
# conditioned и измеряется (memory/recovery_events.jsonl).
RECOVERY_BARS = 3           # 3 × 15m = 45 мин recovery-окно
RECOVERY_BAR_SEC = 900      # 15m
FLOOR_BUFFER_R = 0.25       # пол на 0.25R ниже цены arm'а (adverse side)
RSI_OVERSOLD = 30           # LONG recovery при RSI < 30
RSI_OVERBOUGHT = 70         # SHORT recovery при RSI > 70
SL_BUFFER_ATR = 0.5         # hard-SL buffer = 0.5 × ATR(M15,14)
SL_BUFFER_R_CAP = 0.3       # ... но не более 0.3R (лимит доп. убытка)

# ─── Trailing stop (многоступенчатый, 2026-08-30 owner request) ─────────
# Раньше трейлинг стартовал только после TIGHT (пик ≥1.5R), а между
# ступенями BE/PARTIAL/TIGHT были большие разрывы: при развороте с 1.2R
# выходили по SL≈entry+0.5R, теряя до 0.7R прибыли («бу мог забрать больше»).
# Теперь трейлинг активен СРАЗУ после BE (пик ≥0.8R) и следует за пиком
# плотно (0.25R), с мелким шагом (0.05R) — забирает максимум на откатах.
TRAILING_ENABLED = True
TRAIL_START_AFTER_BE = True      # старт трейлинга после BE (а не после TIGHT)
TRAIL_DISTANCE_R = 0.50          # SL = peak - 0.50R (широкий зазор — даём позиции жить)
TRAIL_MIN_STEP_R = 0.10          # двигать SL при росте пика ≥0.10R (крупнее шаг, меньше дерганий)
TRAIL_MOVE_TP = True             # двигать TP только если он БЫЛ установлен
TRAIL_TP_DISTANCE_R = 1.0

# ─── Near-TP close (2026-08-30, owner request) ─────────────────────────
# BE/PARTIAL/TIGHT двигают SL к entry — НО между entry и ценой у TP откат
# съедает всю прибыль (SL на entry, цена у TP, цена пошла вниз → выходим
# в ноль). Когда цена дошла до 90% пути к TP — закрываем по рынку сами,
# реализуя почти полный TP до возможного отката.
NEAR_TP_CLOSE_ENABLED = True
NEAR_TP_CLOSE_R = 0.9              # закрыть при r_multiple >= 0.9 × путь-до-TP

# ─── Peak-Reversal exit (2026-08-31, owner вводная) ─────────────────────
# TP часто в зоне недостижимости (4×ATR при MFE ~1R) — ждать его = отдать
# 90% пика на откате. Правило: позиция достигла пика >= PEAK_MIN_R, затем
# откатилась на >= PEAK_REVERSAL_GIVEBACK от пика → закрываем по рынку.
PEAK_REVERSAL_ENABLED = True
PEAK_MIN_R = 1.0                    # значимый пик: >= +1R
PEAK_REVERSAL_GIVEBACK = 0.6        # откат >= 0.6R от пика = разворот начался (широкий зазор)

# ─── Time-stop for stale losers (2026-08-31) ─────────────────────────────
# GMT/ROSE висели часами в минусе (mfe <0.1R, mae −0.8R), занимая anti-слоты
# и блокируя корреляционно все новые SELL. Исследование: 62% SL-сделок идут
# дальше против — «ждать отскока» для позиции без пика вредно. Если позиция
# ≥ STALE_HOURS в минусе (MAE ≤ −STALE_MAE_R) и пик был мизерный — закрываем.
# ─── Time-stop — ОТКЛЮЧЁН (2026-08-31 owner) ─────────────────────────────
# Владелец: «ручное закрытие в минусе — это ТВОЁ, не моё; недопустимо».
# TIME-STOP сам фиксировал убытки (GMT/ROSE −$24). Убытки фиксирует ТОЛЬКО
# stop-loss на бирже. Система закрывает ТОЛЬКО с прибылью (BE/трейлинг/
# peak-reversal/NEAR-TP).
TIME_STOP_ENABLED = False           # ВЫКЛЮЧЕНО владельцем
TIME_STOP_HOURS = 2.0               # (неактивно)
TIME_STOP_MAE_R = -0.5              # (неактивно)
TIME_STOP_PEAK_R = 0.3              # (неактивно)


def _read_config_version() -> str:
    """Read the active config_version from trading_mode.json for record attribution.
    Returns "unknown" if the file is missing or the field is absent."""
    try:
        from pathlib import Path
        cfg_path = Path("/root/tradingos/operations/trading_mode.json")
        if not cfg_path.exists():
            return "unknown"
        cfg = json.loads(cfg_path.read_text())
        return cfg.get("config_version", "unknown")
    except Exception:
        return "unknown"


def _safe_float(v, default: float = 0.0) -> float:
    """FIX 2026-08-24: Bybit returns "" for some numeric fields during sync windows.

    Without this, float("") raises and the closure pipeline bubbles the
    exception, the symbol stays in guardian state, and the Guardian loop
    resends the same Telegram close-notification every poll (TQQQ incident).
    """
    if v == "" or v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _load_credentials():
    ak, as_ = "", ""
    try:
        with open(ENV_PATH) as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, v = l.split("=", 1)
                    if k.strip() == "BYBIT_API_KEY":
                        ak = v.strip()
                    elif k.strip() == "BYBIT_API_SECRET":
                        as_ = v.strip()
    except FileNotFoundError:
        pass
    return ak, as_


def _get_live_positions():
    """Fetch all open Bybit positions. Returns list of dicts.

    Robust to:
    - API retCode != 0
    - result is None (e.g., wrong symbol filter)
    - result.list is None
    - missing size field
    """
    import hmac, hashlib, httpx
    ak, as_ = _load_credentials()
    if not ak or not as_:
        logger.error("No Bybit credentials")
        return []
    ts = str(int(time.time() * 1000))
    q = "category=linear&settleCoin=USDT"
    sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": ak,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign,
        "X-BAPI-RECV-WINDOW": "5000",
    }
    for attempt in range(2):
        try:
            r = httpx.get(
                f"{_API_BASE}/v5/position/list?{q}",
                headers=headers,
                timeout=10,
            )
            data = r.json()
            if data.get("retCode") != 0:
                logger.warning(f"Bybit API retCode={data.get('retCode')} msg={data.get('retMsg')}")
                if attempt == 0:
                    time.sleep(1)
                    continue
                return []
            result = data.get("result")
            if not result or not isinstance(result, dict):
                logger.warning(f"Bybit API returned invalid result: {type(result)}")
                if attempt == 0:
                    time.sleep(1)
                    continue
                return []
            raw_list = result.get("list")
            if not raw_list or not isinstance(raw_list, list):
                return []
            return [p for p in raw_list if isinstance(p, dict) and float(p.get("size", 0)) > 0]
        except Exception as e:
            logger.error(f"Bybit API error (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(1)
                continue
            return []
    return []


def _get_ticker(symbol):
    """Get current price for a symbol. Robust to None/empty responses."""
    import hmac, hashlib, httpx
    ak, as_ = _load_credentials()
    if not ak or not as_:
        return 0
    ts = str(int(time.time() * 1000))
    q = f"category=linear&symbol={symbol}"
    sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": ak,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign,
        "X-BAPI-RECV-WINDOW": "5000",
    }
    try:
        r = httpx.get(
            f"https://api.bybit.com/v5/market/tickers?{q}",
            headers=headers,
            timeout=10,
        )
        data = r.json()
        if data.get("retCode") == 0:
            result = data.get("result")
            if not isinstance(result, dict):
                return 0
            raw_list = result.get("list")
            if not raw_list or not isinstance(raw_list, list) or len(raw_list) == 0:
                return 0
            t = raw_list[0]
            if not isinstance(t, dict):
                return 0
            price = t.get("lastPrice", 0)
            if price is None:
                return 0
            return float(price)
    except Exception:
        pass
    return 0


def _set_trading_stop(symbol, stop_loss=None, take_profit=None,
                      sl_trigger_by=None, tp_trigger_by=None):
    """Call Bybit set_trading_stop to modify SL/TP on live position.
    Uses BybitClient for correct POST signing (raw HMAC doesn't work for POST).
    sl_trigger_by/tp_trigger_by: "LastPrice" | "MarkPrice" | "IndexPrice"."""
    try:
        sys.path.insert(0, "/root/trading_brain_v4")
        from exchange.bybit.client import BybitClient
        ak, as_ = _load_credentials()
        if not ak or not as_:
            return False
        client = BybitClient(api_key=ak, api_secret=as_, testnet=False, demo=_demo_enabled())
        params = {"symbol": symbol, "category": "linear", "position_idx": 0}
        if stop_loss is not None:
            params["stop_loss"] = str(stop_loss)
            if sl_trigger_by:
                params["sl_trigger_by"] = sl_trigger_by
        if take_profit is not None:
            params["take_profit"] = str(take_profit)
            if tp_trigger_by:
                params["tp_trigger_by"] = tp_trigger_by
        result = client.set_trading_stop(**params)
        if result.get("retCode") == 0:
            logger.info(f"✅ Guardian SL/TP updated for {symbol}: SL={stop_loss} TP={take_profit}")
            return True
        else:
            logger.error(f"❌ Failed to update SL/TP for {symbol}: {result.get('retMsg', '?')}")
            return False
    except Exception as e:
        logger.error(f"❌ Bybit API error on {symbol}: {e}")
        return False


def _create_reduce_only_order(symbol, side, quantity, order_type="Market"):
    """Создать reduce-only ордер для частичного закрытия позиции (T5 Partial TP).
    
    side: "Buy" или "Sell" (противоположно позиции — LONG закрывает SELL).
    quantity: количество для закрытия (50% от позиции).
    Возвращает True если ордер создан успешно."""
    try:
        sys.path.insert(0, "/root/trading_brain_v4")
        from exchange.bybit.client import BybitClient
        ak, as_ = _load_credentials()
        if not ak or not as_:
            return False
        client = BybitClient(api_key=ak, api_secret=as_, testnet=False, demo=_demo_enabled())
        close_side = "Sell" if side == "Buy" else "Buy"  # противоположно позиции
        # FIX 2026-09-02: округляем qty вниз к кратному шагу биржи.
        # 50% позиции = 1189.5 при шаге 1.0 → невалидно (Bybit "Qty invalid").
        # Достаём qtyStep из instruments-info и округляем вниз через Decimal.
        try:
            _import_httpx = __import__("httpx")
            _base = "https://api.bybit.com" if not _demo_enabled() else "https://api-demo.bybit.com"
            _r = _import_httpx.get(
                _base + "/v5/market/instruments-info",
                params={"category": "linear", "symbol": symbol}, timeout=8)
            _lot = ((_r.json().get("result") or {}).get("list") or [{}])[0].get("lotSizeFilter", {})
            _qstep = float(_lot.get("qtyStep", 1.0) or 1.0)
        except Exception:
            _qstep = 1.0
        if _qstep > 0:
            from decimal import Decimal, ROUND_DOWN
            _sd = Decimal(str(_qstep)).normalize()
            quantity = float(Decimal(str(quantity)).quantize(_sd, rounding=ROUND_DOWN))
        # Не меньше 1 единицы (мин. ордер)
        if quantity < 1:
            quantity = 1
        params = {
            "symbol": symbol,
            "side": close_side,
            "quantity": quantity,      # create_order арг: quantity (не qty)
            "order_type": order_type,  # create_order арг: order_type (не orderType)
            "category": "linear",
            "position_idx": 0,
        }
        # reduce_only через новый параметр create_order
        # FIX 2026-09-02: Bybit возвращает {"orderId": ...} БЕЗ retCode поля при успехе.
        # Проверка `result.get("retCode") == 0` была ложноположительной → Partial TP
        # считался упавшим и спамил в лог, хотя ордер исполнялся.
        result = client.create_order(**params, reduce_only=True)
        ok = ("orderId" in result and result.get("orderId")) or result.get("retCode") == 0
        if ok:
            logger.info(f"✅ PARTIAL TP: reduce-only {order_type} {quantity} {symbol} ({close_side})")
            return True
        else:
            logger.error(f"❌ Partial TP failed for {symbol}: {result.get('retMsg', result.get('retCode', '?'))}")
            return False
    except Exception as e:
        logger.error(f"❌ Partial TP error on {symbol}: {e}")
        return False


def _momentum_decayed(symbol: str, side: str) -> bool:
    """Грубая прокси momentum decay: последние 3 закрытых 15m бара против входа.

    Использует replay_cache (тот же источник, что engine_v2). Если данных нет —
    консервативно False (не закрываем по незнанию).
    """
    try:
        import pandas as pd
        p = Path("/root/tradingos/replay_cache") / f"{symbol}_M15.parquet"
        if not p.exists():
            return False
        df = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts")
        if df["ts"].iloc[0] > 1e12:
            df["ts"] = df["ts"] / 1000
        last3 = df.tail(3)
        if len(last3) < 3:
            return False
        up = str(side).lower().startswith("buy")
        against = 0
        for _, b in last3.iterrows():
            if up and b["c"] < b["o"]:
                against += 1
            elif not up and b["c"] > b["o"]:
                against += 1
        return against >= 2
    except Exception:
        return False


# ─── Soft-SL Recovery helpers (Rule 3.5a) ─────────────────────────────
def _load_m15_df(symbol: str):
    """Загрузить M15 replay-данные (колонки ts,o,h,l,c,v). None если нет."""
    try:
        import pandas as pd
        p = Path("/root/tradingos/replay_cache") / f"{symbol}_M15.parquet"
        if not p.exists():
            return None
        df = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts")
        if df["ts"].iloc[0] > 1e12:
            df["ts"] = df["ts"] / 1000
        return df
    except Exception:
        return None


def _atr_m15(symbol: str, period: int = 14) -> float:
    """ATR(M15, period) по закрытым барам. 0 если данных нет."""
    df = _load_m15_df(symbol)
    if df is None or len(df) < period + 1:
        return 0.0
    import pandas as pd
    h, l, c = df["h"], df["l"], df["c"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])


def _rsi_m15(symbol: str, period: int = 14) -> float:
    """RSI(14, M15) по закрытым барам. 50 если данных нет (нейтрально)."""
    df = _load_m15_df(symbol)
    if df is None or len(df) < period + 1:
        return 50.0
    delta = df["c"].diff()
    up = delta.clip(lower=0).rolling(period).mean()
    down = (-delta.clip(upper=0)).rolling(period).mean()
    rs = up / down.replace(0, 1e-9)
    return float(100 - 100 / (1 + rs.iloc[-1]))


def _recovery_condition(symbol: str, side: str, sl_soft: float, current: float) -> Optional[str]:
    """Строгое условие recovery-арма. Возвращает имя условия или None.

    - "wick": последняя закрытая M15 проткнула sl_soft (low/high), а текущая цена
      уже вернулась за него — классический wick-прокол, шанс отката есть.
    - "oversold(rsi=N)" / "overbought(rsi=N)": цена ПРОТИВ входа и RSI экстремален
      (шанс отскока). Пол 0.25R ограничивает downside.

    Из-за MAE>MFE не arm'им вслепую: без подтверждённого прокола/экстремума — None.
    """
    is_buy = str(side).lower().startswith("buy")
    df = _load_m15_df(symbol)
    if df is not None and len(df) >= 1:
        last = df.iloc[-1]
        touched = (float(last["l"]) <= sl_soft) if is_buy else (float(last["h"]) >= sl_soft)
        returned = (current >= sl_soft) if is_buy else (current <= sl_soft)
        if touched and returned:
            return "wick"
    rsi = _rsi_m15(symbol)
    if is_buy and current <= sl_soft and rsi < RSI_OVERSOLD:
        return f"oversold(rsi={rsi:.0f})"
    if not is_buy and current >= sl_soft and rsi > RSI_OVERBOUGHT:
        return f"overbought(rsi={rsi:.0f})"
    return None


def _sl_hard_buffer(symbol: str, risk_per_unit: float) -> float:
    """Buffer для hard SL = min(0.5×ATR(M15,14), 0.3R). 0 если нет данных."""
    atr = _atr_m15(symbol)
    buf = 0.5 * atr
    cap = risk_per_unit * SL_BUFFER_R_CAP
    return min(buf, cap) if cap > 0 else buf


def _log_recovery_event(rec):
    """Append recovery event (ARM/SOFT_SL/FLOOR/TIMEOUT/RECOVERED/HARD_SL)."""
    RECOVERY_EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(RECOVERY_EVENTS_PATH, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _close_position(symbol: str, side: str, qty: float) -> bool:
    """Market-close позиции reduce-only (raw signed POST).

    ВАЖНО: BybitClient.create_order НЕ поддерживает reduceOnly — market close
    через него РАЗВОРАЧИВАЕТ позицию (FHE case 2026-08-08: Sell 428 flipped
    BUY→SHORT). Поэтому закрываем напрямую с reduceOnly=true.
    """
    try:
        import hashlib
        import hmac
        import urllib.parse
        import httpx
        ak, as_ = _load_credentials()
        if not ak or not as_:
            return False
        ts = int(time.time() * 1000)
        recv_window = "5000"
        # F3 hardening: закрытие позиции — execution boundary. Неизвестный side
        # → skip close (reduceOnly ограничивает ущерб, но не угадываем Buy/Sell).
        _sd = str(side).lower()
        if _sd.startswith("buy"):
            close_side = "Sell"
        elif _sd.startswith("sell"):
            close_side = "Buy"
        else:
            logger.warning(f"close_guard: invalid side {side!r} — close skipped")
            return False
        body = urllib.parse.urlencode({
            "category": "linear", "symbol": symbol,
            "side": close_side, "orderType": "Market",
            "qty": str(qty), "reduceOnly": "true",
            "positionIdx": "0",
        })
        payload = f"{ts}{ak}{recv_window}{body}"
        sign = hmac.new(as_.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": ak,
            "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        r = httpx.post(f"{_API_BASE}/v5/order/create",
                       content=body, headers=headers, timeout=10)
        res = r.json()
        if res.get("retCode") == 0:
            logger.info(f"✅ GUARDIAN CLOSE: {symbol} {close_side} qty={qty} reduceOnly")
            return True
        logger.error(f"❌ GUARDIAN CLOSE FAIL {symbol}: {res.get('retMsg', '?')}")
        return False
    except Exception as e:
        logger.error(f"❌ GUARDIAN CLOSE ERROR {symbol}: {e}")
        return False


def _load_guardian_state():
    """Load Guardian state file (per-symbol). Returns {} if file is empty/corrupt."""
    if not GUARDIAN_STATE_PATH.exists():
        return {}
    try:
        with open(GUARDIAN_STATE_PATH) as f:
            raw = f.read().strip()
        if not raw or raw == "null":
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def manual_close_allowed(symbol: str, reason: str = "", current_price: float = 0.0) -> tuple[bool, str]:
    """T84 + 2026-08-31 owner: запрет ручного закрытия в УБЫТКЕ.

    Два слоя:
    1. T84: AUTO-позиция с активной лестницей (BE/PARTIAL/TIGHT) не закрывается
       вручную (терялись профиты TQQQ/TSLA/META) — кроме аварий.
    2. НОВОЕ (owner 2026-08-31): ЛЮБОЕ закрытие в минусе запрещено по умолчанию.
       Позиция в минусе = решение guardian (SL/time-stop) или владелец ждёт.
       Убыток фиксируется только системой, не эмоцией.
       Исключения: PANIC / EXCHANGE_FAILURE / EMERGENCY / TECHNICAL.

    Возвращает (allowed, reason_for_block)."""
    st = _load_guardian_state().get(symbol) or {}
    source = st.get("source", "AUTO")
    armed = bool(st.get("be_fired") or st.get("partial_fired") or st.get("tight_fired"))
    r = (reason or "").upper()
    if r in ("PANIC", "EXCHANGE_FAILURE", "EMERGENCY", "TECHNICAL"):
        return True, ""
    # Правило 2: ручное закрытие в минусе запрещено (owner 2026-08-31)
    if current_price > 0 and st:
        entry = float(st.get("entry", 0) or 0)
        side = str(st.get("side", "")).lower()
        if entry > 0 and side:
            is_loss = (current_price < entry) if side.startswith("buy") else (current_price > entry)
            if is_loss:
                return False, (
                    f"позиция {symbol} в УБЫТКЕ — закрытие вручную запрещено "
                    f"(owner 2026-08-31). Stop-loss/guardian решат сами. "
                    f"Аварийно: PANIC {symbol}"
                )
    if source == "MANUAL":
        return True, ""
    if not armed:
        return True, ""  # лестница не активирована — закрытие разрешено
    # PROCESS_INTERVENTION: фиксируем попытку ручного закрытия AUTO с лестницей
    try:
        rec = {
            "event": "PROCESS_INTERVENTION_BLOCKED",
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "source": source,
            "reason": reason,
            "be_fired": st.get("be_fired"),
            "partial_fired": st.get("partial_fired"),
            "tight_fired": st.get("tight_fired"),
        }
        INTERVENTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with INTERVENTION_LOG_PATH.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return (False,
            f"MANUAL_CLOSE_BLOCKED: {symbol} — защитная лестница активна "
            f"(BE={st.get('be_fired')} PARTIAL={st.get('partial_fired')} "
            f"TIGHT={st.get('tight_fired')}). Ручное закрытие AUTO-позиции после "
            f"активации защиты запрещено (T84, T58: оператор уничтожал MFE). "
            f"Используйте PANIC/EMERGENCY только при реальной аварии.")


def _save_guardian_state(state):
    """Save Guardian state file."""
    GUARDIAN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GUARDIAN_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _log_profit_alert(alert):
    """Append profit-protection alert."""
    PROFIT_ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFIT_ALERTS_PATH, "a") as f:
        f.write(json.dumps(alert) + "\n")


def _log_timeout_alert(alert):
    """Append timeout alert."""
    TIMEOUT_ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TIMEOUT_ALERTS_PATH, "a") as f:
        f.write(json.dumps(alert) + "\n")


# ─── Telegram integration (direct async via shared event loop) ───
_tg_loop = None
_tg_thread = None
_tg_lock = None
_tg_ready = False
_keepalive_refs = []  # prevent GC of background tasks


def _ensure_telegram_started():
    """Lazy-init Telegram notifier on first event. Thread-safe.

    Architecture:
    - Separate daemon thread runs asyncio event loop with run_forever()
    - TG init + all sends happen inside this loop
    - Guardian main thread uses run_coroutine_threadsafe to dispatch sends
    """
    global _tg_loop, _tg_thread, _tg_lock, _tg_ready
    if _tg_lock is None:
        import threading as _th
        _tg_lock = _th.Lock()
    with _tg_lock:
        if _tg_loop is not None and _tg_ready:
            return _tg_loop
        try:
            from tradingos.guardian.telegram_notifier import init_telegram
            # Verify creds before spinning up thread
            if not os.environ.get("TELEGRAM_BOT_TOKEN"):
                logger.error("TELEGRAM_BOT_TOKEN missing — TG disabled")
                return None

            _tg_loop = asyncio.new_event_loop()
            import threading
            init_done = threading.Event()
            init_error = [None]

            def runner():
                global _tg_ready
                try:
                    asyncio.set_event_loop(_tg_loop)
                    # Schedule init as task on the running loop
                    async def _init_and_run():
                        global _tg_ready
                        result = await init_telegram()
                        if result:
                            _tg_ready = True
                        else:
                            init_error[0] = "init_telegram returned False"

                    fut = asyncio.ensure_future(_init_and_run(), loop=_tg_loop)
                    # Wait for init to complete
                    while not fut.done():
                        _tg_loop.call_soon(_tg_loop.stop)
                        try:
                            _tg_loop.run_forever()
                        except Exception:
                            pass
                except Exception as e:
                    init_error[0] = str(e)
                finally:
                    init_done.set()
                    # Keep the loop running for future sends
                    try:
                        _tg_loop.run_forever()
                    except Exception:
                        pass

            _tg_thread = threading.Thread(target=runner, daemon=True)
            _tg_thread.start()

            # Wait up to 15s for init
            if init_done.wait(timeout=15):
                if init_error[0]:
                    logger.error(f"Telegram init failed: {init_error[0]}")
                    _tg_loop = None
                    _tg_ready = False
                    return None
                if _tg_ready:
                    logger.info(f"Telegram ready: _tg_ready={_tg_ready}")
            else:
                logger.error("Telegram init timed out after 15s")
                _tg_loop = None
                _tg_ready = False
                return None
            return _tg_loop
        except Exception as e:
            logger.error(f"Telegram init outer failed: {e}")
            return None


def _enqueue_telegram_event(event_type, symbol, side, entry, new_sl, r, peak_r,
                             old_sl=0.0, current_price=0.0, leverage=1, entry_time=0):
    """Fire Guardian event (BE/Partial/Tight) to Telegram in background thread.

    Direct async call via run_coroutine_threadsafe (no separate worker process).
    """
    _ensure_telegram_started()
    if not _tg_ready:
        logger.warning(f"TG fire EVENT skipped (not ready): {symbol}")
        return
    import threading
    def runner():
        try:
            from tradingos.guardian.telegram_notifier import send_guardian_event
            fut = asyncio.run_coroutine_threadsafe(
                send_guardian_event(
                    symbol=symbol, event_type=event_type,
                    current_sl=new_sl, entry_price=entry, peak_r=peak_r,
                    old_sl=old_sl, current_price=current_price,
                    side=side, leverage=leverage, entry_time=entry_time,
                ),
                _tg_loop
            )
            fut.result(timeout=30)
            logger.info(f"TG {event_type} sent for {symbol}")
        except Exception as e:
            logger.error(f"Telegram event send failed: {e}")
    threading.Thread(target=runner, daemon=True).start()


def _enqueue_telegram_open(symbol, side, entry_price, qty, sl, tp, reason="", leverage=1, entry_time=0):
    """OPEN-карточка теперь шлётся из observation-цикла (там есть prob/score/adx).

    Здесь НЕ дублируем — иначе в Telegram приходит два одинаковых уведомления.
    Оставлена для обратной совместимости (сигнатура сохраняется).
    """
    logger.info(f"TG open skipped (observation loop sends it): {symbol} {side}")


def _enqueue_telegram_timeout(symbol, side, hold_hours):
    """Fire TIMEOUT alert to Telegram in background thread."""
    _ensure_telegram_started()
    if not _tg_ready:
        logger.warning(f"TG fire TIMEOUT skipped (not ready): {symbol}")
        return
    import threading
    def runner():
        try:
            from tradingos.guardian.telegram_notifier import send_timeout_alert
            fut = asyncio.run_coroutine_threadsafe(
                send_timeout_alert(symbol=symbol, hours=hold_hours),
                _tg_loop
            )
            fut.result(timeout=30)
            logger.info(f"TG timeout sent for {symbol}")
        except Exception as e:
            logger.error(f"Telegram timeout send failed: {e}")
    threading.Thread(target=runner, daemon=True).start()


# FIX 2026-08-31: дедуп phantom-уведомлений (не чаще 1/15 мин на символ)
_phantom_alert_ts: dict = {}


def _enqueue_telegram_phantom(symbol: str):
    """Fire PHANTOM CLOSE alert to Telegram (position missing from poll but alive on exchange)."""
    _ensure_telegram_started()
    if not _tg_ready:
        logger.warning(f"TG PHANTOM skipped (not ready): {symbol}")
        return
    import threading
    def runner():
        try:
            from tradingos.guardian.telegram_notifier import get_raw_send
            fut = asyncio.run_coroutine_threadsafe(
                get_raw_send(
                    f"👻 <b>PHANTOM CLOSE</b>\n"
                    f"{symbol} исчез из опроса, но жив на бирже.\n"
                    f"Закрытие НЕ записано. Проверьте вручную."
                ),
                _tg_loop
            )
            fut.result(timeout=30)
            logger.info(f"TG PHANTOM sent for {symbol}")
        except Exception as e:
            logger.error(f"Telegram phantom send failed: {e}")
    threading.Thread(target=runner, daemon=True).start()


def _enqueue_telegram_close(symbol, side, entry_price, exit_price, qty, pnl,
                              fees, holding_hours, reason, sl=0.0, tp=0.0,
                              mfe_r=0.0, mae_r=0.0, entry_time=0, exit_time=0,
                              entry_price_raw=None, exit_price_raw=None):
    """Fire trade close to Telegram in background thread.

    Direct async call with send_trade_close → notify_trade_close generates chart+caption.
    """
    _ensure_telegram_started()
    if not _tg_ready:
        logger.warning(f"TG fire CLOSE skipped (not ready): {symbol}")
        return
    import threading
    def runner():
        try:
            from tradingos.guardian.telegram_notifier import send_trade_close
            fut = asyncio.run_coroutine_threadsafe(
                send_trade_close(
                    symbol=symbol, side=side,
                    entry_price=entry_price, exit_price=exit_price,
                    qty=qty, pnl=pnl, fees=fees,
                    holding_hours=holding_hours, reason=reason,
                    sl=sl, tp=tp,
                    mfe_r=mfe_r, mae_r=mae_r,
                    entry_time=entry_time, exit_time=exit_time,
                    entry_price_raw=entry_price_raw, exit_price_raw=exit_price_raw
                ),
                _tg_loop
            )
            fut.result(timeout=60)  # 60s for chart gen + TG send
            logger.info(f"TG close sent for {symbol}")
            # Structured monitoring log (per user suggestion)
            logger.info(
                f"TRADE_CLOSE_NOTIFICATION "
                f"symbol={symbol} side={side} "
                f"entry={entry_price} exit={exit_price} sl={sl} "
                f"pnl={pnl} holding_hours={holding_hours} "
                f"status=SUCCESS"
            )
        except Exception as e:
            logger.error(f"Telegram close send failed: {e}")
            logger.error(
                f"TRADE_CLOSE_NOTIFICATION "
                f"symbol={symbol} side={side} "
                f"entry={entry_price} exit={exit_price} "
                f"status=FAILED reason={type(e).__name__}:{e}"
            )
    threading.Thread(target=runner, daemon=True).start()


def _process_position(pos, state):
    """Process one position for Guardian rules."""
    symbol = pos["symbol"]
    side = pos["side"]
    size = float(pos["size"])
    entry = float(pos["avgPrice"])
    sl = _safe_float(pos.get("stopLoss", 0))
    tp = _safe_float(pos.get("takeProfit", 0))
    current = _get_ticker(symbol)
    if current == 0:
        current = float(pos.get("markPrice", entry))

    # Compute R-multiple based on risk from SL.
    # CRITICAL FIX 2026-08-03: use ORIGINAL entry-to-SL risk, NOT current SL.
    # After BE fires, SL moves to entry → abs(entry - sl) ≈ 0 → r_multiple explodes
    # (PLUME showed 19.6R instead of ~1.5R). entry_to_sl_risk is stored on first
    # sight of the position and never changes.
    sym_state_pre = state.get(symbol)
    original_risk = None
    if isinstance(sym_state_pre, dict):
        original_risk = sym_state_pre.get("entry_to_sl_risk", 0)
    risk_per_unit = original_risk if original_risk and original_risk > 0 else (
        abs(entry - sl) if sl > 0 else abs(entry - tp) / 2 if tp > 0 else 0
    )
    if risk_per_unit <= 0:
        return state  # Return current state, NOT None

    # For SELL: profit = entry - current; for BUY: profit = current - entry
    if side == "Sell":
        profit = entry - current
    else:
        profit = current - entry

    r_multiple = profit / risk_per_unit

    # Per-symbol state (with defensive checks)
    sym_state = state.get(symbol)
    is_new_position = sym_state is None or not isinstance(sym_state, dict)
    if is_new_position:
        sym_state = {"be_fired": False, "partial_fired": False, "tight_fired": False, "mfe_peak": 0.0}
        # Задача 1: пометка источника — MANUAL-позиции зарегистрированы с
        # source="MANUAL" (см. manual_signal._place_market_order). Если state
        # создаётся guardian'ом впервые, но символ есть в manual журнале — тоже MANUAL.
        if sym_state.get("source") != "MANUAL":
            try:
                _mj = Path("/root/tradingos/memory/manual_signals.jsonl")
                if _mj.exists():
                    for _l in reversed(_mj.read_text(errors="replace").splitlines()):
                        if not _l.strip():
                            continue
                        try:
                            _r = json.loads(_l)
                        except Exception:
                            continue
                        if _r.get("symbol") == symbol and _r.get("event") == "EXECUTED":
                            sym_state["source"] = "MANUAL"
                            break
            except Exception:
                pass

    # Store initial risk per unit for later "saved amount" calculation
    sym_state["entry_to_sl_risk"] = risk_per_unit
    sym_state["side"] = side
    sym_state["entry"] = entry
    sym_state["size"] = size
    # Сохраняем исходные SL/TP для честной классификации закрытия по факту
    # (2026-08-08: раньше outcome считался эвристикой по entry/close, из-за чего
    # ручное закрытие ACX классифицировалось как ложный "TP").
    sym_state["sl_initial"] = sl
    sym_state["tp_initial"] = tp
    # Store entry_time for chart generation
    # FIX 2026-08-04: use openTime (real position open time), NOT createdTime.
    # Bybit createdTime = position-id creation (artifact from past VETUSDT trades
    # on same account) → hold_hours computed as 124.5h for a position opened today.
    open_time = float(pos.get("openTime", 0) or 0) / 1000
    if open_time <= 0:
        open_time = float(pos.get("createdTime", 0) or 0) / 1000
    if open_time <= 0:
        open_time = time.time()
    sym_state["entry_time"] = open_time

    # Read real leverage from position
    try:
        real_leverage = int(float(pos.get("leverage", 1)))
    except (ValueError, TypeError):
        real_leverage = 1

    # Send OPEN notification on first sight
    if is_new_position:
        _enqueue_telegram_open(
            symbol=symbol, side=side,
            entry_price=entry, qty=size,
            sl=sl, tp=tp, reason="OPEN",
            leverage=real_leverage, entry_time=open_time
        )
        logger.info(f"🟢 OPEN detected: {symbol} {side} @ {entry} qty={size} lev={real_leverage}x SL={sl} TP={tp}")

    # Update MFE peak (max profit reached)
    if r_multiple > sym_state.get("mfe_peak", 0):
        sym_state["mfe_peak"] = r_multiple
    
    # Update MAE trough (max adverse excursion)
    if r_multiple < sym_state.get("mae_trough", 0):
        sym_state["mae_trough"] = r_multiple

    actions = []

    # ── Rule 3.5a: SOFT-SL RECOVERY (за флагом soft_sl_recovery, MANUAL-first) ──
    # Когда цена касается расчётного sl_soft — не закрываем сразу: при подтверждённом
    # сигнале отката (rebound + wick/oversold) даём окно RECOVERY_BARS на восстановление.
    # Без подтверждения — обычный SL (SOFT_SL). Пол = FLOOR_BUFFER_R ниже цены arm'а.
    # Зеркало giveback-TAKE (Rule 3.5b) для убыточной стороны. Флаг OFF → поведения нет.
    recovery_active = False
    try:
        _msp = Path("/root/tradingos/operations/manual_session.json")
        if _msp.exists():
            _soft_sl_recovery = bool(json.load(open(_msp)).get("soft_sl_recovery", False))
        else:
            _soft_sl_recovery = False
    except Exception:
        _soft_sl_recovery = False
    # S1 fix: после ARM recovery_attempted=True, но recovery_state ещё активен.
    # Старое условие `not recovery_attempted` пропускало весь блок → оценка
    # RECOVERED/FLOOR/TIMEOUT (else-ветка ниже) никогда не выполнялась.
    # Теперь блок выполняется также при активном recovery_state. Arm-часть
    # защищена `if recovery is None` (стр. 782) → повторного arm не будет.
    if _soft_sl_recovery and (
        not sym_state.get("recovery_attempted", False)
        or sym_state.get("recovery_state") is not None
    ):
        sl_soft = sym_state.get("sl_soft", 0) or sl or 0
        sl_hard = sym_state.get("sl_hard", 0) or 0
        # FIX 2026-08-31 (owner: не зарубать рано): reality-позиции ставятся без
        # sl_hard (только trade_executor → SL). Для них hard = soft (не расширяем
        # стоп, но даём recovery-окно на той же цене).
        if sl_soft > 0 and sl_hard == 0:
            sl_hard = sl_soft
        is_buy = str(side).lower().startswith("buy")
        if sl_soft > 0:
            touch = (current <= sl_soft) if is_buy else (current >= sl_soft)
            # Wick-возврат: цена уже вернулась за sl_soft, но последняя свеча его
            # проткнула — именно этот случай «проткнуло и отскочило» ловим.
            _wick_touch = False
            _df = _load_m15_df(symbol)
            if _df is not None and len(_df) >= 1:
                _last = _df.iloc[-1]
                _wick_touch = (float(_last["l"]) <= sl_soft) if is_buy else (float(_last["h"]) >= sl_soft)
            if touch or _wick_touch:
                recovery = sym_state.get("recovery_state")
                if recovery is None:
                    cond = _recovery_condition(symbol, side, sl_soft, current)
                    if cond and sl_hard > 0:
                        floor = (current - FLOOR_BUFFER_R * risk_per_unit
                                 if is_buy else current + FLOOR_BUFFER_R * risk_per_unit)
                        # Расширяем exchange SL до safety net (MarkPrice игнорирует wicks)
                        if _set_trading_stop(symbol, stop_loss=sl_hard, sl_trigger_by="MarkPrice"):
                            sym_state["recovery_state"] = {
                                "armed_at": time.time(),
                                "floor": floor,
                                "deadline": time.time() + RECOVERY_BARS * RECOVERY_BAR_SEC,
                                "condition": cond,
                                "r_at_arm": r_multiple,
                            }
                            sym_state["recovery_attempted"] = True
                            recovery_active = True
                            actions.append(
                                f"SOFT-SL RECOVERY armed ({cond}) floor={floor:.6g} "
                                f"deadline={RECOVERY_BARS*RECOVERY_BAR_SEC/60:.0f}min"
                            )
                            logger.warning(
                                f"🛟 SOFT-SL RECOVERY armed: {symbol} {side} cond={cond} "
                                f"sl_soft={sl_soft:.6g} floor={floor:.6g}"
                            )
                            _log_recovery_event({
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "symbol": symbol, "side": side, "event": "ARM",
                                "condition": cond, "r_at_event": round(r_multiple, 3),
                                "price": current, "sl_soft": sl_soft, "sl_hard": sl_hard,
                                "floor": floor, "source": sym_state.get("source", "?"),
                            })
                        else:
                            # Не смогли расширить SL — закрываем как обычный SL
                            if _close_position(symbol, side, size):
                                sym_state["recovery_attempted"] = True
                                sym_state["recovery_outcome"] = "SOFT_SL"
                                _log_recovery_event({
                                    "ts": datetime.now(timezone.utc).isoformat(),
                                    "symbol": symbol, "side": side, "event": "SOFT_SL",
                                    "r_at_event": round(r_multiple, 3), "price": current,
                                    "sl_soft": sl_soft, "sl_hard": sl_hard,
                                    "condition": None, "source": sym_state.get("source", "?"),
                                })
                                actions.append("SOFT-SL: нет recovery (не удалось расширить SL) → closed")
                                logger.warning(f"🛑 SOFT-SL {symbol}: recovery невозможно (set_trading_stop fail), closed")
                                return state
                    else:
                        # Условие не выполнено → обычный SL
                        if _close_position(symbol, side, size):
                            sym_state["recovery_attempted"] = True
                            sym_state["recovery_outcome"] = "SOFT_SL"
                            _log_recovery_event({
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "symbol": symbol, "side": side, "event": "SOFT_SL",
                                "r_at_event": round(r_multiple, 3), "price": current,
                                "sl_soft": sl_soft, "sl_hard": sl_hard,
                                "condition": None, "source": sym_state.get("source", "?"),
                            })
                            actions.append("SOFT-SL: касание sl_soft без recovery-условия → closed")
                            logger.warning(f"🛑 SOFT-SL {symbol}: {side} touch sl_soft={sl_soft:.6g}, нет условия отката, closed")
                            return state
                else:
                    # Recovery armed — оцениваем исходы каждый poll
                    now = time.time()
                    rec_floor = float(recovery.get("floor", 0) or 0)
                    deadline = float(recovery.get("deadline", 0) or 0)
                    recovered = (current >= entry) if is_buy else (current <= entry)
                    hit_floor = (current <= rec_floor) if is_buy else (current >= rec_floor)
                    cond = recovery.get("condition", "?")
                    if recovered:
                        sym_state["recovery_state"] = None
                        sym_state["recovery_outcome"] = "RECOVERED"
                        recovery_active = False
                        actions.append(f"SOFT-SL RECOVERY: RECOVERED (r={r_multiple:+.2f})")
                        logger.info(f"🛟 {symbol} recovery RECOVERED (r={r_multiple:+.2f})")
                        _log_recovery_event({
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": symbol, "side": side, "event": "RECOVERED",
                            "condition": cond, "r_at_event": round(r_multiple, 3),
                            "price": current, "sl_soft": sl_soft, "sl_hard": sl_hard,
                            "floor": rec_floor, "source": sym_state.get("source", "?"),
                        })
                    elif hit_floor:
                        if _close_position(symbol, side, size):
                            sym_state["recovery_attempted"] = True
                            sym_state["recovery_outcome"] = "RECOVERY_FLOOR"
                            _log_recovery_event({
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "symbol": symbol, "side": side, "event": "RECOVERY_FLOOR",
                                "condition": cond, "r_at_event": round(r_multiple, 3),
                                "price": current, "sl_soft": sl_soft, "sl_hard": sl_hard,
                                "floor": rec_floor, "source": sym_state.get("source", "?"),
                            })
                            actions.append(f"SOFT-SL RECOVERY: FLOOR hit ({rec_floor:.6g}) → closed")
                            logger.warning(f"🛑 {symbol} recovery FLOOR hit, closed (r={r_multiple:+.2f})")
                            return state
                    elif now > deadline:
                        if _close_position(symbol, side, size):
                            sym_state["recovery_attempted"] = True
                            sym_state["recovery_outcome"] = "RECOVERY_TIMEOUT"
                            _log_recovery_event({
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "symbol": symbol, "side": side, "event": "RECOVERY_TIMEOUT",
                                "condition": cond, "r_at_event": round(r_multiple, 3),
                                "price": current, "sl_soft": sl_soft, "sl_hard": sl_hard,
                                "floor": rec_floor, "source": sym_state.get("source", "?"),
                            })
                            actions.append(f"SOFT-SL RECOVERY: TIMEOUT ({RECOVERY_BARS*RECOVERY_BAR_SEC/60:.0f}min) → closed")
                            logger.warning(f"🛑 {symbol} recovery TIMEOUT, closed (r={r_multiple:+.2f})")
                            return state
                    else:
                        # Продолжаем ждать откат — подавляем BE/Partial/Tight/Trail
                        recovery_active = True

    # Rule 1: BE — гибридный порог (2026-08-30 owner fix)
    # Старый порог 0.8R требовал движения = 80% дистанции до SL. Для широких
    # стопов (PUMPFUN: SL 3.86% от входа) это 3% цены — недостижимо, хотя
    # +1.5% уже в кармане. Новый порог: срабатывает при max(0.4R, +0.5% цены).
    # 0.5% цены отсекает шум, 0.4R — не даёт BE забыть на тесных стопах.
    # Управляется константой BE_THRESHOLD (0.4) и BE_MIN_PRICE_PCT (0.5).
    _be_r = float(BE_THRESHOLD)
    _be_pct = float(BE_MIN_PRICE_PCT)
    try:
        _be_cfg = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
        _be_r = float(_be_cfg.get("be_threshold_r", BE_THRESHOLD) or BE_THRESHOLD)
        _be_pct = float(_be_cfg.get("be_min_price_pct", BE_MIN_PRICE_PCT) or BE_MIN_PRICE_PCT)
    except Exception:
        pass
    _mfe_pct_price = (sym_state["mfe_peak"] * risk_per_unit) / entry * 100 if entry and risk_per_unit else 0.0
    _be_triggered = (sym_state["mfe_peak"] >= _be_r) or (_mfe_pct_price >= _be_pct)
    if not recovery_active and _be_triggered and not sym_state.get("be_fired", False):
        # Move SL к безубытку с ЗАХВАТОМ части прибыли (2026-08-31 owner fix).
        # Проблема A (2026-08-30): SL=entry платит комиссии → NET<0 (ZKP −$2.7).
        # Проблема B (2026-08-31): SL=entry выбивает в ноль при лёгком откате,
        #   хотя пик был высокий (SIRENUSDT: пик 1.14R → выход +$0.11).
        # Решение: SL = entry + BE_LOCK_FRACTION × зафиксированный доход
        #   (30% от пика в R). Позиция отыгрывается без выбивания в ноль,
        #   и даже при откате фиксируем часть прибыли.
        fee_pct = 0.0022  # 0.22% round-trip (taker open+close, подтверждено CLOUSDT/VELVET)
        try:
            _mfee = json.loads(Path("/root/tradingos/operations/manual_session.json").read_text())
            fee_pct = float(_mfee.get("funding_fee_round_trip_pct", 0.22) or 0.22) / 100.0
        except Exception:
            pass
        fee_buffer = entry * fee_pct  # комиссия в цене
        peak_r = sym_state["mfe_peak"]
        # Доход в цене = peak_r × risk_per_unit; захватываем BE_LOCK_FRACTION от него
        lock_price = peak_r * risk_per_unit * float(BE_LOCK_FRACTION)
        if side == "Sell":
            # SELL: SL выше entry на (комиссию + захват)
            new_sl = entry + fee_buffer + lock_price
        else:
            # BUY: SL ниже entry на (комиссию + захват)
            new_sl = entry - fee_buffer - lock_price
        # FIX (audit explore-1 HIGH): BE — только-вперёд (PARTIAL/trail не откатывает).
        cur_sl_ex = float(pos.get("stopLoss", 0) or 0)
        be_forward = (side == "Buy" and new_sl > cur_sl_ex) or \
                     (side == "Sell" and new_sl < cur_sl_ex) or cur_sl_ex == 0
        if not be_forward:
            sym_state["be_fired"] = True  # SL уже лучше — поглощено
            sym_state["be_sl"] = cur_sl_ex  # BE-уровень = текущий SL (не откатывать ниже)
            actions.append(f"BE absorbed (SL already better: {cur_sl_ex:.6f})")
        elif _set_trading_stop(symbol, stop_loss=new_sl):
            sym_state["be_fired"] = True
            sym_state["be_fired_at"] = time.time()
            sym_state["be_sl"] = new_sl  # запоминаем BE-уровень для трейлинга (не откатывать ниже)
            actions.append(f"Moved SL to breakeven+lock at +{r_multiple:.2f}R "
                           f"(SL={new_sl:.5f}, lock {BE_LOCK_FRACTION*100:.0f}% от пика {peak_r:.2f}R)")
            alert = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "GUARDIAN_BREAKEVEN",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "r_multiple": round(r_multiple, 3),
                "new_sl": new_sl,
                "action": "SL moved to breakeven",
            }
            _log_profit_alert(alert)
            logger.info(f"🟢 BE fired: {symbol} {side} at +{r_multiple:.2f}R, SL→{new_sl:.5f}")
            # Telegram notification (enqueue for guaranteed delivery)
            _enqueue_telegram_event("BE", symbol, side, entry, new_sl, r_multiple, sym_state.get("mfe_peak", 0),
                                     old_sl=sl, current_price=current, leverage=real_leverage, entry_time=open_time)

    # Rule 1.5: Partial TP — закрыть 50% позиции на +1.0R (T5)
    try:
        partial_tp_cfg = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text()).get("partial_tp", {})
        if partial_tp_cfg.get("enabled"):
            pt_trigger = float(partial_tp_cfg.get("trigger_r", 1.0))
            pt_fraction = float(partial_tp_cfg.get("close_fraction", 0.5))
            if not sym_state.get("partial_tp_fired", False) and sym_state["mfe_peak"] >= pt_trigger:
                size = float(pos.get("size", 0))
                if size > 0:
                    close_qty = size * pt_fraction
                    if _create_reduce_only_order(symbol, side, close_qty):
                        sym_state["partial_tp_fired"] = True
                        sym_state["partial_tp_qty"] = close_qty
                        actions.append(f"PARTIAL TP: closed {pt_fraction*100:.0f}% ({close_qty:.4f}) at +{r_multiple:.2f}R")
                        logger.info(f"💰 PARTIAL TP fired: {symbol} {side} +{r_multiple:.2f}R, closed {close_qty:.4f} ({pt_fraction*100:.0f}%)")
                        _enqueue_telegram_event("PARTIAL_TP", symbol, side, entry, sl, r_multiple, sym_state.get("mfe_peak", 0),
                                                 old_sl=sl, current_price=current, leverage=real_leverage, entry_time=open_time)
    except Exception as e:
        logger.debug(f"partial_tp check failed: {e}")

    # Rule 2: Partial lock at +1.0R (SL = entry + 0.5*ATR/2)
    if not recovery_active and sym_state["mfe_peak"] >= PARTIAL_THRESHOLD and not sym_state.get("partial_fired", False):
        # Move SL to entry + partial-risk offset
        partial_offset = risk_per_unit * 0.5
        if side == "Sell":
            new_sl = entry - partial_offset  # SELL: SL ниже entry (фиксация прибыли)
        else:
            new_sl = entry + partial_offset  # LONG: SL выше entry (фиксация прибыли)
        # 2026-08-30: ступень НЕ должна откатывать SL назад, если трейлинг
        # (многоступенчатый, активен после BE) уже поднял SL выше.
        cur_sl_ex = float(pos.get("stopLoss", 0))
        steps_forward = (side == "Buy" and new_sl > cur_sl_ex) or \
                        (side == "Sell" and new_sl < cur_sl_ex) or cur_sl_ex == 0
        if not steps_forward:
            sym_state["partial_fired"] = True  # ступень "поглощена" трейлингом
            actions.append(f"PARTIAL absorbed by trail (SL already above {cur_sl_ex:.6f})")
        elif _set_trading_stop(symbol, stop_loss=new_sl):
            sym_state["partial_fired"] = True
            sym_state["partial_fired_at"] = time.time()
            actions.append(f"Moved SL to partial lock +{partial_offset:.6f} at +{r_multiple:.2f}R")
            alert = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "GUARDIAN_PARTIAL",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "r_multiple": round(r_multiple, 3),
                "new_sl": new_sl,
                "action": "SL moved to partial lock",
            }
            _log_profit_alert(alert)
            logger.info(f"🟡 PARTIAL fired: {symbol} {side} at +{r_multiple:.2f}R, SL→{new_sl:.5f}")
            # Telegram notification (enqueue for guaranteed delivery)
            _enqueue_telegram_event("PARTIAL", symbol, side, entry, new_sl, r_multiple, sym_state.get("mfe_peak", 0),
                                     old_sl=sl, current_price=current, leverage=real_leverage, entry_time=open_time)

    # Rule 3: Tight lock at +1.5R
    if not recovery_active and sym_state["mfe_peak"] >= TIGHT_THRESHOLD and not sym_state.get("tight_fired", False):
        tight_offset = risk_per_unit * 1.0
        if side == "Sell":
            new_sl = entry - tight_offset  # SELL: SL ниже entry
        else:
            new_sl = entry + tight_offset  # LONG: SL выше entry
        # 2026-08-30: не откатываем SL назад, если трейлинг его уже поднял выше.
        cur_sl_ex2 = float(pos.get("stopLoss", 0))
        steps_forward2 = (side == "Buy" and new_sl > cur_sl_ex2) or \
                         (side == "Sell" and new_sl < cur_sl_ex2) or cur_sl_ex2 == 0
        if not steps_forward2:
            sym_state["tight_fired"] = True  # поглощено трейлингом
            actions.append(f"TIGHT absorbed by trail (SL already above {cur_sl_ex2:.6f})")
        elif _set_trading_stop(symbol, stop_loss=new_sl):
            sym_state["tight_fired"] = True
            sym_state["tight_fired_at"] = time.time()
            actions.append(f"Moved SL to tight lock +{tight_offset:.6f} at +{r_multiple:.2f}R")
            alert = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "GUARDIAN_TIGHT",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "r_multiple": round(r_multiple, 3),
                "new_sl": new_sl,
                "action": "SL moved to tight lock",
            }
            _log_profit_alert(alert)
            logger.info(f"🟢 TIGHT fired: {symbol} {side} at +{r_multiple:.2f}R, SL→{new_sl:.5f}")
            # Telegram notification (enqueue for guaranteed delivery)
            _enqueue_telegram_event("TIGHT", symbol, side, entry, new_sl, r_multiple, sym_state.get("mfe_peak", 0),
                                     old_sl=sl, current_price=current, leverage=real_leverage, entry_time=open_time)

    # Rule 3.5: Trailing stop (многоступенчатый)
    # Активен ПОСЛЕ BE (пик ≥0.8R), не после TIGHT — непрерывно следует
    # за пиком с дистанцией TRAIL_DISTANCE_R и шагом TRAIL_MIN_STEP_R.
    trail_active_ok = sym_state.get("be_fired", False) if TRAIL_START_AFTER_BE \
        else sym_state.get("tight_fired", False)
    if TRAILING_ENABLED and not recovery_active and trail_active_ok:
        peak_r = sym_state.get("mfe_peak", 0)
        last_trail_peak_r = sym_state.get("trail_last_peak_r", 0)
        # FIX 2026-08-31 (bad trail): при мизерном пике (< дистанции трейлинга)
        # отступ больше пика → SL уходит НИЖЕ точки безубытка (ZKP: пик 0.06R,
        # трейлинг −0.25R → стоп 0.05069 < вход 0.0517 — «безубыток» = −2%).
        # Трейлинг имеет смысл только когда пик >= дистанции (иначе BE уже
        # зафиксировал лучшее).
        if peak_r < TRAIL_DISTANCE_R:
            pass  # пик слишком мал — трейлинг не нужен, BE уже защитил
        elif peak_r - last_trail_peak_r >= TRAIL_MIN_STEP_R:
            trail_distance_price = risk_per_unit * TRAIL_DISTANCE_R
            # New SL = peak - TRAIL_DISTANCE_R (in price units, on the favorable side)
            if side == "Sell":
                # SELL: profit when price goes DOWN → peak is below entry
                # SL sits trail_distance ABOVE the current price (or peak)
                peak_price = entry - peak_r * risk_per_unit
                new_trail_sl = peak_price + trail_distance_price
            else:
                # BUY: profit when price goes UP → peak is above entry
                peak_price = entry + peak_r * risk_per_unit
                new_trail_sl = peak_price - trail_distance_price
            # FLAT: трейлинг не может опустить SL ниже BE-уровня (зафиксирован
            # при BE) — иначе «безубыток» снова превращается в убыток.
            be_floor = float(sym_state.get("be_sl", 0) or 0)
            if be_floor > 0:
                if side == "Buy":
                    new_trail_sl = max(new_trail_sl, be_floor)
                else:
                    new_trail_sl = min(new_trail_sl, be_floor)
            # Only move SL forward (never backwards)
            current_sl_on_exchange = float(pos.get("stopLoss", 0))
            only_forward = (
                (side == "Buy" and new_trail_sl > current_sl_on_exchange) or
                (side == "Sell" and new_trail_sl < current_sl_on_exchange) or
                current_sl_on_exchange == 0
            )
            if only_forward and new_trail_sl > 0:
                new_tp = None
                # TP двигаем ТОЛЬКО если он был установлен изначально
                # (SL-only модели — funding/DN-sweep — TP создавать нельзя,
                # это ломает валидированный exit).
                tp_existed = float(sym_state.get("tp_initial") or 0) > 0
                if TRAIL_MOVE_TP and tp_existed:
                    if side == "Sell":
                        new_tp = peak_price - risk_per_unit * TRAIL_TP_DISTANCE_R
                    else:
                        new_tp = peak_price + risk_per_unit * TRAIL_TP_DISTANCE_R
                if _set_trading_stop(symbol, stop_loss=new_trail_sl, take_profit=new_tp):
                    sym_state["trail_last_peak_r"] = peak_r
                    sym_state["trail_last_sl"] = new_trail_sl
                    sym_state["trail_last_tp"] = new_tp if new_tp else 0
                    actions.append(
                        f"Trail SL→{new_trail_sl:.6f} (peak {peak_r:.2f}R)"
                        + (f" TP→{new_tp:.6f}" if new_tp else "")
                    )
                    alert = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "type": "GUARDIAN_TRAIL",
                        "symbol": symbol,
                        "side": side,
                        "entry": entry,
                        "r_multiple": round(peak_r, 3),
                        "new_sl": new_trail_sl,
                        "new_tp": new_tp,
                        "action": "Trailing stop moved",
                    }
                    _log_profit_alert(alert)
                    logger.info(
                        f"📈 TRAIL fired: {symbol} {side} peak +{peak_r:.2f}R, "
                        f"SL→{new_trail_sl:.6f}"
                        + (f" TP→{new_tp:.6f}" if new_tp else "")
                    )
                    _enqueue_telegram_event(
                        "TRAIL", symbol, side, entry, new_trail_sl, peak_r, peak_r,
                        old_sl=current_sl_on_exchange, current_price=current,
                        leverage=real_leverage, entry_time=open_time,
                    )

    # Rule 3.6: NEAR-TP CLOSE (2026-08-30, owner request).
    # Проблема: BE/PARTIAL/TIGHT двигают SL к entry и выше, но откат у TP
    # съедает прибыль от entry до текущей цены (SL=entry+0.5R, цена у TP,
    # разворот → фиксируем $0 вместо почти-TP). Когда цена дошла до
    # NEAR_TP_CLOSE_R пути до TP — закрываем по рынку сами.
    # open_time/hold_hours нужны для telegram-карточки закрытия; вычисляем
    # здесь же (ниже они пересчитываются повторно — это нормально).
    _ot_n = float(pos.get("openTime", 0) or 0) / 1000
    if _ot_n <= 0:
        _ot_n = float(pos.get("createdTime", 0) or 0) / 1000
    if _ot_n <= 0:
        _ot_n = time.time()
    if (NEAR_TP_CLOSE_ENABLED and not recovery_active
            and not sym_state.get("near_tp_closed", False)):
        # Путь до TP в R (target_r), текущий r_multiple
        tp_px = sym_state.get("tp_initial") or pos.get("takeProfit") or 0
        try:
            tp_px = float(tp_px)
        except (TypeError, ValueError):
            tp_px = 0.0
        if tp_px and tp_px > 0 and risk_per_unit and risk_per_unit > 0:
            if side == "Buy":
                target_r = (tp_px - entry) / risk_per_unit
                near_tp = (r_multiple >= target_r * NEAR_TP_CLOSE_R)
            else:
                target_r = (entry - tp_px) / risk_per_unit
                near_tp = (r_multiple >= target_r * NEAR_TP_CLOSE_R)
            if near_tp and target_r > 0:
                if _close_position(symbol, side, size):
                    sym_state["near_tp_closed"] = True
                    sym_state["near_tp_closed_at"] = time.time()
                    actions.append(
                        f"NEAR-TP CLOSE: {r_multiple:.2f}R / target {target_r:.2f}R "
                        f"({r_multiple/target_r*100:.0f}% пути) — фикс. прибыль до отката"
                    )
                    logger.info(
                        f"🎯 NEAR-TP CLOSE {symbol} {side}: {r_multiple:.2f}R "
                        f"(target {target_r:.2f}R), closed at market"
                    )
                    _enqueue_telegram_close(
                        symbol, side, entry, current, size,
                        pnl=profit, fees=0.0,
                        holding_hours=(time.time() - _ot_n) / 3600,
                        reason="NEAR_TP",
                    )

    # Rule 3.7: PEAK-REVERSAL EXIT (2026-08-31, owner вводная)
    # «TP в зоне недостижимости — если ждать, теряем 90% пика».
    # Позиция дошла до значимого максимума (>= PEAK_MIN_R), а потом откатилась
    # на >= PEAK_REVERSAL_GIVEBACK от пика → разворот начался, TP скорее всего
    # недостижим. Фиксируем по рынку ТЕКУЩУЮ прибыль, не дожидаясь ни TP,
    # ни трейлинга (который выпустит до пика − 0.25R, а не до цены разворота).
    # В отличие от трейлинга (провоцирует стоп на шуме), здесь реагируем на
    # фактический откат от локального пика.
    if (PEAK_REVERSAL_ENABLED and not recovery_active
            and not sym_state.get("peak_reversal_closed", False)
            and not sym_state.get("near_tp_closed", False)):
        _peak = sym_state.get("mfe_peak", 0.0)
        _cur = r_multiple
        if _peak >= float(PEAK_MIN_R):
            _giveback = _peak - _cur
            if _giveback >= float(PEAK_REVERSAL_GIVEBACK) and _cur > 0:
                if _close_position(symbol, side, size):
                    sym_state["peak_reversal_closed"] = True
                    sym_state["peak_reversal_at"] = time.time()
                    actions.append(
                        f"PEAK-REVERSAL: пик {_peak:.2f}R → откат {_giveback:.2f}R, "
                        f"зафиксировано {_cur:+.2f}R (TP {float(sym_state.get('tp_initial') or 0):.6g} недостижим)"
                    )
                    logger.warning(
                        f"🔄 PEAK-REVERSAL {symbol} {side}: пик {_peak:.2f}R → "
                        f"сейчас {_cur:+.2f}R (откат {_giveback:.2f}R) — закрыл по рынку"
                    )
                    _enqueue_telegram_close(
                        symbol, side, entry, current, size,
                        pnl=profit, fees=0.0,
                        holding_hours=(time.time() - _ot_n) / 3600,
                        reason="PEAK_REVERSAL",
                    )

    # Rule 3.5b: GIVEBACK-TAKE (за флагом engine_v2_live)
    # "Не отдавать победителя": позиция была прибыльной, но отдала существенную
    # часть MFE при ухудшении momentum → фиксируем. Пороги 0.7R/1.0R — те же,
    # что в engine_v2.profit_decision (не изобретены заново).
    # Активно ТОЛЬКО при engine_v2_live=true (иначе поведение Guardian не меняется).
    _engine_v2_live = False
    try:
        _mp = Path("/root/tradingos/operations/trading_mode.json")
        if _mp.exists():
            _engine_v2_live = bool(json.load(open(_mp)).get("engine_v2_live", False))
    except Exception:
        _engine_v2_live = False
    # open_time/hold_hours вычисляем здесь (используются в telegram-карточке).
    _ot = float(pos.get("openTime", 0) or 0) / 1000
    if _ot <= 0:
        _ot = float(pos.get("createdTime", 0) or 0) / 1000
    if _ot <= 0:
        _ot = time.time()
    open_time = _ot
    hold_hours = (time.time() - open_time) / 3600
    if _engine_v2_live and not recovery_active and not sym_state.get("giveback_closed", False):
        mfe_peak = sym_state.get("mfe_peak", 0.0)
        if mfe_peak >= 1.0:
            giveback = mfe_peak - r_multiple
            take = False
            if giveback >= 1.0:
                take = True
            elif giveback >= 0.7:
                # momentum decay: последние 3 закрытых бара против входа (грубая прокси)
                _mom = _momentum_decayed(symbol, side)
                take = bool(_mom)
            if take:
                if _close_position(symbol, side, size):
                    sym_state["giveback_closed"] = True
                    sym_state["giveback_at"] = time.time()
                    actions.append(
                        f"GIVEBACK-TAKE: mfe {mfe_peak:.2f}R -> {r_multiple:.2f}R "
                        f"(giveback {giveback:.2f}R)"
                    )
                    logger.warning(
                        f"💸 GIVEBACK-TAKE {symbol}: mfe {mfe_peak:.2f}R -> "
                        f"{r_multiple:.2f}R, closed at market"
                    )
                    _enqueue_telegram_close(
                        symbol, side, entry, current, size,
                        pnl=profit, fees=0.0, holding_hours=hold_hours,
                        reason="GIVEBACK_TAKE",
                    )

    # Rule 3.8: TIME-STOP for stale losers (2026-08-31).
    # Позиция висит в минусе без пика — она только занимает анти-слот
    # (корреляция блокирует новые SELL/BUY) и шансов нет (62% идут дальше
    # против). Закрываем сами, если: возраст ≥ TIME_STOP_HOURS И минус
    # ≥ TIME_STOP_MAE_R И пик был < TIME_STOP_PEAK_R.
    if (TIME_STOP_ENABLED and not recovery_active
            and not sym_state.get("time_stopped", False)
            and not sym_state.get("be_fired", False)):
        _st_mae = sym_state.get("mae_trough", 0.0) or 0.0
        _st_peak = sym_state.get("mfe_peak", 0.0) or 0.0
        if (hold_hours >= float(TIME_STOP_HOURS)
                and _st_mae <= float(TIME_STOP_MAE_R)
                and _st_peak < float(TIME_STOP_PEAK_R)):
            if _close_position(symbol, side, size):
                sym_state["time_stopped"] = True
                sym_state["time_stopped_at"] = time.time()
                actions.append(
                    f"TIME-STOP: {hold_hours:.1f}ч в минусе (mae {_st_mae:.2f}R, "
                    f"пик {_st_peak:.2f}R) — освобождаю слот"
                )
                logger.warning(
                    f"⏰ TIME-STOP {symbol} {side}: {hold_hours:.1f}ч, "
                    f"mae {_st_mae:.2f}R (пик {_st_peak:.2f}R) — закрыл по рынку"
                )
                _enqueue_telegram_close(
                    symbol, side, entry, current, size,
                    pnl=profit, fees=0.0, holding_hours=hold_hours,
                    reason="TIME_STOP",
                )

    # Rule 4: Timeout check (open_time heuristic)
    # FIX 2026-08-04: use openTime (real open) not createdTime (position-id artifact)
    if hold_hours > MAX_HOLD_HOURS and not sym_state.get("timeout_alerted", False):
        alert = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "GUARDIAN_TIMEOUT",
            "severity": "WARNING",
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "hold_hours": round(hold_hours, 1),
            "max_hours": MAX_HOLD_HOURS,
            "action": "Human review required",
        }
        _log_timeout_alert(alert)
        sym_state["timeout_alerted"] = True
        actions.append(f"Timeout alert: {hold_hours:.1f}h > {MAX_HOLD_HOURS}h")
        logger.warning(f"⏰ TIMEOUT: {symbol} {side} held {hold_hours:.1f}h")
        # Telegram notification (enqueue for guaranteed delivery)
        _enqueue_telegram_timeout(symbol, side, hold_hours)

    sym_state["last_check"] = time.time()
    sym_state["last_r"] = r_multiple
    state[symbol] = sym_state

    if actions:
        peak = sym_state.get("mfe_peak") or 0
        logger.info(f"📊 {symbol} {side}: R={r_multiple:+.3f} (peak {peak:+.3f}) — {len(actions)} action(s)")
    return state

TRADE_RESULTS_DIR = Path("/root/tradingos/logs/trades")
FINAL_TRADE_LOG = Path("/root/tradingos/guardian/guardian_effectiveness.jsonl")


def _fetch_actual_close_price(symbol: str, side: str, fallback: float) -> float:
    """Fetch actual exit price from Bybit closed PnL history."""
    try:
        ak, as_ = _load_credentials()
        if not ak or not as_:
            return fallback
        import hmac, hashlib, httpx
        ts = str(int(time.time() * 1000))
        q = f"category=linear&symbol={symbol}&limit=1"
        sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000",
        }
        r = httpx.get(f"{_API_BASE}/v5/position/closed-pnl?{q}", headers=headers, timeout=5)
        d = r.json()
        if d.get("retCode") == 0:
            items = d["result"].get("list", [])
            if items:
                exit_price = float(items[0].get("avgExitPrice", 0))
                if exit_price > 0:
                    return exit_price
    except Exception:
        pass
    return fallback


def _fetch_closed_trade(symbol: str, side: str) -> Optional[dict]:
    """Fetch the most recent closed trade for a symbol with REAL fees/PnL.

    Returns dict with: avgEntryPrice, avgExitPrice, qty, closedPnl, openFee,
    closeFee, execType, createdTime — or None if not found.
    Retries 3x with backoff (closed-pnl endpoint can lag the fill).
    """
    ak, as_ = _load_credentials()
    if not ak or not as_:
        return None
    import hmac, hashlib, httpx
    for attempt in range(3):
        try:
            ts = str(int(time.time() * 1000))
            q = f"category=linear&symbol={symbol}&limit=1"
            sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000",
            }
            r = httpx.get(f"{_API_BASE}/v5/position/closed-pnl?{q}", headers=headers, timeout=5)
            d = r.json()
            if d.get("retCode") == 0:
                items = d["result"].get("list", [])
                if items:
                    it = items[0]
                    return {
                        # FIX 2026-08-24: defend against Bybit returning "" for
                        # numeric fields (seen on qty/fees) — float('') raises and
                        # poisons the closure path. Treat empty as 0.
                        "avgEntryPrice": _safe_float(it.get("avgEntryPrice")),
                        "avgExitPrice":  _safe_float(it.get("avgExitPrice")),
                        "qty":           _safe_float(it.get("qty")),
                        "closedPnl":     _safe_float(it.get("closedPnl")),
                        "openFee":       _safe_float(it.get("openFee")),
                        "closeFee":      _safe_float(it.get("closeFee")),
                        "execType":      it.get("execType", ""),
                        "createdTime":   it.get("createdTime", 0),
                    }
            # retCode != 0 or empty list → retry with backoff
        except Exception:
            pass
        if attempt < 2:
            time.sleep(2 + attempt * 2)  # 2s, 4s backoff
    return None


def _confirm_position_closed(symbol: str, attempts: int = 2) -> bool:
    """Confirm a position close using the EXCHANGE as source of truth.

    A single poll can transiently miss a live symbol (rate-limit / lag) →
    false 'phantom close' card. Returns True if EITHER:
      - position is absent on a fresh request (full close), OR
      - there is a closed-pnl record (partial close — position may still be
        alive but a reduce/TP fill already happened; not a phantom).
    """
    import hmac, hashlib, httpx
    ak, as_ = _load_credentials()
    if not ak or not as_:
        return False
    for _ in range(attempts):
        try:
            ts = str(int(time.time() * 1000))
            q = f"category=linear&symbol={symbol}"
            sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
            headers = {
                "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000",
            }
            r = httpx.get(f"{_API_BASE}/v5/position/list?{q}", headers=headers, timeout=5)
            d = r.json()
            if d.get("retCode") == 0:
                raw = d.get("result", {}).get("list", []) or []
                still_open = any(float(p.get("size", 0)) > 0 for p in raw if isinstance(p, dict))
                if not still_open:
                    return True  # confirmed fully gone
                # Position still alive — check if a partial close happened
                # (closed-pnl record exists = real reduce/TP fill, not phantom).
                try:
                    cp = _fetch_closed_trade(symbol, "")
                    if cp and cp.get("closedPnl") is not None and abs(cp.get("closedPnl", 0)) > 0:
                        return True  # partial close confirmed via closed-pnl
                except Exception:
                    pass
                # Still alive with no closed-pnl → not a real close → phantom
                return False
        except Exception:
            pass
        time.sleep(1)
    # Could not confirm via API — assume still open (don't record phantom close)
    return False


def _is_already_closed(symbol: str, state_entry: dict) -> bool:
    """Idempotency guard: check if this exact closure was already recorded.

    Matches on (symbol, entry_time, entry, side). Prevents duplicate
    Telegram notifications and duplicate trade_results entries when a
    closed symbol persists in reality_state (restart race / exception
    in the closure path). The guard reads the last 200 lines of
    trade_results.jsonl — enough to cover hours of polling.
    """
    try:
        if not TRADE_RESULTS_DIR.exists():
            return False
        log_file = TRADE_RESULTS_DIR / "trade_results.jsonl"
        if not log_file.exists():
            return False
        entry_time = state_entry.get("entry_time", 0)
        entry = state_entry.get("entry", 0)
        side = state_entry.get("side", "")
        if not entry_time:
            return False
        # Read last 200 lines efficiently
        lines = log_file.read_text().splitlines()[-200:]
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("symbol") != symbol:
                continue
            # Match on entry_time (unique per trade) + entry price + side
            # entry_time stored in state as epoch float; in record it's not
            # stored, so we match on entry+side which are stable per trade.
            if (abs(float(rec.get("entry", 0)) - float(entry)) < 1e-9 and
                    rec.get("side") == side and
                    rec.get("status") == "CLOSED"):
                return True
        return False
    except Exception:
        return False


def _record_trade_closure(symbol, state_entry):
    """
    Record a trade closure with separate Guardian metrics:
    - Triggered: which Guardian levels fired
    - Outcome: TP, BE, SL, Manual, Timeout
    - Estimated benefit: only counted if the trade closed
      at or below the Guardian SL (i.e., Guardian did something)

    IDEMPOTENCY: if this exact closure (symbol+entry+side) was already
    recorded, skip re-recording and re-notifying. Prevents the
    duplicate-notification bug where a closed symbol stays in state
    and re-triggers on every poll cycle.
    """
    if _is_already_closed(symbol, state_entry):
        logger.info(
            f"⏭️ SKIP duplicate closure: {symbol} already recorded "
            f"(entry={state_entry.get('entry')}, side={state_entry.get('side')}) — "
            f"not re-notifying Telegram"
        )
        return
    peak_r = state_entry.get("mfe_peak", 0)
    trough_r = state_entry.get("mae_trough", 0)
    be_fired = state_entry.get("be_fired", False)
    partial_fired = state_entry.get("partial_fired", False)
    tight_fired = state_entry.get("tight_fired", False)

    entry = state_entry.get("entry", 0)
    side = state_entry.get("side", "")
    original_risk = state_entry.get("entry_to_sl_risk", 0)  # 2*ATR

    # Determine if Guardian moved the SL during the trade
    if be_fired:
        guardian_trigger = "BE"
        guardian_sl_at_close = entry  # SL at entry after BE
    elif tight_fired:
        guardian_trigger = "TIGHT"
        guardian_sl_at_close = entry + original_risk  # SL at entry + 1.0x risk
    elif partial_fired:
        guardian_trigger = "PARTIAL"
        guardian_sl_at_close = entry + original_risk * 0.5  # SL at entry + 0.5x risk
    else:
        guardian_trigger = "NONE"
        guardian_sl_at_close = 0  # no SL change

    # Get actual close data from Bybit (real fees, PnL, exit price)
    closed = _fetch_closed_trade(symbol, side)
    if closed and closed.get("avgExitPrice", 0) > 0:
        close_price = closed["avgExitPrice"]
        real_fees = abs(closed.get("openFee", 0)) + abs(closed.get("closeFee", 0))
        real_pnl = closed.get("closedPnl", 0)
        real_qty = closed.get("qty", 0) or state_entry.get("size", 0)
    else:
        close_price = _fetch_actual_close_price(symbol, side, entry)
        real_fees = 0.0
        real_pnl = None
        real_qty = state_entry.get("size", 0)

    # ─── Честная классификация закрытия ПО ФАКТУ (2026-08-08) ───
    # Раньше: outcome = эвристика по entry/close (любой прибыльный close → "TP"),
    # из-за чего ручное закрытие ACX классифицировалось как ложный "TP".
    # Теперь сравниваем close_price с реальными уровнями SL/TP (из state).
    sl_initial = float(state_entry.get("sl_initial", 0) or 0)
    tp_initial = float(state_entry.get("tp_initial", 0) or 0)
    # Допуск: 10% от расстояния entry→SL или 0.1% цены (что больше) — поглощает спред/проскальзывание
    tolerance = max(abs(entry - sl_initial) * 0.10, entry * 0.001) if sl_initial else entry * 0.001

    outcome = "UNKNOWN"
    # FIX 2026-08-31: guardian-initiated закрытия (time-stop, peak-reversal,
    # near-tp) раньше классифицировались по ЦЕНЕ выхода → выходили как
    # "MANUAL" → владелец видел «ручное закрытие в минусе» и не понимал,
    # кто закрыл. Теперь guardian-флаги — приоритет: это НАШИ решения.
    if state_entry.get("time_stopped"):
        outcome = "TIME_STOP"
    elif state_entry.get("peak_reversal_closed"):
        outcome = "PEAK_REVERSAL"
    elif state_entry.get("near_tp_closed"):
        outcome = "NEAR_TP"
    elif tp_initial and abs(close_price - tp_initial) <= tolerance:
        outcome = "TP"
    elif sl_initial and abs(close_price - sl_initial) <= tolerance:
        # Закрытие у исходного SL → SL, ЕСЛИ guardian не перенёс SL ближе.
        # Если guardian-триггер сработал, реальный выход был у защищённого SL.
        if be_fired or partial_fired or tight_fired:
            outcome = "GUARDIAN"
        else:
            outcome = "SL"
    elif abs(close_price - entry) <= tolerance:
        # Закрытие у входа: BE (guardian перенёс SL на entry) либо ручной выход в ноль
        outcome = "BE" if be_fired else "MANUAL"
    else:
        # Не у TP, не у SL, не у entry → ручное/внешнее закрытие (напр. ACX)
        outcome = "MANUAL"

    # Классификация по данным exchange, если она доступна (execType: "Traded"/"Stop"/"Take")
    try:
        if closed and closed.get("execType"):
            exec_type = str(closed.get("execType", ""))
            if "Take" in exec_type:
                outcome = "TP"
            elif "Stop" in exec_type:
                outcome = "SL"
    except Exception:
        pass

    # Only count "estimated benefit" when Guardian actually protected:
    # 1. Guardian moved SL (any of the 3 triggers fired)
    # 2. The position's actual close was WORSE than Guardian's new SL
    # Otherwise Guardian only gave protection, no actual financial benefit
    estimated_benefit = 0
    protected_exit = False

    if guardian_trigger != "NONE":
        if side == "Sell":
            actual_was_at_loss = close_price > entry
            guardian_protected = close_price < guardian_sl_at_close
        else:
            actual_was_at_loss = close_price < entry
            guardian_protected = close_price > guardian_sl_at_close

        if actual_was_at_loss and guardian_protected:
            # Position went into loss, but Guardian's SL caught it at a better price
            if side == "Sell":
                loss_without_guardian = close_price - (entry + original_risk)
            else:
                loss_without_guardian = (entry - original_risk) - close_price
            actual_loss = 0  # Guardian caught at BE
            estimated_benefit = max(0, loss_without_guardian)
            protected_exit = True
        elif not actual_was_at_loss:
            # Trade was profitable at close — Guardian only insured, no benefit
            estimated_benefit = 0
            protected_exit = False
        else:
            estimated_benefit = 0
            protected_exit = False

    # Compute realized PnL from entry vs exit
    size = state_entry.get("size", 0)
    if real_pnl is not None:
        # Use REAL closed PnL from exchange (includes fees)
        realized_pnl = real_pnl
        fees = real_fees
        net_pnl = real_pnl  # closedPnl already net of fees
    else:
        # Fallback estimate (no exchange data)
        if side == "Sell":
            realized_pnl = (entry - close_price) * size
        else:
            realized_pnl = (close_price - entry) * size
        fees = abs(realized_pnl) * 0.00075  # 0.075% taker fee estimate
        net_pnl = realized_pnl - fees

    # ─── Execution Attribution ──────────────────────────────
    # Signal class: did price move in the right direction?
    if side == "Sell":
        signal_win = close_price < entry
    else:
        signal_win = close_price > entry
    
    if signal_win and realized_pnl > 0:
        signal_class = "SIGNAL_WIN_EXEC_WIN"
    elif signal_win and realized_pnl <= 0:
        signal_class = "SIGNAL_WIN_EXEC_LOSS"
    else:
        signal_class = "SIGNAL_LOSS_EXEC_LOSS"
    
    # Slippage cost = expected PnL at exit_price - actual gross PnL
    if side == "Sell":
        expected_pnl = (entry - close_price) * size
    else:
        expected_pnl = (close_price - entry) * size
    slippage_cost = expected_pnl - realized_pnl

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "config_version": _read_config_version(),
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "close_price": close_price,
        "size": size,
        "realized_pnl": round(realized_pnl, 6),
        "fees": round(fees, 6),
        "net_pnl": round(net_pnl, 6),
        "gross_pnl": round(realized_pnl + fees, 6),
        "data_source": "real" if real_pnl is not None else "estimated",
        "slippage_cost": round(slippage_cost, 6),
        "mfe_peak_r": round(peak_r, 3),
        "mae_trough_r": round(trough_r, 3),
        "signal_class": signal_class,
        "status": "CLOSED",
        "guardian_trigger": guardian_trigger,
        "be_fired": be_fired,
        "partial_fired": partial_fired,
        "tight_fired": tight_fired,
        "outcome": outcome,
        "sl": round(sl_initial, 8) if sl_initial else 0.0,
        "tp": round(tp_initial, 8) if tp_initial else 0.0,
        "protected_exit": protected_exit,
        "estimated_benefit": round(estimated_benefit, 6),
        "protection_cost_window_hours": 24,
        "protection_cost_status": "PENDING",
    }

    TRADE_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRADE_RESULTS_DIR / "trade_results.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")

    # Also append to Guardian effectiveness log (separate file for analysis)
    FINAL_TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FINAL_TRADE_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

    logger.info(
        f"📝 TRADE CLOSED: {symbol} | peak_r={peak_r:.3f} | "
        f"Guardian: {guardian_trigger} | outcome: {outcome} | "
        f"protected: {protected_exit} | estimated_benefit=${estimated_benefit:.4f}"
    )
    # Register realized loss with deposit guard (daily loss limit)
    try:
        sys.path.insert(0, "/root/tradingos")
        from tradingos.strategies.deposit_guard import get_guard
        get_guard().on_trade_closed(net_pnl, fees=fees)
    except Exception as e:
        logger.error(f"Deposit guard on_close failed: {e}")
    # Enqueue Telegram notification (guaranteed delivery via persistent queue)
    # Include SL/TP/mfe_r/mae_r so chart and text use SAME source of truth
    sl_price = entry - original_risk if side == "Buy" else entry + original_risk
    tp_price = 0  # tp not in state, skip
    entry_ts = state_entry.get("entry_time", 0)
    exit_ts = time.time()
    # RAW prices from Bybit for accurate pnl_pct calculation
    raw_entry = entry
    raw_exit = close_price
    _enqueue_telegram_close(
        symbol=symbol, side=side,
        entry_price=entry, exit_price=close_price,
        qty=size, pnl=net_pnl, fees=fees,
        holding_hours=state_entry.get("hold_hours", 0) or 0,
        reason=outcome,
        sl=sl_price, tp=tp_price,
        mfe_r=peak_r, mae_r=trough_r,
        entry_time=entry_ts, exit_time=exit_ts,
        entry_price_raw=raw_entry, exit_price_raw=raw_exit
    )


def _check_missed_profit(symbol: str, record: dict) -> dict:
    """
    Guardian Effectiveness v3 — Protection Cost check.
    Called 24h+ after a Guardian-triggered close.
    Checks if the market continued in the profitable direction,
    meaning Guardian prevented additional profit capture.
    
    Returns updated record with:
    - potential_missed_profit: how much more profit would have been made
    - net_guardian_value: benefit - missed_profit
    """
    if record.get("guardian_trigger") == "NONE":
        # Guardian didn't trigger — no cost to attribute
        record["potential_missed_profit"] = 0
        record["net_guardian_value"] = 0
        record["protection_cost_status"] = "N/A"
        return record
    
    side = record.get("side", "")
    entry = record.get("entry", 0)
    close_price = record.get("close_price", 0)
    close_time = record.get("timestamp", "")
    
    # Fetch current price (24h+ later)
    current_price = _fetch_actual_close_price(symbol, side, close_price)
    
    if side == "Sell":
        # Profit = price goes DOWN. If it kept going down, Guardian cost profit.
        max_observed_below_close = current_price < close_price
        if max_observed_below_close:
            # In SELL, missing profit = close_price - current_price (price kept going down)
            record["potential_missed_profit"] = round(close_price - current_price, 6)
        else:
            # Price went up, no missed profit from Guardian
            record["potential_missed_profit"] = 0
    else:
        # Buy: profit = price goes UP. If kept going up, Guardian cost profit.
        if current_price > close_price:
            record["potential_missed_profit"] = round(current_price - close_price, 6)
        else:
            record["potential_missed_profit"] = 0
    
    record["current_price_at_check"] = current_price
    record["net_guardian_value"] = round(
        record.get("estimated_benefit", 0) - record["potential_missed_profit"], 6
    )
    record["protection_cost_status"] = "CHECKED"
    return record


async def _missed_profit_check_loop():
    """Periodically check closed trades for missed profit (24h+ after close)."""
    import asyncio
    GUARDIAN_EFFECTIVENESS_LOG = Path("/root/tradingos/guardian/guardian_effectiveness.jsonl")
    PROTECTION_COST_LOG = Path("/root/tradingos/guardian/protection_cost.jsonl")
    
    while True:
        try:
            if not GUARDIAN_EFFECTIVENESS_LOG.exists():
                await asyncio.sleep(3600)  # 1 hour
                continue
            
            PROTECTION_COST_LOG.parent.mkdir(parents=True, exist_ok=True)
            
            cutoff = time.time() - 86400  # 24 hours ago
            with open(GUARDIAN_EFFECTIVENESS_LOG) as f:
                lines = [json.loads(l.strip()) for l in f if l.strip()]
            
            for record in lines:
                if record.get("protection_cost_status") != "PENDING":
                    continue
                close_time_str = record.get("timestamp", "")
                if not close_time_str:
                    continue
                try:
                    close_time = datetime.fromisoformat(close_time_str).timestamp()
                except:
                    continue
                if close_time > cutoff:
                    continue
                
                # Time elapsed
                record = _check_missed_profit(record["symbol"], record)
                # Re-save with updated Protection Cost fields
                # For now, log to a separate file
                with open(PROTECTION_COST_LOG, "a") as f:
                    f.write(json.dumps(record) + "\n")
                # Mark as checked
                with open(GUARDIAN_EFFECTIVENESS_LOG, "r") as f:
                    content = f.read()
                content = content.replace(json.dumps(record), json.dumps(record))
        except Exception as e:
            pass
        await asyncio.sleep(3600)  # check hourly


async def run_guardian():
    """Main Guardian loop — poll positions and apply rules."""
    logger.info("🛡️ Reality Guardian LIVE starting")
    logger.info(f"   Poll interval: {POLL_INTERVAL}s")
    logger.info(f"   BE: +{BE_THRESHOLD}R, Partial: +{PARTIAL_THRESHOLD}R, Tight: +{TIGHT_THRESHOLD}R")
    logger.info(f"   Timeout: {MAX_HOLD_HOURS}h")

    # CRITICAL: Initialize Telegram eagerly at startup (not lazy on first event).
    logger.info("Pre-warming Telegram notifier...")
    loop = _ensure_telegram_started()
    if loop and _tg_ready:
        logger.info(f"Telegram pre-warmed successfully (loop={loop})")
    else:
        logger.error("Telegram pre-warm FAILED — TG notifications will not work")

    while True:
        try:
            positions = _get_live_positions()
            live_symbols = {p["symbol"] for p in positions}

            # Detect trade closures: symbols in state but not in live positions
            state = _load_guardian_state()
            if not isinstance(state, dict):
                state = {}
            state_symbols = set(state.keys())
            closed_symbols = state_symbols - live_symbols
            for sym in closed_symbols:
                if sym in state and state[sym] is not None:
                    # FIX 2026-08-04: confirm the position is REALLY gone from the
                    # exchange before recording a close. A transient API error can
                    # make a live symbol disappear from one poll → false "phantom
                    # close" card (ADA/ARB incident). Re-query the exchange.
                    if not _confirm_position_closed(sym):
                        logger.warning(
                            f"⚠️ PHANTOM CLOSE: {sym} missing from poll but still "
                            f"on exchange — not recording closure"
                        )
                        # Alert the user — phantom close must be visible, not buried.
                        # FIX 2026-08-31: множившийся спам (12/10мин при API-лаге)
                        # — уведомляем не чаще 1 раза в 15 мин на символ.
                        try:
                            _last = _phantom_alert_ts.get(sym, 0)
                            if time.time() - _last >= 900:
                                _phantom_alert_ts[sym] = time.time()
                                _enqueue_telegram_phantom(sym)
                        except Exception as _e:
                            logger.error(f"phantom TG alert failed: {_e}")
                        continue
                    # FIX 2026-08-24: ALWAYS drop from state + save, even if closure
                    # recording throws — otherwise the symbol stays in state and
                    # re-triggers duplicate Telegram notifications on every poll
                    # (TQQQ incident). The closure record already exists on disk
                    # by the time we get here, or we accept losing this one
                    # snapshot rather than spamming the user.
                    try:
                        _record_trade_closure(sym, state[sym])
                    except Exception as closure_err:
                        logger.error(
                            f"_record_trade_closure failed for {sym}: {closure_err} — "
                            f"dropping from state anyway to prevent duplicate notifications"
                        )
                    finally:
                        state.pop(sym, None)
                        _save_guardian_state(state)
            for pos in positions:
                new_state = _process_position(pos, state)
                if isinstance(new_state, dict):
                    state = new_state
                # else: keep current state

            _save_guardian_state(state)
        except Exception as e:
            logger.error(f"Guardian loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


async def main():
    """Run both Guardian main loop and missed-profit checker concurrently."""
    import asyncio
    await asyncio.gather(run_guardian(), _missed_profit_check_loop())


if __name__ == "__main__":
    try:
        # Singleton guard: a second guardian instance double-polls the
        # exchange and sends duplicate Telegram notifications.
        sys.path.insert(0, "/root/tradingos")
        from core.singleton import acquire_singleton_lock
        acquire_singleton_lock("reality_guardian")
        asyncio.run(run_guardian())
    except KeyboardInterrupt:
        logger.info("Guardian stopped by user")
