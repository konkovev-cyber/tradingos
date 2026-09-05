"""
engine_v2.py — Profit-Oriented Trade Engine v2 (SHADOW-ONLY).

Новый слой между кандидатом и исполнением. НЕ исполняет ордера, НЕ меняет
AUTO/executor/guardian/risk. Считает решение NEW (MARKET/LIMIT_PULLBACK/
BREAKOUT_RETEST/WAIT/SKIP) + TradePlan и логирует CURRENT vs NEW.

Принципы (по ТЗ пользователя):
  - Сигнал = только кандидат, НЕ решение.
  - Entry Quality зависит от типа setup (НЕ универсальные hard-пороги).
  - TP1 = ближайшая реалистичная рыночная цель (структура/swing/range),
    НЕ 4×ATR, НЕ исторический экстремум.
  - SL = структурный invalidation + buffer, НЕ просто 2×ATR.
  - ProfitEngine: TP1 + runner + giveback/thesis decay решения.
  - SKIP с причиной (LATE_MOMENTUM/NO_ROOM/NO_TARGET/CHASE_RISK/...).
"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"

# ---------------------------------------------------------------------------
# MARKET CONTEXT (переиспользуем rolling-признаки из кэша)
# ---------------------------------------------------------------------------

def load_candles(symbol: str) -> pd.DataFrame | None:
    p = CACHE_DIR / f"{symbol}_M15.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if df["ts"].iloc[0] > 1e12:
        df["ts"] = df["ts"] / 1000
    tr = pd.concat([
        (df["h"] - df["l"]),
        (df["h"] - df["c"].shift(1)).abs(),
        (df["l"] - df["c"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(30).mean()
    df["hi40"] = df["h"].rolling(40).max().shift(1)
    df["lo40"] = df["l"].rolling(40).min().shift(1)
    df["hi20"] = df["h"].rolling(24).max().shift(1)
    df["lo20"] = df["l"].rolling(24).min().shift(1)
    df["vm"] = df["v"].rolling(60).median().shift(1)
    df["e20"] = df["c"].ewm(span=20, adjust=False).mean()
    df["e50"] = df["c"].ewm(span=50, adjust=False).mean()
    return df


def bar_context(df: pd.DataFrame, ts: float) -> dict | None:
    """Все признаки бара ДО ts (без look-ahead). Возвращает dict или None."""
    pre = df[df["ts"] <= ts - 60]
    if len(pre) < 60:
        return None
    b = pre.iloc[-1]
    price = float(b["c"])
    atr = float(b["atr"]) if pd.notna(b["atr"]) else 0.0
    if atr <= 0:
        return None
    hi40 = float(b["hi40"]) if pd.notna(b["hi40"]) else price
    lo40 = float(b["lo40"]) if pd.notna(b["lo40"]) else price
    hi20 = float(b["hi20"]) if pd.notna(b["hi20"]) else price
    lo20 = float(b["lo20"]) if pd.notna(b["lo20"]) else price
    vm = float(b["vm"]) if pd.notna(b["vm"]) else float(b["v"])
    e20 = float(b["e20"]) if pd.notna(b["e20"]) else price
    e50 = float(b["e50"]) if pd.notna(b["e50"]) else price
    return {
        "ts": float(b["ts"]), "price": price, "atr": atr,
        "hi40": hi40, "lo40": lo40, "hi20": hi20, "lo20": lo20,
        "vm": vm, "vol": float(b["v"]), "e20": e20, "e50": e50,
        "h": float(b["h"]), "l": float(b["l"]), "o": float(b["o"]),
    }


# ---------------------------------------------------------------------------
# REGIME
# ---------------------------------------------------------------------------

def market_regime(ctx: dict) -> str:
    """TREND_UP/TREND_DOWN/RANGE/BREAKOUT/EXHAUSTION/NO_STRUCTURE (простая версия)."""
    price, e20, e50 = ctx["price"], ctx["e20"], ctx["e50"]
    atr = ctx["atr"]
    rng = ctx["hi20"] - ctx["lo20"]
    if rng / atr > 8.0:  # 20-барный диапазон шире 8 ATR — структуры нет
        return "NO_STRUCTURE"
    if e20 > e50 and price > e20:
        # тренд вверх; exhaustion если цена далеко выше e20 (>2.5 ATR)
        if (price - e20) / atr > 2.5:
            return "EXHAUSTION"
        return "TREND_UP"
    if e20 < e50 and price < e20:
        if (e20 - price) / atr > 2.5:
            return "EXHAUSTION"
        return "TREND_DOWN"
    return "RANGE"


# ---------------------------------------------------------------------------
# SETUP DISCOVERY
# ---------------------------------------------------------------------------

def setup_discovery(ctx: dict, side: str, df: pd.DataFrame | None = None) -> dict | None:
    """Определить setup. Возвращает {type, zone, trigger, invalidation, notes}."""
    price, atr = ctx["price"], ctx["atr"]
    hi40, lo40 = ctx["hi40"], ctx["lo40"]
    vol, vm = ctx["vol"], ctx["vm"]
    up = side == "BUY"

    # B_BREAKOUT_RETEST: цена в зоне пробоя уровня (±1 ATR), не только строго выше
    if up and price > hi40 - 1.0 * atr:
        return {"type": "BREAKOUT_RETEST" if price <= hi40 + 1.5 * atr else "BREAKOUT",
                "zone": f"{hi40:.8g}", "trigger": "close in/above breakout zone",
                "invalidation": f"{hi40 - 1.5 * atr:.8g}",
                "notes": "breakout / retest candidate"}
    if not up and price < lo40 + 1.0 * atr:
        return {"type": "BREAKOUT_RETEST" if price >= lo40 - 1.5 * atr else "BREAKOUT",
                "zone": f"{lo40:.8g}", "trigger": "close in/below breakdown zone",
                "invalidation": f"{lo40 + 1.5 * atr:.8g}",
                "notes": "breakout / retest candidate"}

    # A_TREND_PULLBACK: тренд + цена у EMA20
    e20, e50 = ctx["e20"], ctx["e50"]
    if up and e20 > e50 and abs(price - e20) / atr < 1.2 and price > e20:
        return {"type": "TREND_PULLBACK", "zone": f"{e20:.8g}", "trigger": "pullback hold",
                "invalidation": f"{e20 - 1.5 * atr:.8g}", "notes": "pullback in uptrend"}
    if not up and e20 < e50 and abs(price - e20) / atr < 1.2 and price < e20:
        return {"type": "TREND_PULLBACK", "zone": f"{e20:.8g}", "trigger": "pullback hold",
                "invalidation": f"{e20 + 1.5 * atr:.8g}", "notes": "pullback in downtrend"}

    # E_LIQUIDITY_SWEEP_RECLAIM: intrabar sweep экстремума + закрытие обратно
    if up and ctx["h"] > hi40 and price < hi40:
        return {"type": "LIQ_SWEEP", "zone": f"{hi40:.8g}", "trigger": "sweep+reclaim",
                "invalidation": f"{hi40 - 2 * atr:.8g}", "notes": "liquidity sweep upward"}
    if not up and ctx["l"] < lo40 and price > lo40:
        return {"type": "LIQ_SWEEP", "zone": f"{lo40:.8g}", "trigger": "sweep+reclaim",
                "invalidation": f"{lo40 + 2 * atr:.8g}", "notes": "liquidity sweep downward"}

    # C_RANGE_REVERSAL: в диапазоне + откат от границы
    rng = ctx["hi20"] - ctx["lo20"]
    if rng / atr < 4.5:
        if up and (price - ctx["lo20"]) / max(rng, 1e-9) < 0.30:
            return {"type": "RANGE_REVERSAL", "zone": f"{ctx['lo20']:.8g}",
                    "trigger": "rejection at range low", "invalidation": f"{ctx['lo20'] - 1.5 * atr:.8g}",
                    "notes": "range reversal at lower bound"}
        if not up and (ctx["hi20"] - price) / max(rng, 1e-9) < 0.30:
            return {"type": "RANGE_REVERSAL", "zone": f"{ctx['hi20']:.8g}",
                    "trigger": "rejection at range high", "invalidation": f"{ctx['hi20'] + 1.5 * atr:.8g}",
                    "notes": "range reversal at upper bound"}

    # G_LATE_MOMENTUM (анти-KMNO): объёмный импульс в направлении был >60 мин
    # назад и follow-through отсутствует — цена «догоняет» уже ушедшее движение.
    # Это НЕ hard-запрет для всех (pullback может быть старым), а маркер для
    # EntryQuality: MARKET запрещён, возможен только WAIT/LIMIT.
    vol, vm = ctx["vol"], ctx["vm"]
    fav = "UP" if up else "DOWN"
    imp_ts = None
    for b in [df.iloc[j] for j in range(max(0, len(df) - 24), len(df))]:
        if b["ts"] > ctx["ts"] - 60:
            continue
        if b["v"] > 2.2 * vm and b["c"] != b["o"]:
            b_up = b["c"] > b["o"]
            if (fav == "UP" and b_up) or (fav == "DOWN" and not b_up):
                imp_ts = b["ts"]
    if imp_ts is not None and (ctx["ts"] - imp_ts) / 60 > 60:
        return {"type": "LATE_MOMENTUM", "zone": f"{imp_ts:.0f}",
                "trigger": "stale impulse, no follow-through",
                "invalidation": f"{price - 2 * atr:.8g}",
                "notes": "impulse >60min ago"}

    return None


# ---------------------------------------------------------------------------
# ENTRY QUALITY (зависит от setup, не универсальные пороги)
# ---------------------------------------------------------------------------

def entry_quality(ctx: dict, side: str, setup: str) -> dict:
    """Оценка качества цены входа. Возвращает quality + reasons."""
    price, atr = ctx["price"], ctx["atr"]
    e20 = ctx["e20"]
    dev = (price - e20) / atr if side == "BUY" else (e20 - price) / atr
    room_up = (ctx["hi40"] - price) / atr if ctx["hi40"] > price else 0
    room_dn = (price - ctx["lo40"]) / atr if price > ctx["lo40"] else 0
    room = room_up if side == "BUY" else room_dn
    follow = None

    reasons = []
    quality = "GOOD"

    if setup in ("BREAKOUT", "BREAKOUT_RETEST"):
        # breakout: расширение допустимо; важно подтверждение + room
        if room < 1.5:
            quality = "POOR"; reasons.append("NO_ROOM")
        if ctx["price"] > ctx["hi40"] and ctx["h"] == ctx["price"]:
            reasons.append("AT_LEVEL")  # не обязательно плохо
    elif setup == "TREND_PULLBACK":
        if dev > 1.5:
            quality = "POOR"; reasons.append("CHASE_RISK")
    elif setup == "LIQ_SWEEP":
        if room < 1.0:
            quality = "POOR"; reasons.append("NO_ROOM")
    elif setup == "RANGE_REVERSAL":
        if dev > 1.5:
            quality = "POOR"; reasons.append("CHASE_RISK")

    return {"quality": quality, "room_atr": round(room, 2), "dev_atr": round(dev, 2),
            "reasons": reasons, "follow_through": follow}


# ---------------------------------------------------------------------------
# TARGET / TP1 (market-based)
# ---------------------------------------------------------------------------

def realistic_target(ctx: dict, side: str) -> float:
    """TP1 = ближайшая реалистичная структура до входа (не экстремум)."""
    price, atr = ctx["price"], ctx["atr"]
    if side == "BUY":
        above = [v for v in (ctx["hi20"], ctx["hi40"]) if v > price]
        tgt = (min(above) if above else price + 2 * atr) - atr * 0.3
    else:
        below = [v for v in (ctx["lo20"], ctx["lo40"]) if v < price]
        tgt = (max(below) if below else price - 2 * atr) + atr * 0.3
    return tgt


def structural_sl(ctx: dict, side: str) -> float:
    """SL = структурный invalidation + buffer."""
    price, atr = ctx["price"], ctx["atr"]
    if side == "BUY":
        # ниже ближайшего swing low (lo20) с buffer
        return ctx["lo20"] - atr * 0.5 if ctx["lo20"] < price else price - 1.5 * atr
    return ctx["hi20"] + atr * 0.5 if ctx["hi20"] > price else price + 1.5 * atr


# ---------------------------------------------------------------------------
# MAIN ENTRY DECISION
# ---------------------------------------------------------------------------

def decide(candidate: dict, df: pd.DataFrame | None) -> dict:
    """Основное решение по кандидату. Возвращает полный TradePlan-решение."""
    symbol = candidate["symbol"]
    side = candidate["side"]
    ts = candidate["ts"]
    rec = {"symbol": symbol, "side": side, "ts": ts, "setup": None,
           "regime": None, "entry_method": "SKIP", "skip_reason": None,
           "quality": None, "sl": None, "tp1": None, "runner": "NONE",
           "room_r": None, "expiry": None, "reason": []}

    if df is None:
        rec["skip_reason"] = "NO_DATA"; return rec
    ctx = bar_context(df, ts)
    if ctx is None:
        rec["skip_reason"] = "NO_DATA"; return rec

    rec["regime"] = regime = market_regime(ctx)
    if regime == "NO_STRUCTURE":
        rec["skip_reason"] = "NO_STRUCTURE"; return rec
    # EXHAUSTION: не запрещает всё, но запрещает blind MARKET (вход ниже)
    setup = setup_discovery(ctx, side, df)
    rec["setup"] = setup["type"] if setup else None
    if setup is None:
        rec["skip_reason"] = "NO_SETUP"; return rec

    eq = entry_quality(ctx, side, setup["type"])
    rec["quality"] = eq["quality"]

    tp1 = realistic_target(ctx, side)
    sl = structural_sl(ctx, side)
    risk = abs(ctx["price"] - sl)
    reward = abs(tp1 - ctx["price"])
    room_r = reward / risk if risk > 0 else 0
    rec["sl"], rec["tp1"], rec["room_r"] = sl, tp1, round(room_r, 2)

    # Экономика: цель должна покрывать издержки (fees 0.11%×2 ≈ 0.2R минимум)
    fee_r = 0.0011 * ctx["price"] / max(risk, 1e-9)
    if room_r < max(0.5, fee_r * 2 + 0.2):
        rec["skip_reason"] = "NO_TARGET"
        rec["reason"].append(f"room {room_r:.2f}R too small vs fees {fee_r:.2f}R")
        return rec

    # Тип входа по setup + качеству (НЕ universal hard-пороги)
    if eq["quality"] == "POOR":
        # расширенный вход: не MARKET, но можно WAIT/LIMIT если зона есть
        if setup["type"] in ("TREND_PULLBACK", "RANGE_REVERSAL"):
            rec["entry_method"] = "LIMIT_PULLBACK"
            rec["expiry"] = int(time.time()) + 5400  # 90 мин
            rec["reason"].append(f"expanded ({eq['dev_atr']:.1f}ATR) -> limit pullback")
        else:
            rec["entry_method"] = "WAIT"
            rec["expiry"] = int(time.time()) + 3600
            rec["reason"].append(f"poor entry quality ({eq['reasons']}) -> wait")
    else:
        if setup["type"] == "BREAKOUT_RETEST":
            rec["entry_method"] = "BREAKOUT_RETEST"
            rec["expiry"] = int(time.time()) + 5400
            rec["reason"].append("breakout+retest confirmation")
        elif setup["type"] == "LIQ_SWEEP":
            rec["entry_method"] = "MARKET"
            rec["reason"].append("sweep+reclaim, room ok")
        else:
            rec["entry_method"] = "MARKET"
            rec["reason"].append(f"setup {setup['type']}, quality {eq['quality']}")

    rec["runner"] = "ACTIVE" if rec["entry_method"] != "SKIP" else "NONE"
    return rec


# ---------------------------------------------------------------------------
# PROFIT ENGINE (минимальный — решения по открытой позиции)
# ---------------------------------------------------------------------------

def profit_decision(mfe_r: float, current_r: float, momentum_broken: bool,
                    structure_broken: bool) -> str:
    """Возвращает HOLD/PROTECT/TAKE/TRAIL/EXIT."""
    giveback = mfe_r - current_r
    if structure_broken:
        return "EXIT"
    if mfe_r >= 2.0 and current_r >= 1.0:
        return "TRAIL"
    if mfe_r >= 1.0 and giveback >= 0.7 and momentum_broken:
        return "TAKE"
    if mfe_r >= 1.0 and giveback >= 1.0:
        return "TAKE"
    if current_r >= 1.5 and momentum_broken:
        return "PROTECT"
    return "HOLD"
