"""
manual_scanner.py — SIGNAL_ONLY v0 (композитор эффектов, НЕ новая стратегия).

Сканирует рынок и оценивает сетап по таблице весов (v0, гипотеза, не факт):

  H1 trend        25   — направление H1 vs EMA20/EMA50
  M15 structure   20   — направление M15 структуры
  momentum        15   — сила импульса (RSI + ADX)
  volume          15   — относительный объём
  funding/OI      10   — деривативный контекст
  entry quality   15   — откат к зоне/близость к EMA (качество входа)

Итого 0-100. >= min_signal_score (80) → кандидат.

НИЧЕГО не исполняет: только формирует сигнал и пишет журнал.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

# Import the new contour classifier
from tradingos.signals.contour_classifier import (
    ContourClassifier,
    ContourThresholds,
    build_features_from_scanner,
)

# Import validation recorder (shadow mode)
try:
    from tradingos.signals.validation import get_recorder, FunnelSnapshot
    _VALIDATION_ENABLED = True
except Exception:
    _VALIDATION_ENABLED = False

ROOT = Path("/root/tradingos")
CONFIG = ROOT / "operations/manual_session.json"
JOURNAL = ROOT / "memory/manual_signals.jsonl"

# v0 weights (гипотеза пользователя, НЕ подтверждённая закономерность)
WEIGHTS = {
    "h1_trend": 25,
    "m15_structure": 20,
    "momentum": 15,
    "volume": 15,
    "funding_oi": 10,
    "entry_quality": 15,
}


def load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {"symbols": [], "min_signal_score": 80}


def _ema(vals: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    e = vals[0]
    out = [e]
    for x in vals[1:]:
        e = x * k + e * (1 - k)
        out.append(e)
    return out


def _rsi_wilder(closes: list[float], n: int = 14) -> float:
    """RSI14 со сглаживанием Wilder (стандарт), а не простым средним.

    Простая средняя gain/loss скачет (48->96 на тех же барах), потому что
    один аномальный бар перевешивает окно. Wilder-сглаживание устойчиво."""
    if len(closes) < n + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        elif d < 0:
            losses += -d
    avg_g = gains / n
    avg_l = losses / n
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (n - 1) + max(d, 0)) / n
        avg_l = (avg_l * (n - 1) + max(-d, 0)) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def _klines(client: httpx.Client, symbol: str, interval: str, limit: int) -> list[dict] | None:
    r = client.get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        timeout=15,
    )
    data = r.json()
    if data.get("retCode") != 0:
        return None
    rows = (data.get("result") or {}).get("list") or []
    if len(rows) < 30:
        return None
    # rows: newest first; нормализуем к хронологическому порядку
    out = []
    for row in reversed(rows):
        out.append({
            "ts": int(row[0]), "open": float(row[1]), "high": float(row[2]),
            "low": float(row[3]), "close": float(row[4]), "volume": float(row[5]),
        })
    return out


def _funding_oi(client: httpx.Client, symbol: str) -> dict:
    try:
        r = client.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "linear", "symbol": symbol},
            timeout=15,
        )
        t = ((r.json().get("result") or {}).get("list") or [{}])[0]
        funding = float(t.get("fundingRate") or 0)
        oi = float(t.get("openInterest") or 0)
        return {"funding": funding, "oi": oi}
    except Exception:
        return {"funding": 0.0, "oi": 0.0}


_STOCK_TYPES: dict[str, str] = {}


def _stock_symbol_type(client: httpx.Client, symbol: str) -> str:
    """symbolType из /v5/market/instruments-info (stock/commodity/innovation/"").
    Кэш в модуле: один запрос на символ на весь скан."""
    if symbol in _STOCK_TYPES:
        return _STOCK_TYPES[symbol]
    try:
        r = client.get(
            "https://api.bybit.com/v5/market/instruments-info",
            params={"category": "linear", "symbol": symbol},
            timeout=15,
        )
        lst = (r.json().get("result") or {}).get("list") or [{}]
        st = str(lst[0].get("symbolType") or "")
    except Exception:
        st = ""
    _STOCK_TYPES[symbol] = st
    return st


def score_symbol(client: httpx.Client, symbol: str, min_score: int) -> dict | None:
    h1 = _klines(client, symbol, "60", 96)
    m15 = _klines(client, symbol, "15", 200)
    if not h1 or not m15:
        return None

    # ─── STRUCTURAL QUALITY GATE (2026-08-29, T19) ───────────
    # Пользователь прав: система «не видит рынок» — она генерила сигналы на
    # застывшей структуре (AMZN 266.3→266.5 весь день), по одним и тем же
    # монетам, с мизерной ликвидностью. Три фильтра ДО начисления баллов:
    #
    # 1. LIGUIDITY: медианный H1 notional за сутки. Токенизированные акции
    #    Bybit (AMZN/AAPL/MSFT/META ~$20-40k/час vs BTC $138M) — единичные
    #    тики, не рыночная структура. Порог из конфига min_h1_notional_usd.
    # 2. FRESHNESS: последний бар не старше 3× интервала (данные «замерли» —
    #    рынок закрыт/листинг не торгуется) → сигнал невозможен.
    # 3. MOVEMENT: за последние 3 закрытых H1 цена должна сдвинуться ≥
    #    min_h1_move_pct (иначе «сигнал» — повтор застывшей структуры).
    cfgq = load_config()
    min_notional = float(cfgq.get("min_h1_notional_usd", 200_000) or 200_000)
    min_h1_move_pct = float(cfgq.get("min_h1_move_pct", 0.25) or 0.25)
    try:
        h1_vols = [(b["close"] * b["volume"]) for b in h1]
        h1_notional_med = sorted(h1_vols)[len(h1_vols) // 2]
        last_ts = h1[-1]["ts"]
        last_age_h = (time.time() * 1000 - last_ts) / 3_600_000
        range_3h = max(b["high"] for b in h1[-3:]) - min(b["low"] for b in h1[-3:])
        move_pct = range_3h / h1[-1]["close"] * 100 if h1[-1]["close"] else 0
    except Exception:
        return None
    if h1_notional_med < min_notional:
        return None  # неликвид: сигнал = шум на единичных тиках
    if last_age_h > 3:
        return None  # данные замерли
    if move_pct < min_h1_move_pct:
        return None  # нет движения — нет сетапа

    h1c = [b["close"] for b in h1]
    # Индикаторы считаем по последнему ЗАКРЫТОМУ бару (индекс -2: последний бар
    # таймфрейма ещё формируется и имеет почти нулевой объём/искажённые close).
    closed_last = h1c[-2]
    e20, e50 = _ema(h1c, 20)[-2], _ema(h1c, 50)[-2]
    h1_up = closed_last > e20 > e50  # строгая структура: цена > EMA20 > EMA50
    h1_down = closed_last < e20 < e50  # строгая структура: цена < EMA20 < EMA50

    m15c = [b["close"] for b in m15]
    me20 = _ema(m15c, 20)[-2]
    m15_up = m15c[-2] > me20

    # C1 (2026-08-29): зрелость отката для WAIT-LIMIT. Лимитка на откате имеет
    # смысл только когда падение УЖЕ начало разворачиваться — появился первый
    # восходящий M15-бар после серии вниз. Если цена всё ещё летит вниз
    # (последние 3+ закрытых баров подряд вниз, ни одного up-бара) — откат
    # незрелый: лимитка поймает нож. Считаем по последним 6 закрытым M15.
    m15_recent = m15c[-6:]
    m15_up_bars = sum(1 for i in range(1, len(m15_recent))
                      if m15_recent[i] > m15_recent[i - 1])
    m15_straight_down = len(m15_recent) >= 4 and all(
        m15_recent[i] <= m15_recent[i - 1] for i in range(1, len(m15_recent)))
    # «откат зрелый»: есть хотя бы один up-бар в последних 6 (вышла из
    # серии чистых вниз) И не в начале вертикального обвала (>3 up подряд —
    # это новая волна вверх, ждём её откат, а не это).
    pullback_mature = (not m15_straight_down and m15_up_bars >= 1)

    # momentum: RSI14 (Wilder-сглаживание) по закрытым H1-барам
    rsi = _rsi_wilder(h1c)

    # volume: последний ЗАКРЫТЫЙ час vs средние 20 закрытых
    vols = [b["volume"] for b in h1]
    vol_ratio = vols[-2] / (sum(vols[-22:-2]) / 20) if len(vols) > 22 and sum(vols[-22:-2]) > 0 else 1.0

    # entry quality: расстояние до EMA20 (откат = лучше для входа)
    dist_to_e20 = (closed_last - e20) / e20 * 100  # % от цены
    entry_q = 15 if abs(dist_to_e20) < 0.15 else (10 if abs(dist_to_e20) < 0.35 else 5)

    fo = _funding_oi(client, symbol)
    funding = fo.get("funding", 0.0)
    # funding близко к нулю и OI есть → нейтральный контекст = не наказываем
    fund_score = 10 if abs(funding) < 0.0001 else (6 if funding < 0.0003 else 3)

    parts = {}
    # направление по H1 — СИММЕТРИЧНЫЙ скоринг (LONG и SHORT получают одинаково)
    if h1_up:
        parts["h1_trend"] = 25       # строгий аптренд → LONG
    elif h1_down:
        parts["h1_trend"] = 25       # строгий даунтренд → SHORT
    elif closed_last > e20:
        parts["h1_trend"] = 15       # частичный аптренд
    elif closed_last < e20:
        parts["h1_trend"] = 15       # частичный даунтренд
    else:
        parts["h1_trend"] = 0        # боковик / flat

    parts["m15_structure"] = 20 if m15_up else (5 if not m15_up else 5)
    # momentum: СИММЕТРИЧНЫЙ — бычий RSI для LONG, медвежий для SHORT
    if h1_up and rsi > 50:
        parts["momentum"] = min(15, int(rsi / 100 * 15))
    elif h1_down and rsi < 50:
        parts["momentum"] = min(15, int((100 - rsi) / 100 * 15))
    else:
        parts["momentum"] = 3
    # P2-fix: below-average volume НЕ даёт очков (раньше int(0.68×10)=6 за слабый объём)
    parts["volume"] = min(15, int(vol_ratio * 10)) if vol_ratio >= 1.0 else 0
    parts["funding_oi"] = fund_score
    parts["entry_quality"] = entry_q
    total = sum(parts.values())

    # ─── AUX INDICATOR LAYER (Stochastic + RSI-squeeze + MTF H1/H4/D1) ───
    # Не добавляют баллы к score (шкала /100); влияют через gate и карточку.
    stoch = {"k": None, "d": None, "cross_up": False, "cross_down": False}
    squeeze = {"squeeze_on": False, "fired": False, "momentum": 0.0}
    mtf = {"h4_trend": None, "d1_trend": None, "agree": None}
    try:
        from tradingos.data.indicators import IndicatorCalculator
        from tradingos.data.models.candle import Candle as _Candle
        from tradingos.signals.manual_scanner import _klines as _k

        def _candles(rows_raw):
            out = []
            for x in reversed(rows_raw):
                out.append(_Candle(timestamp=int(x[0]) / 1000, open=float(x[1]),
                                   high=float(x[2]), low=float(x[3]),
                                   close=float(x[4]), volume=float(x[5])))
            return out

        # Stochastic + squeeze на H1 (закрытый бар -2 внутри расчётов)
        h1_raw = client.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": "60", "limit": 200},
            timeout=15,
        ).json().get("result", {}).get("list") or []
        h1_candles = _candles(h1_raw)
        if len(h1_candles) >= 40:
            stoch = IndicatorCalculator.stochastic(h1_candles)
            squeeze = IndicatorCalculator.rsi_squeeze(h1_candles)

        # MTF: H4 и D1 тренды (EMA20/EMA50, последний закрытый бар)
        def _mtf_trend(interval: str, limit: int) -> str | None:
            r = client.get(
                "https://api.bybit.com/v5/market/kline",
                params={"category": "linear", "symbol": symbol, "interval": interval,
                        "limit": limit},
                timeout=15,
            ).json().get("result", {}).get("list") or []
            if len(r) < 60:
                return None
            closes = [float(x[4]) for x in reversed(r)]
            e20 = _ema(closes, 20)[-2]
            e50 = _ema(closes, 50)[-2]
            cl = closes[-2]
            if cl > e20 > e50:
                return "UP"
            if cl < e20 < e50:
                return "DOWN"
            return "MIXED"

        mtf["h4_trend"] = _mtf_trend("240", 120)
        mtf["d1_trend"] = _mtf_trend("D", 60)
        if mtf["h4_trend"] in ("UP", "DOWN") and mtf["d1_trend"] in ("UP", "DOWN"):
            mtf["agree"] = mtf["h4_trend"] == mtf["d1_trend"]
    except Exception:
        pass

    # Направление: только строго подтверждённое. НЕТ нейтрального шорта отката
    # в аптренде и наоборот — если H1 не имеет чёткой структуры, сделки нет.
    if h1_up:
        side = "LONG"
    elif h1_down:
        side = "SHORT"
    else:
        return None  # H1 mixed/range — нет направления, не торгуем

    # sell_disabled=true → НЕ генерировать SHORT-сигналы вообще (2026-08-25).
    # Шорты ручного контура систематически в минус (WR ~30% против 80% LONG),
    # корень — ложный h1_down на растущем рынке. Временно отключаем SHORT,
    # пока не будет валидирован направленный фикс скоринга.
    try:
        with open("/root/tradingos/operations/trading_mode.json") as _f:
            import json as _json
            _tm = _json.load(_f)
            if _tm.get("sell_disabled", False) and side == "SHORT":
                return None
    except Exception:
        pass

    # Gate: MTF-конфликт (H4 или D1 против направления H1) → SKIP
    # FIX 2026-09-02: конфигурируемый флаг. При mtf_gate_enabled=false → WARN в карточке, не блок.
    mtf_gate = None
    _mtf_enabled = bool(cfgq.get("mtf_gate_enabled", True))
    if _mtf_enabled:
        if side == "LONG":
            if mtf["h4_trend"] == "DOWN" or mtf["d1_trend"] == "DOWN":
                mtf_gate = "MTF_CONFLICT_DOWN"
        else:
            if mtf["h4_trend"] == "UP" or mtf["d1_trend"] == "UP":
                mtf_gate = "MTF_CONFLICT_UP"
    # Stochastic: пересечение против направления → предупреждение; зона против
    # направления (K>80 при LONG / K<20 при SHORT) → блок (импульс против входа)
    stoch_conflict = (side == "LONG" and stoch.get("cross_down")) or \
                     (side == "SHORT" and stoch.get("cross_up"))
    k = stoch.get("k")
    stoch_zone_block = (side == "LONG" and k is not None and k > 80) or \
                       (side == "SHORT" and k is not None and k < 20)
    stoch_gate = "STOCH_ZONE_CONFLICT" if stoch_zone_block else None

    if total < min_score:
        return None

    # ─── MARKET CONTEXT + TRADE PLAN (market-based TP, не «4×ATR вслепую») ───
    # raw_tp = 4×ATR (только для сравнения), final_tp ограничен структурой:
    # TP ставится ПЕРЕД ближайшим значимым уровнем + buffer, никогда за экстремумом.
    atr = 0.0
    try:
        trs = []
        for i in range(1, len(h1)):
            tr = max(h1[i]["high"] - h1[i]["low"],
                     abs(h1[i]["high"] - h1[i - 1]["close"]),
                     abs(h1[i]["low"] - h1[i - 1]["close"]))
            trs.append(tr)
        if len(trs) >= 14:
            atr = sum(trs[-14:]) / 14
    except Exception:
        pass

    tp_unreachable = False
    rr_invalid = False
    raw_tp = final_tp = 0.0
    range_7d_high = range_7d_low = range_30d_high = range_30d_low = 0.0
    try:
        r7 = client.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": "60", "limit": 168},
            timeout=15,
        )
        r30 = client.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": "60", "limit": 720},
            timeout=15,
        )
        rows7 = (r7.json().get("result") or {}).get("list") or []
        rows30 = (r30.json().get("result") or {}).get("list") or []
        if len(rows7) >= 100:
            range_7d_high = max(float(x[2]) for x in rows7)
            range_7d_low = min(float(x[3]) for x in rows7)
        if len(rows30) >= 100:
            range_30d_high = max(float(x[2]) for x in rows30)
            range_30d_low = min(float(x[3]) for x in rows30)

        # π-коэффициент для акций: TP = π×ATR, SL = π×ATR/2 (вместо 4×ATR / 2×ATR),
        # чтобы расстояние под акции было меньше кратно π, сохраняя R:R ≈ 2.
        # Для крипто/товаров остаются классические множители.
        try:
            from tradingos.data.reality_universe import classify_instrument as _cls
            st = _stock_symbol_type(client, symbol)
            is_stock = _cls(symbol, st) == "tokenized_stock"
        except Exception:
            is_stock = False
        tp_mult = float(load_config().get("pi_coef", 3.14159)) if is_stock else 4.0
        sl_mult = tp_mult / 2.0
        # raw_tp (только для сравнения), final_tp ограничен структурой:
        # TP ставится ПЕРЕД ближайшим значимым уровнем + buffer, никогда за экстремумом.
        # FIX 2026-08-28 (AMZN/AAPL chase-top incident): ceiling/floor ограничены
        # ТАКЖЕ 7-дневным экстремумом. Раньше только 30d — TP спокойно висел над
        # недельным максимумом (AAPL: TP 324.34 vs 7d-high 322.44, касаний 3.3%,
        # последний 29 дней назад). raw_tp за ближней структурой = недостижим
        # без пробоя → маркет-ввод отклоняем, предлагаем лимитку на откате.
        raw_tp = closed_last + atr * tp_mult if side == "LONG" else closed_last - atr * tp_mult
        # WAIT_LIMIT-зона: fib 0.5 отката 7d-high → EMA20(H1) (для LONG).
        # Модель владельца («ордера в прогнозных точках, сидишь ждёшь»): если
        # цена откатит в зону — войдём лимитом, ТП уже ограничен структурой.
        # FIX 2026-08-29 (AAPL instant-fill incident): зона ДОЛЖНА быть
        # заметно ниже текущей цены — иначе лимитка становится marketable
        # (цена уже в/внизу зоны → мгновенный филл, а не «ждать откат»).
        # Минимальная дистанция отката = 0.3×ATR (≈ один значимый шаг M15),
        # иначе WAIT-сетап бессмыслен — это скорее маркет-вход с зоной.
        wait_limit_entry = 0.0
        wait_limit_entry_deep = 0.0
        # Минимальный откат до лимита: НЕ менее 0.25% цены (иначе лимитка
        # почти у рынка — мгновенный филл либо ловля боковика, не откат).
        wait_min_dist = max(atr * 0.5, closed_last * 0.0025)
        # C1: только зрелый откат (см. pullback_mature). Незрелый (цена ещё
        # летит вниз, ни одного up-бара) → лимитка ловит нож → зона не валидна.
        if not pullback_mature:
            wait_limit_entry = 0.0
            wait_limit_entry_deep = 0.0
        if range_7d_high > 0 and e20 > 0:
            if side == "LONG":
                wl = range_7d_high - (range_7d_high - e20) * 0.5
                if wl < closed_last and (closed_last - wl) >= wait_min_dist:
                    wait_limit_entry = wl
                # B2 (2026-08-29): второй уровень лестницы — fib 0.618
                # (глубже откат = чаще филл, ниже цена). Только если заметно
                # ниже первого уровня (≥ 1×ATR), иначе уровни сливаются.
                wl_d = range_7d_high - (range_7d_high - e20) * 0.618
                if wl_d < closed_last and (wl_d - wl) >= atr:
                    wait_limit_entry_deep = wl_d
            else:
                wl = range_7d_low + (e20 - range_7d_low) * 0.5
                if wl > closed_last and (wl - closed_last) >= wait_min_dist:
                    wait_limit_entry = wl
                wl_d = range_7d_low + (e20 - range_7d_low) * 0.618
                if wl_d > closed_last and (wl_d - wl) >= atr:
                    wait_limit_entry_deep = wl_d
        tp_beyond_7d = False
        # FIX 2026-09-02: Для ручного контура НЕ ограничиваем TP структурой 7D/30D.
        # Структурное ограничение (ceiling/floor) убивало R:R (SL=2xATR, TP=0.5xATR).
        # Оставляем только 30D unreachable как WARN — пользователь сам решает.
        if side == "LONG":
            if raw_tp > range_30d_high:
                tp_unreachable = True
        else:
            if raw_tp < range_30d_low:
                tp_unreachable = True
        final_tp = raw_tp
    except Exception:
        pass

    # TP reachability + FRESHNESS: % баров за 30D касались TP — НО это недостаточно:
    # уровень мог не посещаться 2 недели (36% набран старыми барами). Считаем
    # дополнительно давность последнего касания и stale-флаг.
    tp_reachability_pct = 0.0
    tp_last_touch_days = 999.0
    tp_stale = False
    touches = []
    try:
        if side == "LONG" and final_tp > 0 and rows30:
            touches = [(int(x[0]), float(x[2])) for x in rows30 if float(x[2]) >= final_tp]
        elif side == "SHORT" and final_tp > 0 and rows30:
            touches = [(int(x[0]), float(x[3])) for x in rows30 if float(x[3]) <= final_tp]
        else:
            touches = []
        tp_reachability_pct = round(len(touches) / max(len(rows30), 1) * 100, 1)
        if touches:
            last_ts = max(t[0] for t in touches)
            tp_last_touch_days = max(0.0, (time.time() - last_ts / 1000) / 86400)
        # stale: уровень не касался дольше 7 дней — TP "мёртвый" для входа сейчас
        tp_stale = 0 < tp_last_touch_days <= 999 and tp_last_touch_days > 7.0
    except Exception:
        pass

    # Пересчёт R:R после ограничения TP (никогда не подгоняем SL)
    sl = closed_last - atr * sl_mult if side == "LONG" else closed_last + atr * sl_mult
    risk = abs(closed_last - sl)
    reward = abs(final_tp - closed_last) if final_tp else 0
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    min_rr = float(load_config().get("min_rr", 1.2))
    if rr < min_rr or risk <= 0:
        rr_invalid = True

    # WAIT_LIMIT: если TP упирается в 7д-экстремум, RR от ТЕКУЩЕЙ цены плохой
    # (цена рядом с вершиной → reward мал). Но вход ЛИМИТНОЙ ЗАЯВКОЙ на откате
    # (wait_limit_entry) восстанавливает RR — при rr_wait >= min_rr сетап
    # валиден как «Поставить LIMIT на откате» (модель владельца). SL не трогаем.
    wait_valid = False
    wait_rr = 0.0
    wait_rr_deep = 0.0
    if tp_beyond_7d and wait_limit_entry > 0:
        w_risk = abs(wait_limit_entry - sl)
        w_reward = abs(final_tp - wait_limit_entry) if final_tp else 0
        wait_rr = round(w_reward / w_risk, 2) if w_risk > 0 else 0.0
        wait_valid = wait_rr >= min_rr
        # B2: RR второго уровня (если лестница валидна — глубокий уровень
        # дальше от цены, поэтому TP-дистанция больше → RR выше)
        if wait_limit_entry_deep > 0:
            w_risk_d = abs(wait_limit_entry_deep - sl)
            w_reward_d = abs(final_tp - wait_limit_entry_deep) if final_tp else 0
            wait_rr_deep = round(w_reward_d / w_risk_d, 2) if w_risk_d > 0 else 0.0
    # WAIT_LIMIT может быть валиден ДАЖЕ при стоп-зоне стохастика: вход лимиткой
    # на откате происходит НИЖЕ текущей цены — стохастик на момент исполнения
    # другой, а заявка ограничена 7д-структурой + экспирацией. Не душим.
    stoch_gate_final = None
    if wait_valid and stoch_zone_block:
        stoch_gate_final = "STOCH_ZONE_CONFLICT_WAIT"

    # P2-fix: Entry Quality Gate — блок входов против свежего импульса
    # FIX 2026-09-02: конфигурируемый флаг. При entry_gate_enabled=false — гейт не блокирует.
    gate_reason = None
    _entry_gate_enabled = bool(cfgq.get("entry_gate_enabled", True))
    if _entry_gate_enabled:
        try:
            from trade.entry_quality_gate import load_candles as _eq_load, gate as _eq_gate
            _df, _meta = _eq_load(symbol)
            # F7 fix: гейт принимает BUY/SELL, scanner выдаёт LONG/SHORT — нормализуем
            # на стороне вызова, не меняя семантику гейта (иначе все импульсные
            # сигналы с 08-12 13:06 подавлялись как NO_DATA: invalid side).
            _gate_side = {"LONG": "BUY", "SHORT": "SELL"}.get(side, side)
            _g = _eq_gate(symbol, _gate_side, time.time(), _df, log=False, meta=_meta)
            if _g["decision"] == "SKIP":
                gate_reason = _g["reason"]
        except Exception:
            gate_reason = None

    # Каноническая классификация причины решения (для телеслоя проверки гипотез):
    # ALLOW / MTF_CONFLICT / STOCH_ZONE_CONFLICT / OTHER — без изменения логики.
    # FIX 2026-08-25: tp_stale (TP не касался >7д) НЕ блокирует — это WARN.
    # Раньше TP_STALE резал все LONG на растущих акциях (TP выше цены касался
    # давно) → бот молчал. Продажа cp считает: цель достижима если в 30D-диапазоне.
    if not (tp_unreachable or rr_invalid or gate_reason or mtf_gate or stoch_gate):
        skip_reason = "ALLOW"
    elif mtf_gate:
        skip_reason = "MTF_CONFLICT"
    elif stoch_gate:
        skip_reason = "STOCH_ZONE_CONFLICT"
    else:
        skip_reason = "OTHER"

    return {
        "signal_id": f"S-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{symbol.replace('USDT','')}",
        "symbol": symbol,
        "side": side,
        "score": total,
        "parts": parts,
        "price": round(closed_last, 6),
        "rsi": round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2),
        "funding": funding,
        "dist_e20_pct": round(dist_to_e20, 2),
        "atr": round(atr, 8),
        "raw_tp": round(raw_tp, 8) if raw_tp else 0.0,
        "final_tp": round(final_tp, 8) if final_tp else 0.0,
        "sl": round(sl, 8) if sl else 0.0,
        "rr": rr,
        "min_rr": min_rr,
        "range_7d_high": round(range_7d_high, 8),
        "range_7d_low": round(range_7d_low, 8),
        "range_30d_high": round(range_30d_high, 8),
        "range_30d_low": round(range_30d_low, 8),
        "tp_unreachable": tp_unreachable,
        "tp_reachability_pct": tp_reachability_pct,
        "tp_touch_count": len(touches),
        "tp_last_touch_days": round(tp_last_touch_days, 1) if tp_last_touch_days < 999 else None,
        "tp_stale": tp_stale,
        "rr_invalid": rr_invalid,
        "tp_beyond_7d": tp_beyond_7d,
        "wait_limit_entry": round(wait_limit_entry, 8) if wait_limit_entry else 0.0,
        "wait_limit_entry_deep": round(wait_limit_entry_deep, 8) if wait_limit_entry_deep else 0.0,
        "gate_reason": gate_reason or mtf_gate or stoch_gate,
        "mtf_gate": mtf_gate,
        "stoch_k": stoch.get("k"),
        "stoch_d": stoch.get("d"),
        "stoch_cross_up": stoch.get("cross_up", False),
        "stoch_cross_down": stoch.get("cross_down", False),
        "stoch_conflict": stoch_conflict,
        "stoch_zone_block": stoch_zone_block,
        "squeeze_on": squeeze.get("squeeze_on", False),
        "squeeze_fired": squeeze.get("fired", False),
        "squeeze_momentum": squeeze.get("momentum", 0.0),
        "h4_trend": mtf.get("h4_trend"),
        "d1_trend": mtf.get("d1_trend"),
        "mtf_agree": mtf.get("agree"),
        "skip_reason": skip_reason,
        # WAIT_LIMIT: маркет-вход отклонён (TP упёрся в 7д-экстремум), но
        # вход ЛИМИТКОЙ на откате даёт RR >= min_rr → предлагаем «Поставить
        # LIMIT» (владелец решает). Приоритет: tp_unreachable(30D) > гейты >
        # WAIT_LIMIT > ALLOW.
        "wait_rr": wait_rr,
        "wait_rr_deep": wait_rr_deep,
        "trade_decision": ("SKIP" if (tp_unreachable or (rr_invalid and not wait_valid)
                                     or gate_reason or mtf_gate or (stoch_gate and not wait_valid))
                           else ("WAIT_LIMIT" if wait_valid else "ALLOW")),
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "why": [
            f"H1 trend: {'UP (price>EMA20>EMA50)' if h1_up else 'DOWN/смешанный'} [+{parts['h1_trend']}]",
            f"M15 structure: {'UP' if m15_up else 'DOWN'} [+{parts['m15_structure']}]",
            f"Momentum: RSI {rsi:.0f} [+{parts['momentum']}]",
            f"Volume: ratio {vol_ratio:.2f} [+{parts['volume']}]",
            f"Funding: {funding:+.6f} [+{parts['funding_oi']}]",
            f"Entry quality: dist-EMA20 {dist_to_e20:+.2f}% [+{parts['entry_quality']}]",
            f"Stochastic: %K {stoch.get('k')} / %D {stoch.get('d')}"
            f"{' (CROSS_UP)' if stoch.get('cross_up') else ''}"
            f"{' (CROSS_DOWN)' if stoch.get('cross_down') else ''}"
            f"{' ❌ ZONE CONFLICT' if stoch_zone_block else ''}",
            f"Squeeze: {'ON' if squeeze.get('squeeze_on') else 'OFF'}"
            f"{' → FIRED' if squeeze.get('fired') else ''}"
            f"{f' (mom {squeeze.get('momentum', 0.0):+.5g})' if squeeze.get('momentum') else ''}",
            f"MTF: H4 {mtf.get('h4_trend') or 'N/A'} / D1 {mtf.get('d1_trend') or 'N/A'}"
            f"{' ✅' if mtf.get('agree') else (' ❌' if mtf.get('agree') is False else '')}",
            f"TradePlan: raw TP {raw_tp:.6g} → final TP {final_tp:.6g} (R:R {rr})",
            f"Gate: {gate_reason or mtf_gate or 'ALLOW'}",
        ],
    }


def _record_decision_trace(sig: dict, client: httpx.Client) -> None:
    """Record full decision trace for validation (shadow mode)."""
    try:
        from tradingos.signals.validation import get_recorder, DecisionTrace
        from tradingos.signals.contour_classifier import ContourThresholds

        recorder = get_recorder()
        thresholds = ContourThresholds()

        # Fetch current price for counterfactual
        cur_px = 0.0
        try:
            rt = client.get("https://api.bybit.com/v5/market/tickers",
                           params={"category": "linear", "symbol": sig["symbol"]})
            cur_px = float((((rt.json().get("result") or {}).get("list") or [{}])[0]).get("lastPrice", 0))
        except Exception:
            cur_px = sig.get("price", 0)

        trace = DecisionTrace(
            ts=time.time(),
            iso=datetime.now(timezone.utc).isoformat(),
            symbol=sig["symbol"],
            direction=sig["side"],
            h1_trend_score=sig.get("parts", {}).get("h1_trend", 0),
            m15_structure_score=sig.get("parts", {}).get("m15_structure", 0),
            momentum_score=sig.get("parts", {}).get("momentum", 0),
            volume_score=sig.get("parts", {}).get("volume", 0),
            funding_oi_score=sig.get("parts", {}).get("funding_oi", 0),
            entry_quality_score=sig.get("parts", {}).get("entry_quality", 0),
            total_score=sig.get("score", 0),
            h1_notional_med=sig.get("h1_notional_med", 0),
            h1_move_pct=sig.get("h1_move_pct", 0),
            data_age_hours=sig.get("data_age_hours", 0),
            quality_passed=sig.get("contour") != "NO_TRADE" or sig.get("skip_reason") == "ALLOW",
            quality_reject_reason=sig.get("skip_reason", "") if sig.get("contour") == "NO_TRADE" else "",
            classifier_version=sig.get("contour_version", "unknown"),
            contour=sig.get("contour", "NO_TRADE"),
            contour_confidence=sig.get("contour_confidence", 0),
            contour_reasoning=sig.get("contour_reasoning", []),
            market_min_momentum_score=thresholds.market_min_momentum_score,
            market_min_vol_ratio=thresholds.market_min_vol_ratio,
            market_min_h1_trend_score=thresholds.market_min_h1_trend_score,
            market_max_dist_to_ema20_pct=thresholds.market_max_dist_to_ema20_pct,
            market_require_mtf_agree=thresholds.market_require_mtf_agree,
            limit_min_pullback_mature=thresholds.limit_min_pullback_mature,
            limit_min_pullback_dist_pct=thresholds.limit_min_pullback_dist_pct,
            limit_min_pullback_dist_atr_mult=thresholds.limit_min_pullback_dist_atr_mult,
            limit_max_rr_from_current=thresholds.limit_max_rr_from_current,
            limit_require_tp_beyond_7d=thresholds.limit_require_tp_beyond_7d,
            limit_min_wait_rr=thresholds.limit_min_wait_rr,
            rejection_reason=sig.get("skip_reason", "") if sig.get("contour") == "NO_TRADE" else "",
            is_stock=sig.get("is_stock", False),
            h4_trend=sig.get("h4_trend"),
            d1_trend=sig.get("d1_trend"),
            mtf_agree=sig.get("mtf_agree"),
            signal_price=sig.get("price", 0),
            wait_limit_entry=sig.get("wait_limit_entry", 0),
            wait_limit_entry_deep=sig.get("wait_limit_entry_deep", 0),
            sl=sig.get("sl", 0),
            final_tp=sig.get("final_tp", 0),
            atr=sig.get("atr", 0),
            vol_ratio=sig.get("vol_ratio", 0),
            dist_to_e20_pct=sig.get("dist_e20_pct", 0),
        )
        recorder.record_decision(trace)
        # Start counterfactual tracking
        recorder.start_counterfactual(trace, cur_px)

        # Update funnel counts for rejections that happen inside classifier
        contour = sig.get("contour", "NO_TRADE")
        skip = sig.get("skip_reason", "")
        # Note: MTF/entry gate/TP/RR rejects are counted in scan_all via skip_reason
        # This is a best-effort; the funnel snapshot is recorded in scan_all
    except Exception as e:
        import logging
        logging.getLogger("ManualScanner").debug(f"Validation trace failed: {e}")


def scan_all() -> list[dict]:
    cfg = load_config()
    symbols = cfg.get("symbols") or []
    min_score = int(cfg.get("min_signal_score", 80))
    found, blocked_tp = [], []
    gated: list[str] = []   # T19: диагностика — кто отсечён фильтрами качества

    # Validation funnel counters
    funnel_counts = {
        "raw_candidates": 0,
        "quality_reject": 0,
        "liquidity_reject": 0,
        "stale_reject": 0,
        "flat_reject": 0,
        "mtf_reject": 0,
        "entry_gate_reject": 0,
        "tp_unreachable": 0,
        "rr_reject": 0,
        "market_candidates": 0,
        "limit_candidates": 0,
        "no_trade": 0,
        "long_candidates": 0,
        "short_candidates": 0,
        "market_long": 0,
        "market_short": 0,
        "limit_long": 0,
        "limit_short": 0,
        "no_trade_long": 0,
        "no_trade_short": 0,
    }

    with httpx.Client() as client:
        for sym in symbols:
            try:
                sig = score_symbol(client, sym, min_score)
                if sig:
                    # Validation: record decision trace
                    if _VALIDATION_ENABLED:
                        _record_decision_trace(sig, client)

                    contour = sig.get("contour", "NO_TRADE")
                    side = sig.get("side", "LONG")

                    if contour == "MARKET":
                        funnel_counts["market_candidates"] += 1
                        if side == "LONG":
                            funnel_counts["market_long"] += 1
                        else:
                            funnel_counts["market_short"] += 1
                    elif contour == "LIMIT":
                        funnel_counts["limit_candidates"] += 1
                        if side == "LONG":
                            funnel_counts["limit_long"] += 1
                        else:
                            funnel_counts["limit_short"] += 1
                    else:
                        funnel_counts["no_trade"] += 1
                        if side == "LONG":
                            funnel_counts["no_trade_long"] += 1
                        else:
                            funnel_counts["no_trade_short"] += 1

                    if side == "LONG":
                        funnel_counts["long_candidates"] += 1
                    else:
                        funnel_counts["short_candidates"] += 1

                    if sig.get("trade_decision") == "SKIP":
                        blocked_tp.append(sig)
                    else:
                        found.append(sig)
                else:
                    # Диагностика фильтра (для лога, не для карточки)
                    # Also count quality rejects
                    try:
                        h1 = _klines(client, sym, "60", 96)
                        if h1:
                            h1_vols = sorted((b["close"] * b["volume"]) for b in h1)
                            med = h1_vols[len(h1_vols) // 2]
                            age = (time.time() * 1000 - h1[-1]["ts"]) / 3_600_000
                            rng = max(b["high"] for b in h1[-3:]) - min(b["low"] for b in h1[-3:])
                            move = rng / h1[-1]["close"] * 100 if h1[-1]["close"] else 0
                            mn = float(cfg.get("min_h1_notional_usd", 200_000) or 200_000)
                            mvp = float(cfg.get("min_h1_move_pct", 0.25) or 0.25)
                            funnel_counts["raw_candidates"] += 1
                            if med < mn:
                                funnel_counts["quality_reject"] += 1
                                funnel_counts["liquidity_reject"] += 1
                                gated.append(f"{sym}:LIQ${med/1000:.0f}k")
                            elif age > 3:
                                funnel_counts["quality_reject"] += 1
                                funnel_counts["stale_reject"] += 1
                                gated.append(f"{sym}:STALE")
                            elif move < mvp:
                                funnel_counts["quality_reject"] += 1
                                funnel_counts["flat_reject"] += 1
                                gated.append(f"{sym}:FLAT{move:.2f}%")
                            else:
                                funnel_counts["raw_candidates"] += 1
                                gated.append(f"{sym}:NO_SETUP")
                    except Exception:
                        pass
            except Exception:
                continue

    # Record funnel snapshot
    if _VALIDATION_ENABLED and funnel_counts["raw_candidates"] > 0:
        from tradingos.signals.validation import get_recorder, FunnelSnapshot
        recorder = get_recorder()
        now = time.time()
        snapshot = FunnelSnapshot(
            ts=now,
            iso=datetime.now(timezone.utc).isoformat(),
            **funnel_counts
        )
        recorder.record_funnel(snapshot)

    found.sort(key=lambda s: -s["score"])
    if gated:
        print(f"[scanner] отсечено фильтрами качества ({len(gated)}): {', '.join(gated[:40])}")
    if blocked_tp:
        # Статистика отклонённых по TP (в журнал)
        for s in blocked_tp:
            reason = []
            if s.get("tp_unreachable"):
                reason.append("TP_UNREACHABLE")
            if s.get("tp_stale"):
                reason.append(f"TP_STALE_{s.get('tp_last_touch_days', '?')}d")
            if s.get("rr_invalid"):
                reason.append("RR_INVALID")
            log_signal(s, "BLOCKED_TRADE_CONSTRUCTION",
                       {"reason": "+".join(reason) if reason else "NO_MARKET_TARGET"})
    return found


def log_signal(sig: dict, event: str, extra: dict | None = None) -> None:
    rec = {"event": event, **sig}
    if extra:
        rec.update(extra)
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    res = scan_all()
    print(f"Найдено сигналов >= {load_config().get('min_signal_score')}: {len(res)}")
    for s in res:
        print(f"  {s['symbol']} {s['side']} score={s['score']} price={s['price']}")
