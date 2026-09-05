"""
vwap_sr.py — VWAP-Bounce стратегия + Support/Resistance уровни (T9/T10).

Стратегии:
1. VWAP-Bounce: цена выше VWAP → бычий тренд;
   откат к VWAP + подтверждающая свеча → LONG (и наоборот для SHORT).
2. S/R-уровни: горизонтальные уровни по пивотам (локальные экстр.)
   с касанием-контактами (touches). Вход на отскоке от уровня.

Интеграция: run_observation передаёт candles + vwap из FeatureStore.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

log = logging.getLogger("VWAP_SR")


@dataclass
class SRLevel:
    price: float
    touches: int          # сколько раз цена коснулась уровня
    strength: float       # 0-1, растёт с числом касаний и объёмом
    kind: str             # "support" | "resistance"
    last_touch_idx: int   # индекс последнего касания


@dataclass
class VWAPSignal:
    direction: str         # "LONG" | "SHORT" | None
    reason: str
    vwap: float
    close: float
    bounce_quality: float  # 0-1 качество отскока


class VWAPBounceDetector:
    """
    VWAP-стратегия (Mind Math Money):
    1. Тренд: close > VWAP → покупатели, close < VWAP → продавцы.
    2. Вход: откат к VWAP + отскок подтверждающей свечой.
    3. Стоп: за локальным экстремумом.
    """

    def __init__(self, touch_tolerance_pct: float = 0.002,
                 min_bounce_body_pct: float = 0.5):
        # Допуск касания VWAP: 0.2% цены
        self.touch_tol = touch_tolerance_pct
        # Подтверждающая свеча: тело ≥ 50% диапазона
        self.min_body_pct = min_bounce_body_pct

    def detect(self, candles: List[dict], vwap: float) -> Optional[VWAPSignal]:
        """
        candles: последние N свечей (dict с open/high/low/close).
        vwap: текущее значение VWAP из FeatureStore.
        Нужны минимум 3 свечи: [откат, касание, подтверждение].
        """
        if not candles or len(candles) < 3 or not vwap or vwap <= 0:
            return None

        c_confirm, c_touch, c_prior = candles[-1], candles[-2], candles[-3]
        tol = vwap * self.touch_tol

        near_vwap = lambda c: abs(c["close"] - vwap) <= tol or (c["low"] <= vwap <= c["high"])

        # ─── LONG: тренд выше VWAP, откат, подтверждение ───
        if (c_prior["close"] > vwap                      # тренд бычий
                and c_touch["low"] <= vwap + tol          # касание VWAP
                and c_confirm["close"] > vwap             # закрылись выше VWAP
                and c_confirm["close"] > c_confirm["open"]):  # бычья свеча
            rng = c_confirm["high"] - c_confirm["low"]
            body = abs(c_confirm["close"] - c_confirm["open"])
            quality = (body / rng) if rng > 0 else 0.0
            if quality >= self.min_body_pct:
                return VWAPSignal("LONG",
                                  "VWAP-bounce LONG: тренд вверх, откат к VWAP, бычье подтверждение",
                                  vwap, c_confirm["close"], quality)

        # ─── SHORT: тренд ниже VWAP, откат, подтверждение ───
        if (c_prior["close"] < vwap
                and c_touch["high"] >= vwap - tol
                and c_confirm["close"] < vwap
                and c_confirm["close"] < c_confirm["open"]):
            rng = c_confirm["high"] - c_confirm["low"]
            body = abs(c_confirm["close"] - c_confirm["open"])
            quality = (body / rng) if rng > 0 else 0.0
            if quality >= self.min_body_pct:
                return VWAPSignal("SHORT",
                                  "VWAP-bounce SHORT: тренд вниз, откат к VWAP, медвежье подтверждение",
                                  vwap, c_confirm["close"], quality)

        return None


class SRLevelDetector:
    """
    Support & Resistance по пивотам (Support & Resistance Pro подход).
    Уровень = зона пивотов. Сила = число касаний + объём.
    """

    def __init__(self, pivot_window: int = 5, merge_tolerance_pct: float = 0.003,
                 max_levels: int = 6):
        self.pivot_win = pivot_window
        self.merge_tol = merge_tolerance_pct
        self.max_levels = max_levels

    def _find_pivots(self, candles: List[dict]) -> tuple:
        """Локальные maxima/minima highs/lows."""
        highs, lows = [], []
        w = self.pivot_win
        n = len(candles)
        for i in range(w, n - w):
            window = candles[i - w:i + w + 1]
            if candles[i]["high"] >= max(c["high"] for c in window):
                highs.append((i, candles[i]["high"]))
            if candles[i]["low"] <= min(c["low"] for c in window):
                lows.append((i, candles[i]["low"]))
        return highs, lows

    def _merge_levels(self, pivot_prices: List[float],
                      direction: str, candles: List[dict]) -> List[SRLevel]:
        """Слить близкие пивоты в один уровень, посчитать касания."""
        levels: List[SRLevel] = []
        for price in sorted(pivot_prices, reverse=(direction == "resistance")):
            merged = False
            for lv in levels:
                if abs(price - lv.price) / lv.price <= self.merge_tol:
                    lv.touches += 1
                    lv.strength = min(1.0, lv.touches * 0.25)
                    merged = True
                    break
            if not merged:
                levels.append(SRLevel(price, 1, 0.25, direction, 0))

        # Посчитать фактические касания каждого уровня по всей истории
        for lv in levels:
            tol = lv.price * self.merge_tol * 2
            touches = sum(
                1 for c in candles
                if c["low"] <= lv.price + tol and c["high"] >= lv.price - tol
            )
            lv.touches = max(lv.touches, touches)
            lv.strength = min(1.0, lv.touches * 0.2)
            lv.last_touch_idx = max(
                (i for i, c in enumerate(candles)
                 if c["low"] <= lv.price + tol and c["high"] >= lv.price - tol),
                default=0,
            )
        return levels

    def detect(self, candles: List[dict]) -> List[SRLevel]:
        """Вернуть топ уровней (support + resistance) по силе."""
        if len(candles) < self.pivot_win * 2 + 4:
            return []
        highs, lows = self._find_pivots(candles)
        res_levels = self._merge_levels([p for _, p in highs], "resistance", candles)
        sup_levels = self._merge_levels([p for _, p in lows], "support", candles)
        all_levels = res_levels + sup_levels
        all_levels.sort(key=lambda l: l.strength * l.touches, reverse=True)
        return all_levels[:self.max_levels]

    def nearest_levels(self, candles: List[dict], price: float) -> dict:
        """Ближайшие уровни относительно текущей цены."""
        levels = self.detect(candles)
        support = [l for l in levels if l.price < price]
        resistance = [l for l in levels if l.price > price]
        return {
            "nearest_support": support[0] if support else None,
            "nearest_resistance": resistance[0] if resistance else None,
            "all": levels,
        }


class FalseBreakDetector:
    """
    FALSE BREAK (ложный пробой) — стратегия Craig Percoco.

    Логика: цена пробивает сильный уровень, но свеча закрывается
    обратно за уровнем → ловушка для пробойных трейдеров, движение
    в обратную сторону.

    Бычий FALSE BREAK (LONG):
      - Уровень поддержки, минимум свечи пробил его вниз
      - ЗАКРЫТИЕ вернулось выше уровня (пробой ложный)
      - Уровень сильный (много касаний) → вход LONG

    Медвежий FALSE BREAK (SHORT):
      - Уровень сопротивления, максимум свечи пробил вверх
      - ЗАКРЫТИЕ вернулось ниже уровня
      - Уровень сильный → вход SHORT
    """

    def __init__(self, min_touches: int = 3, wick_penetration_pct: float = 0.001,
                 min_level_strength: float = 0.4):
        # Уровень значим, если касаний ≥3
        self.min_touches = min_touches
        # Минимальная глубина прокола за уровень (0.1% цены)
        self.wick_pen = wick_penetration_pct
        self.min_strength = min_level_strength
        # Собственный S/R-детектор для поиска уровней
        self._sr = SRLevelDetector()

    def detect(self, candles: List[dict]) -> Optional[dict]:
        """
        Проверить последнюю ЗАКРЫТУЮ свечу на ложный пробой ближайшего уровня.
        Возвращает dict(direction, level, reason, quality) или None.
        """
        if not candles or len(candles) < self._sr.pivot_win * 2 + 4:
            return None
        c = candles[-1]
        rng = c["high"] - c["low"]
        if rng <= 0:
            return None

        levels = self._sr.detect(candles)
        for lv in levels:
            if lv.touches < self.min_touches or lv.strength < self.min_strength:
                continue
            tol = lv.price * self.wick_pen

            # Бычий ложный пробой поддержки: низ проколол уровень,
            # но закрылись выше него
            if lv.kind == "support" and c["low"] < lv.price - tol \
                    and c["close"] > lv.price:
                depth = (lv.price - c["low"]) / rng  # доля тени ниже уровня
                return {
                    "direction": "LONG",
                    "pattern": "FALSE_BREAK_SUPPORT",
                    "level": lv.price,
                    "touches": lv.touches,
                    "depth_pct": round(depth, 3),
                    "close": c["close"],
                    "reason": f"False break support {lv.price:.6g} "
                              f"(touches={lv.touches}, wick-depth {depth:.0%})",
                }

            # Медвежий ложный пробой сопротивления
            if lv.kind == "resistance" and c["high"] > lv.price + tol \
                    and c["close"] < lv.price:
                depth = (c["high"] - lv.price) / rng
                return {
                    "direction": "SHORT",
                    "pattern": "FALSE_BREAK_RESISTANCE",
                    "level": lv.price,
                    "touches": lv.touches,
                    "depth_pct": round(depth, 3),
                    "close": c["close"],
                    "reason": f"False break resistance {lv.price:.6g} "
                              f"(touches={lv.touches}, wick-depth {depth:.0%})",
                }
        return None


class SMA20BounceDetector:
    """
    SMA(20) Bounce — Kristina Forex / LiveFree FX:
    1. Тренд: close > EMA20 → бычий, close < EMA20 → медвежий.
    2. Откат к EMA20 (цена касается/пересекает).
    3. Подтверждающая свеча в сторону тренда → вход.
    """

    def __init__(self, touch_tolerance_pct: float = 0.002,
                 min_bounce_body_pct: float = 0.5):
        self.touch_tol = touch_tolerance_pct
        self.min_body_pct = min_bounce_body_pct

    def detect(self, candles: List[dict], ema20: float) -> Optional[VWAPSignal]:
        if not candles or len(candles) < 3 or not ema20 or ema20 <= 0:
            return None
        c_confirm, c_touch, c_prior = candles[-1], candles[-2], candles[-3]
        tol = ema20 * self.touch_tol

        # LONG: тренд выше EMA20, откат к EMA20, подтверждение
        if (c_prior["close"] > ema20
                and c_touch["low"] <= ema20 + tol
                and c_confirm["close"] > ema20
                and c_confirm["close"] > c_confirm["open"]):
            rng = c_confirm["high"] - c_confirm["low"]
            body = abs(c_confirm["close"] - c_confirm["open"])
            quality = (body / rng) if rng > 0 else 0.0
            if quality >= self.min_body_pct:
                return VWAPSignal("LONG",
                    "SMA20-bounce LONG: тренд вверх, откат к EMA20, бычье подтверждение",
                    ema20, c_confirm["close"], quality)

        # SHORT: тренд ниже EMA20, откат вверх, подтверждение
        if (c_prior["close"] < ema20
                and c_touch["high"] >= ema20 - tol
                and c_confirm["close"] < ema20
                and c_confirm["close"] < c_confirm["open"]):
            rng = c_confirm["high"] - c_confirm["low"]
            body = abs(c_confirm["close"] - c_confirm["open"])
            quality = (body / rng) if rng > 0 else 0.0
            if quality >= self.min_body_pct:
                return VWAPSignal("SHORT",
                    "SMA20-bounce SHORT: тренд вниз, откат к EMA20, медвежье подтверждение",
                    ema20, c_confirm["close"], quality)
        return None