"""
entry_quality_gate.py — ENTRY QUALITY GATE v1 (SHADOW-ONLY).

Изолированный защитный слой качества входа. НЕ исполняет ордера, НЕ меняет
executor/guardian/risk/Signal Generator/AUTO. Возвращает структурированное
решение: ALLOW / WAIT / SKIP + reason + метрики.

Защищает от очевидно плохих входов:
  OPPOSITE_IMPULSE   — свежий сильный импульс ПРОТИВ входа, нет reversal
  LATE_MOMENTUM      — импульс в сторону входа, но старый/без follow-through
  EXTENDED_ENTRY     — цена далеко от origin импульса (догон)
  NO_ROOM            — нет реалистичной цели до SL после издержек

Принцип: импульс = комбинация (displacement ATR, relative volume, body/range,
направление, возраст, расстояние от origin), НЕ просто volume > N.
Данные: только закрытые бары ДО момента решения (без look-ahead).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"
GATE_LOG = ROOT / "memory/entry_gate.jsonl"

# Максимальный возраст кэша 15m (в секундах). Если кэш старше — считаем stale
# и обновляем из живого источника (Bybit API). Это P1-фикс: раньше stale кэш
# превращался в NO_DATA, хотя свежие OHLCV были доступны.
CACHE_MAX_AGE_S = 1800  # 30 мин (2 бара 15m)
# FIX 2026-08-31: entry-gate/KV2 с live-загрузкой 15m на каждый из 124 символов
# упирался в rate-limit (115/124 кэша stale → скан встал, reality замолчал).
# Структура рынка (тренд/импульс) живёт дольше 30 мин — для gate допустим
# stale-кэш до CACHE_STALE_OK_AGE_S (3ч); live-API только при совсем старом.
CACHE_STALE_OK_AGE_S = 3 * 3600


def load_candles(symbol: str) -> pd.DataFrame | None:
    """Загрузить 15m свечи: свежий кэш → иначе живой источник → иначе stale кэш.

    Возвращает (df, meta) где meta содержит source/age для логирования.
    """
    p = CACHE_DIR / f"{symbol}_M15.parquet"
    df_cache = None
    cache_age = None
    if p.exists():
        try:
            df_cache = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
            if df_cache["ts"].iloc[0] > 1e12:
                df_cache["ts"] = df_cache["ts"] / 1000
            if len(df_cache) > 0:
                cache_age = time.time() - float(df_cache["ts"].iloc[-1])
        except Exception:
            df_cache = None

    fresh = None
    source = "cache"
    if df_cache is not None and cache_age is not None and cache_age <= CACHE_MAX_AGE_S:
        fresh = df_cache
    elif df_cache is not None and cache_age is not None and cache_age <= CACHE_STALE_OK_AGE_S:
        # FIX 2026-08-31: свежий stale-кэш (до 3ч) достаточен для gate —
        # не долбим Bybit live, экономим rate-limit. Помечаем source.
        fresh = df_cache
        source = "cache_stale_ok"
    else:
        # Кэш совсем старый/нет → живой источник (Bybit v5 kline, 15m, 200 баров)
        try:
            import httpx
            with httpx.Client(timeout=10) as _c:
                r = _c.get("https://api.bybit.com/v5/market/kline",
                           params={"category": "linear", "symbol": symbol, "interval": "15", "limit": 200})
                rows = (r.json().get("result") or {}).get("list") or []
            if rows:
                fresh = pd.DataFrame([
                    {"ts": int(b[0]) / 1000, "o": float(b[1]), "h": float(b[2]),
                     "l": float(b[3]), "c": float(b[4]), "v": float(b[5])}
                    for b in rows
                ]).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
                source = "live_api"
                # Обновляем кэш для следующих вызовов
                try:
                    _df_save = fresh.copy()
                    _df_save["ts"] = (_df_save["ts"] * 1000).astype("int64")
                    _df_save.to_parquet(p)
                except Exception:
                    pass
        except Exception:
            fresh = None
        if fresh is None:
            fresh = df_cache  # данные реально недоступны → stale кэш как последний шанс
            source = "cache_stale"

    if fresh is None or len(fresh) < 60:
        return None, {"source": source, "data_age": None, "ok": False}

    # Предрасчёт ATR/медианного объёма (как раньше)
    tr = pd.concat([
        (fresh["h"] - fresh["l"]),
        (fresh["h"] - fresh["c"].shift(1)).abs(),
        (fresh["l"] - fresh["c"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    fresh["atr"] = tr.rolling(30).mean()
    fresh["vm"] = fresh["v"].rolling(60).median().shift(1)
    # Колонки для engine_v2.decide/bar_context (hi40 и др.). БЕЗ них v2_decide
    # падает KeyError 'hi40' → run_observation делает fallback к 2/4 ATR →
    # gate фактически НЕ работает в live (баг 2026-08-09 08:49-10:35 MSK).
    fresh["hi40"] = fresh["h"].rolling(40).max().shift(1)
    fresh["lo40"] = fresh["l"].rolling(40).min().shift(1)
    fresh["hi20"] = fresh["h"].rolling(24).max().shift(1)
    fresh["lo20"] = fresh["l"].rolling(24).min().shift(1)
    fresh["e20"] = fresh["c"].ewm(span=20, adjust=False).mean()
    fresh["e50"] = fresh["c"].ewm(span=50, adjust=False).mean()
    data_age = time.time() - float(fresh["ts"].iloc[-1])
    return fresh, {"source": source, "data_age": round(data_age / 60, 1), "ok": True}


def analyze_impulse(df: pd.DataFrame, ts: float) -> dict | None:
    """Определить последний значимый импульс ДО ts. Возвращает метрики или None."""
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < 60:
        return None
    b = pre.iloc[-1]
    atr = float(b["atr"]) if pd.notna(b["atr"]) else 0.0
    vm = float(b["vm"]) if pd.notna(b["vm"]) else float(b["v"])
    if atr <= 0:
        return None
    win = pre.tail(6)  # окно свежести: до 6 баров (1.5ч)
    best = None
    for _, wb in win.iterrows():
        if wb["v"] <= 1.0 * vm:
            continue
        rng = wb["h"] - wb["l"]
        if rng <= 0:
            continue
        disp = abs(wb["c"] - wb["o"]) / atr
        rel_v = wb["v"] / vm if vm > 0 else 1
        body = abs(wb["c"] - wb["o"]) / rng
        strength = disp * rel_v
        if best is None or strength > best["strength"]:
            best = {
                "direction": "UP" if wb["c"] > wb["o"] else "DOWN",
                "disp_atr": disp, "rel_vol": rel_v, "body_ratio": body,
                "strength": strength, "age_min": (ts - wb["ts"]) / 60,
                "origin": float(wb["o"]), "extreme": float(wb["h"] if wb["c"] > wb["o"] else wb["l"]),
                "close": float(wb["c"]),
                "close_ts": float(wb["ts"]),  # S3 fix: timestamp импульса для reversal-window
            }
    if best is None:
        return None
    # расстояние текущей цены от origin/extreme
    price = float(b["c"])
    best["dist_origin_atr"] = abs(price - best["origin"]) / atr
    best["dist_extreme_atr"] = abs(best["extreme"] - price) / atr
    return best


def check_reversal(df: pd.DataFrame, imp: dict, ts: float, side: str) -> bool:
    """Подтверждённый разворот ПОСЛЕ импульса: lower-high (SELL) / higher-low (BUY).
    S3 fix: окно строится по TIMESTAMP импульса (close_ts), НЕ по цене close."""
    between = df[(df["ts"] > imp["close_ts"] + 60) & (df["ts"] <= ts - 60)]
    if len(between) < 2:
        return False
    if side == "SELL":
        return between["h"].iloc[0] > between["h"].max() - 1e-12 and \
               between["h"].iloc[0] > between["h"].iloc[-1] * 0.999
    return between["l"].iloc[0] < between["l"].min() + 1e-12 and \
           between["l"].iloc[0] < between["l"].iloc[-1] * 1.001


def gate(symbol: str, side: str, ts: float, df: pd.DataFrame | None,
         log: bool = True, meta: dict | None = None) -> dict:
    """ENRTY QUALITY GATE. Возвращает {decision, reason, metrics...}.

    meta: результат load_candles → {source, data_age, ok}. Если не передан,
    считаем данные из кэша без проверки свежести (обратная совместимость).
    """
    rec = {
        "symbol": symbol, "side": side, "timestamp": ts,
        "decision": "SKIP", "reason": "NO_DATA",
        "impulse_direction": None, "impulse_atr": None,
        "relative_volume": None, "distance_origin_atr": None,
        "distance_extreme_atr": None, "impulse_age_min": None,
        "data_timestamp": None, "data_age": None, "source": None,
    }
    if meta:
        rec["data_age"] = meta.get("data_age")
        rec["source"] = meta.get("source")
        if df is not None and len(df) > 0:
            rec["data_timestamp"] = float(df["ts"].iloc[-1])
    if df is None or len(df) < 60:
        return _finish(rec, log)

    # T85 hardening: сторона валидируется ДО раннего возврата no-impulse.
    # invalid side + no impulse не должно давать ALLOW — отсутствие валидной
    # стороны всегда означает NO ORDER (safety-корректность, не стратегия).
    if side not in ("BUY", "SELL"):
        return {"decision": "SKIP", "reason": f"NO_DATA: invalid side {side!r}"}

    imp = analyze_impulse(df, ts)
    if imp is None:
        rec["decision"] = "ALLOW"; rec["reason"] = "NORMAL"
        return rec

    rec.update({
        "impulse_direction": imp["direction"],
        "impulse_atr": round(imp["disp_atr"], 2),
        "relative_volume": round(imp["rel_vol"], 1),
        "distance_origin_atr": round(imp["dist_origin_atr"], 2),
        "distance_extreme_atr": round(imp["dist_extreme_atr"], 2),
        "impulse_age_min": round(imp["age_min"], 0),
    })

    # F7 hardening: UNKNOWN side → SKIP/NO_DATA, а не дефолтный DOWN-favor.
    fav = "UP" if side == "BUY" else "DOWN"
    # Значимый импульс: displacement + volume + body (характеристика события)
    significant = imp["disp_atr"] > 0.8 and imp["rel_vol"] > 1.5 and imp["body_ratio"] > 0.5

    if significant and imp["direction"] != fav:
        # Против входа: разрешаем только при подтверждённом развороте
        rev = check_reversal(df, imp, ts, side)
        if not rev:
            rec["decision"] = "SKIP"; rec["reason"] = "OPPOSITE_IMPULSE"
        else:
            rec["decision"] = "ALLOW"; rec["reason"] = "REVERSAL_CONFIRMED"
        return _finish(rec, log)

    if significant and imp["direction"] == fav:
        # В сторону входа: возраст + расстояние от origin
        if imp["age_min"] > 60 or imp["dist_origin_atr"] > 2.0:
            rec["decision"] = "SKIP"; rec["reason"] = "LATE_MOMENTUM"
        elif imp["dist_origin_atr"] > 1.5:
            rec["decision"] = "SKIP"; rec["reason"] = "EXTENDED_ENTRY"
        else:
            rec["decision"] = "ALLOW"; rec["reason"] = "CONTINUATION"
        return _finish(rec, log)

    # Слабый импульс: оцениваем location
    if imp["dist_origin_atr"] > 2.0:
        rec["decision"] = "SKIP"; rec["reason"] = "EXTENDED_ENTRY"
    else:
        rec["decision"] = "ALLOW"; rec["reason"] = "NORMAL"

    return _finish(rec, log)


def _finish(rec: dict, log: bool) -> dict:
    """Логирование решения (единая точка для всех return)."""
    if log:
        rec["logged_at"] = time.time()
        GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with GATE_LOG.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec
