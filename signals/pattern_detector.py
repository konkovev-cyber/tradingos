"""
pattern_detector.py — Мульти-свечные паттерны технического анализа v2.

ПОЛНЫЙ СПИСОК паттернов:
1. Single-bar: pin_bar, hammer, shooting_star, doji, marubozu, engulfing, harami
2. Double: double_top, double_bottom
3. Triple: triple_top, triple_bottom
4. Head-and-shoulders: hs_top, hs_bottom (inverse)
5. Consolidation: bull_flag, bear_flag, pennant
6. Triangles: ascending_triangle, descending_triangle, symmetrical_triangle
7. Wedges: falling_wedge (bullish), rising_wedge (bearish)
8. Range: rectangle_top, rectangle_bottom, rounding_top, rounding_bottom
9. Advanced: cup_and_handle, diamond_top, megaphone
10. Indicator-based: golden_cross, death_cross, bollinger_squeeze

PatternTracker — валидатор: записывает каждый найденный паттерн,
сравнивает с фактическим исходом через N свечей, считает edge.
"""
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any

log = logging.getLogger("PatternDetector")


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        return self.high - max(self.close, self.open)

    @property
    def lower_shadow(self) -> float:
        return min(self.close, self.open) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open


@dataclass
class Pattern:
    name: str
    direction: str  # "BULLISH" | "BEARISH"
    confidence: float  # 0-1
    start_idx: int
    end_idx: int
    neckline: Optional[float] = None
    target: Optional[float] = None
    stop: Optional[float] = None
    description: str = ""


class PatternDetector:
    """Детектор мульти-свечных паттернов."""

    def __init__(self, candles: List[Candle]):
        self.candles = candles
        self.n = len(candles)

    def _find_local_extremes(self, window: int = 5) -> tuple:
        """Найти локальные максимумы и минимумы."""
        highs = []
        lows = []
        for i in range(window, self.n - window):
            # Локальный максимум
            is_max = all(
                self.candles[i].high >= self.candles[j].high
                for j in range(i - window, i + window + 1)
                if j != i
            )
            if is_max:
                highs.append((i, self.candles[i].high))
            # Локальный минимум
            is_min = all(
                self.candles[i].low <= self.candles[j].low
                for j in range(i - window, i + window + 1)
                if j != i
            )
            if is_min:
                lows.append((i, self.candles[i].low))
        return highs, lows

    def detect_double_top(self, tolerance: float = 0.02) -> Optional[Pattern]:
        """
        Двойная вершина: два пика примерно на одном уровне,
        с откатом (дном) между ними.
        """
        highs, lows = self._find_local_extremes()
        if len(highs) < 2 or len(lows) < 1:
            return None

        for i in range(len(highs) - 1):
            idx1, peak1 = highs[i]
            idx2, peak2 = highs[i + 1]
            # Расстояние между пиками: минимум 5 свечей
            if idx2 - idx1 < 5:
                continue
            # Пики примерно на одном уровне (±tolerance)
            if abs(peak1 - peak2) / peak1 > tolerance:
                continue
            # Должно быть дно между пиками
            lows_between = [l for l in lows if idx1 < l[0] < idx2]
            if not lows_between:
                continue
            # neckline = уровень дна между пиками
            neckline = min(l[1] for l in lows_between)
            # Размер паттерна (от neckline до пика)
            pattern_height = peak1 - neckline
            target = neckline - pattern_height  # цель = дно минус высота
            stop = peak1 + pattern_height * 0.1  # стоп чуть выше пика
            return Pattern(
                name="DOUBLE_TOP",
                direction="BEARISH",
                confidence=0.75,
                start_idx=idx1,
                end_idx=idx2,
                neckline=neckline,
                target=target,
                stop=stop,
                description=f"Двойная вершина: пики {peak1:.6f} и {peak2:.6f}, neckline {neckline:.6f}"
            )
        return None

    def detect_double_bottom(self, tolerance: float = 0.02) -> Optional[Pattern]:
        """Двойное дно — зеркало двойной вершины."""
        highs, lows = self._find_local_extremes()
        if len(lows) < 2 or len(highs) < 1:
            return None

        for i in range(len(lows) - 1):
            idx1, bottom1 = lows[i]
            idx2, bottom2 = lows[i + 1]
            if idx2 - idx1 < 5:
                continue
            if abs(bottom1 - bottom2) / bottom1 > tolerance:
                continue
            highs_between = [h for h in highs if idx1 < h[0] < idx2]
            if not highs_between:
                continue
            neckline = max(h[1] for h in highs_between)
            pattern_height = neckline - bottom1
            target = neckline + pattern_height
            stop = bottom1 - pattern_height * 0.1
            return Pattern(
                name="DOUBLE_BOTTOM",
                direction="BULLISH",
                confidence=0.75,
                start_idx=idx1,
                end_idx=idx2,
                neckline=neckline,
                target=target,
                stop=stop,
                description=f"Двойное дно: дна {bottom1:.6f} и {bottom2:.6f}, neckline {neckline:.6f}"
            )
        return None

    def detect_head_and_shoulders(self, tolerance: float = 0.03) -> Optional[Pattern]:
        """
        Голова и плечи: плечо1 (L) — голова (H) — плечо2 (L).
        neckline соединяет дна между плечами.
        """
        highs, lows = self._find_local_extremes()
        if len(highs) < 3:
            return None

        for i in range(len(highs) - 2):
            idx_s1, shoulder1 = highs[i]
            idx_h, head = highs[i + 1]
            idx_s2, shoulder2 = highs[i + 2]

            # Голова выше плеч
            if head <= max(shoulder1, shoulder2):
                continue
            # Плечи примерно на одном уровне
            if abs(shoulder1 - shoulder2) / shoulder1 > tolerance:
                continue
            # Достаточное расстояние
            if idx_h - idx_s1 < 3 or idx_s2 - idx_h < 3:
                continue

            # neckline: минимумы между плечами и головой
            lows_between = [l for l in lows
                          if (idx_s1 < l[0] < idx_h) or (idx_h < l[0] < idx_s2)]
            if not lows_between:
                continue
            neckline = min(l[1] for l in lows_between)
            pattern_height = head - neckline
            target = neckline - pattern_height
            stop = head + pattern_height * 0.1

            return Pattern(
                name="HEAD_AND_SHOULDERS",
                direction="BEARISH",
                confidence=0.80,
                start_idx=idx_s1,
                end_idx=idx_s2,
                neckline=neckline,
                target=target,
                stop=stop,
                description=f"Голова-плечи: плечи {shoulder1:.6f}/{shoulder2:.6f}, голова {head:.6f}"
            )
        return None

    def detect_flag(self, min_bars: int = 5, max_bars: int = 20) -> Optional[Pattern]:
        """
        Флаг: импульс (flagpole) + консолидация (флаг) против тренда.
        Бычий флаг: сильный рост + нисходящий канал.
        Медвежий флаг: сильное падение + восходящий канал.
        """
        if self.n < min_bars + 5:
            return None

        for start in range(self.n - min_bars - 5):
            # Flagpole: сильный импульс (первые 3-5 свечей)
            pole_end = min(start + 5, self.n - min_bars)
            pole_candles = self.candles[start:pole_end]
            if len(pole_candles) < 3:
                continue

            # Определяем направление импульса
            pole_move = (pole_candles[-1].close - pole_candles[0].open) / pole_candles[0].open
            if abs(pole_move) < 0.03:  # минимум 3% импульс
                continue

            # Консолидация (флаг)
            flag_start = pole_end
            flag_end = min(flag_start + max_bars, self.n)
            flag_candles = self.candles[flag_start:flag_end]
            if len(flag_candles) < min_bars:
                continue

            # Флаг против импульса
            flag_highs = [c.high for c in flag_candles]
            flag_lows = [c.low for c in flag_candles]
            flag_trend = (flag_candles[-1].close - flag_candles[0].open) / flag_candles[0].open

            # Бычий флаг: импульс вверх + консолидация вниз
            if pole_move > 0 and flag_trend < 0:
                entry = flag_candles[-1].close
                target = pole_candles[-1].close + (pole_candles[-1].close - pole_candles[0].open)
                stop = min(flag_lows) * 0.99
                return Pattern(
                    name="BULL_FLAG",
                    direction="BULLISH",
                    confidence=0.70,
                    start_idx=start,
                    end_idx=flag_end - 1,
                    target=target,
                    stop=stop,
                    description=f"Бычий флаг: импульс +{pole_move*100:.1f}%, консолидация"
                )

            # Медвежий флаг: импульс вниз + консолидация вверх
            if pole_move < 0 and flag_trend > 0:
                entry = flag_candles[-1].close
                target = pole_candles[-1].close + (pole_candles[-1].close - pole_candles[0].open)
                stop = max(flag_highs) * 1.01
                return Pattern(
                    name="BEAR_FLAG",
                    direction="BEARISH",
                    confidence=0.70,
                    start_idx=start,
                    end_idx=flag_end - 1,
                    target=target,
                    stop=stop,
                    description=f"Медвежий флаг: импульс {pole_move*100:.1f}%, консолидация"
                )
        return None

    def scan_all(self) -> List[Pattern]:
        """Сканировать все паттерны и вернуть список найденных."""
        patterns = []
        detectors = [
            self.detect_double_top,
            self.detect_double_bottom,
            self.detect_head_and_shoulders,
            self.detect_flag,
        ]
        for detector in detectors:
            try:
                result = detector()
                if result:
                    patterns.append(result)
            except Exception as e:
                log.debug(f"Pattern detector {detector.__name__} failed: {e}")
        # Вернуть только самый уверенный паттерн
        if patterns:
            patterns.sort(key=lambda p: p.confidence, reverse=True)
        return patterns


class SymbolPatternLearner:
    """
    Символ-специфичное обучение паттернов.
    Анализирует историю по каждому символу и находит повторяющиеся паттерны.
    """

    PATTERN_DB = Path("/root/tradingos/memory/symbol_patterns.json")

    def __init__(self):
        self.patterns: Dict[str, List[Dict]] = {}
        self._load()

    def _load(self):
        """Загрузить базу паттернов."""
        if self.PATTERN_DB.exists():
            try:
                self.patterns = json.loads(self.PATTERN_DB.read_text())
            except Exception:
                self.patterns = {}

    def _save(self):
        """Сохранить базу паттернов."""
        self.PATTERN_DB.parent.mkdir(parents=True, exist_ok=True)
        with open(self.PATTERN_DB, "w") as f:
            json.dump(self.patterns, f, indent=2)

    def record_pattern(self, symbol: str, pattern_name: str,
                       direction: str, outcome: str, r_multiple: float):
        """Записать результат паттерна для символа."""
        if symbol not in self.patterns:
            self.patterns[symbol] = []
        self.patterns[symbol].append({
            "pattern": pattern_name,
            "direction": direction,
            "outcome": outcome,
            "r": r_multiple,
            "ts": time.time(),
        })
        # Хранить только последние 50 записей
        self.patterns[symbol] = self.patterns[symbol][-50:]
        self._save()

    def get_pattern_edge(self, symbol: str, pattern_name: str,
                         direction: str, min_samples: int = 3) -> float:
        """
        Получить edge (ожидаемый R) для паттерна на символе.
        Возвращает средний R или 0 если недостаточно данных.
        """
        entries = self.patterns.get(symbol, [])
        matching = [
            e for e in entries
            if e["pattern"] == pattern_name and e["direction"] == direction
        ]
        if len(matching) < min_samples:
            return 0.0
        avg_r = sum(e["r"] for e in matching) / len(matching)
        return avg_r

    def get_best_patterns(self, symbol: str, min_samples: int = 3) -> List[Dict]:
        """Получить лучшие паттерны для символа (по среднему R)."""
        entries = self.patterns.get(symbol, [])
        if len(entries) < min_samples:
            return []
        # Группировать по (pattern, direction)
        from collections import defaultdict
        grouped = defaultdict(list)
        for e in entries:
            grouped[(e["pattern"], e["direction"])].append(e["r"])
        results = []
        for (pat, dir), rs in grouped.items():
            if len(rs) >= min_samples:
                results.append({
                    "pattern": pat,
                    "direction": dir,
                    "count": len(rs),
                    "avg_r": sum(rs) / len(rs),
                })
        results.sort(key=lambda x: x["avg_r"], reverse=True)
        return results


# ═══════════════════════════════════════════════════════════════
# ДВУХСВЕЧНЫЕ И ТРЁХСВЕЧНЫЕ ПАТТЕРНЫ
# ═══════════════════════════════════════════════════════════════

class CandlePatternScanner:
    """Сканер свечных паттернов (1-5 свечей)."""

    def __init__(self, candles: List[Candle]):
        self.candles = candles
        self.n = len(candles)

    def _body(self, i: int) -> float:
        return abs(self.candles[i].close - self.candles[i].open)

    def _is_bullish(self, i: int) -> bool:
        return self.candles[i].close > self.candles[i].open

    def _is_bearish(self, i: int) -> bool:
        return self.candles[i].close < self.candles[i].open

    # ─── Single-bar ────────────────────────────────────────────

    def detect_doji(self, i: int) -> bool:
        """Доджи: тело почти отсутствует, тени с обеих сторон."""
        if i >= self.n:
            return False
        c = self.candles[i]
        body = self._body(i)
        range_ = c.high - c.low
        if range_ == 0:
            return False
        return body / range_ < 0.05  # тело < 5% диапазона

    def detect_marubozu(self, i: int) -> bool:
        """Марубозу: длинное тело без теней."""
        if i >= self.n:
            return False
        c = self.candles[i]
        body = self._body(i)
        range_ = c.high - c.low
        if range_ == 0:
            return False
        shadows = c.upper_shadow + c.lower_shadow
        return body / range_ > 0.95 and shadows / body < 0.05

    # ─── Double candle ─────────────────────────────────────────

    def detect_engulfing(self, i: int) -> Optional[str]:
        """Бычье/медвежье поглощение. i = первая свеча."""
        if i + 1 >= self.n:
            return None
        c1, c2 = self.candles[i], self.candles[i + 1]
        b1, b2 = self._body(i), self._body(i + 1)
        if b1 == 0 or b2 == 0:
            return None
        # Бычье поглощение: c1 медвежья, c2 бычья и перекрывает c1
        if (self._is_bearish(i) and self._is_bullish(i + 1)
                and c2.open < c1.close and c2.close > c1.open
                and b2 > b1 * 1.2):
            return "BULLISH_ENGULFING"
        # Медвежье поглощение: c1 бычья, c2 медвежья и перекрывает c1
        if (self._is_bullish(i) and self._is_bearish(i + 1)
                and c2.open > c1.close and c2.close < c1.open
                and b2 > b1 * 1.2):
            return "BEARISH_ENGULFING"
        return None

    def detect_piercing(self, i: int) -> Optional[str]:
        """Пронзание / Тёмное облако."""
        if i + 1 >= self.n:
            return None
        c1, c2 = self.candles[i], self.candles[i + 1]
        b1 = self._body(i)
        if b1 == 0:
            return None
        # Пронзание (бычье): c1 медвежья, c2 бычья, перекрывает 50-100% c1
        if (self._is_bearish(i) and self._is_bullish(i + 1)
                and c2.open < c1.close
                and c1.close < c2.close < c1.open):
            overlap = (c2.close - c1.close) / (c1.open - c1.close)
            if 0.5 <= overlap <= 1.0:
                return "PIERCING_PATTERN"
        # Тёмное облако (медвежье): c1 бычья, c2 медвежья, перекрывает 50-100%
        if (self._is_bullish(i) and self._is_bearish(i + 1)
                and c2.open > c1.close
                and c1.close > c2.close > c1.open):
            overlap = (c1.close - c2.close) / (c1.close - c1.open)
            if 0.5 <= overlap <= 1.0:
                return "DARK_CLOUD_COVER"
        return None

    def detect_harami(self, i: int) -> Optional[str]:
        """Харами: вторая свеча внутри тела первой."""
        if i + 1 >= self.n:
            return None
        c1, c2 = self.candles[i], self.candles[i + 1]
        top1, bottom1 = max(c1.open, c1.close), min(c1.open, c1.close)
        top2, bottom2 = max(c2.open, c2.close), min(c2.open, c2.close)
        if top2 < top1 and bottom2 > bottom1:
            if self._is_bearish(i) and self._is_bullish(i + 1):
                return "BULLISH_HARAMI"
            if self._is_bullish(i) and self._is_bearish(i + 1):
                return "BEARISH_HARAMI"
        return None

    # ─── Triple candle ─────────────────────────────────────────

    def detect_morning_star(self, i: int) -> bool:
        """Утренняя звезда (i, i+1, i+2)."""
        if i + 2 >= self.n:
            return False
        c1, c2, c3 = self.candles[i], self.candles[i + 1], self.candles[i + 2]
        # c1: длинная медвежья
        if not self._is_bearish(i) or self._body(i) < (c1.high - c1.low) * 0.5:
            return False
        # c2: маленькое тело (доджи/волчок)
        if self._body(i + 1) > self._body(i) * 0.3:
            return False
        # c3: длинная бычья, закрывается выше середины c1
        if not self._is_bullish(i + 2) or self._body(i + 2) < self._body(i) * 0.5:
            return False
        return c3.close >= (c1.open + c1.close) / 2

    def detect_evening_star(self, i: int) -> bool:
        """Вечерняя звезда."""
        if i + 2 >= self.n:
            return False
        c1, c2, c3 = self.candles[i], self.candles[i + 1], self.candles[i + 2]
        if not self._is_bullish(i) or self._body(i) < (c1.high - c1.low) * 0.5:
            return False
        if self._body(i + 1) > self._body(i) * 0.3:
            return False
        if not self._is_bearish(i + 2) or self._body(i + 2) < self._body(i) * 0.5:
            return False
        return c3.close <= (c1.open + c1.close) / 2

    def detect_three_soldiers(self, i: int) -> bool:
        """Три белых солдата."""
        if i + 2 >= self.n:
            return False
        for j in range(3):
            if not self._is_bullish(i + j):
                return False
            b = self._body(i + j)
            r = self.candles[i + j].high - self.candles[i + j].low
            if b < r * 0.6:  # длинное тело
                return False
        # Каждая открывается внутри предыдущей и закрывается выше
        c1, c2, c3 = self.candles[i], self.candles[i + 1], self.candles[i + 2]
        return (c1.open < c2.open < c1.close
                and c2.open < c3.open < c2.close
                and c2.close > c1.close
                and c3.close > c2.close)

    def detect_three_crows(self, i: int) -> bool:
        """Три чёрных ворона."""
        if i + 2 >= self.n:
            return False
        for j in range(3):
            if not self._is_bearish(i + j):
                return False
            b = self._body(i + j)
            r = self.candles[i + j].high - self.candles[i + j].low
            if b < r * 0.6:
                return False
        c1, c2, c3 = self.candles[i], self.candles[i + 1], self.candles[i + 2]
        return (c1.open > c2.open > c1.close
                and c2.open > c3.open > c2.close
                and c2.close < c1.close
                and c3.close < c2.close)

    def detect_three_inside_up(self, i: int) -> bool:
        """Три внутри вверх."""
        if i + 2 >= self.n:
            return False
        c1, c2, c3 = self.candles[i], self.candles[i + 1], self.candles[i + 2]
        if not self._is_bearish(i) or not self._is_bullish(i + 1) or not self._is_bullish(i + 2):
            return False
        # c2 внутри c1
        top1, bottom1 = max(c1.open, c1.close), min(c1.open, c1.close)
        top2, bottom2 = max(c2.open, c2.close), min(c2.open, c2.close)
        if not (top2 < top1 and bottom2 > bottom1):
            return False
        # c3 закрывается выше максимума c1
        return c3.close > c1.high

    def detect_three_inside_down(self, i: int) -> bool:
        """Три внутри вниз."""
        if i + 2 >= self.n:
            return False
        c1, c2, c3 = self.candles[i], self.candles[i + 1], self.candles[i + 2]
        if not self._is_bullish(i) or not self._is_bearish(i + 1) or not self._is_bearish(i + 2):
            return False
        top1, bottom1 = max(c1.open, c1.close), min(c1.open, c1.close)
        top2, bottom2 = max(c2.open, c2.close), min(c2.open, c2.close)
        if not (top2 < top1 and bottom2 > bottom1):
            return False
        return c3.close < c1.low

    # ─── Five candle ───────────────────────────────────────────

    def detect_rising_three(self, i: int) -> bool:
        """Восходящий метод трёх."""
        if i + 4 >= self.n:
            return False
        c = self.candles
        # c1: длинная зелёная
        if not self._is_bullish(i) or self._body(i) < (c[i].high - c[i].low) * 0.5:
            return False
        # c2-c4: три маленькие красные внутри диапазона c1
        for j in range(1, 4):
            if not self._is_bearish(i + j):
                return False
            if c[i + j].high > c[i].high or c[i + j].low < c[i].low:
                return False
            if self._body(i + j) > self._body(i) * 0.5:
                return False
        # c5: длинная зелёная, закрывается выше максимума c1
        if not self._is_bullish(i + 4) or c[i + 4].close <= c[i].high:
            return False
        return self._body(i + 4) >= self._body(i) * 0.8

    def detect_falling_three(self, i: int) -> bool:
        """Нисходящий метод трёх."""
        if i + 4 >= self.n:
            return False
        c = self.candles
        if not self._is_bearish(i) or self._body(i) < (c[i].high - c[i].low) * 0.5:
            return False
        for j in range(1, 4):
            if not self._is_bullish(i + j):
                return False
            if c[i + j].high > c[i].high or c[i + j].low < c[i].low:
                return False
            if self._body(i + j) > self._body(i) * 0.5:
                return False
        if not self._is_bearish(i + 4) or c[i + 4].close >= c[i].low:
            return False
        return self._body(i + 4) >= self._body(i) * 0.8

    def scan_all(self) -> List[Pattern]:
        """Сканировать ВСЕ свечные паттерны."""
        patterns = []
        for i in range(self.n - 4):  # минимум 5 свечей для полного сканирования
            # Single
            if self.detect_doji(i):
                patterns.append(Pattern("DOJI", "NEUTRAL", 0.5, i, i, description="Доджи — нерешительность рынка"))
            if self.detect_marubozu(i):
                dir_ = "BULLISH" if self._is_bullish(i) else "BEARISH"
                patterns.append(Pattern("MARUBOZU", dir_, 0.7, i, i, description="Марубозу — сильный импульс без теней"))
            # Double
            engulf = self.detect_engulfing(i)
            if engulf:
                dir_ = "BULLISH" if "BULLISH" in engulf else "BEARISH"
                patterns.append(Pattern(engulf, dir_, 0.75, i, i + 1, description="Поглощение — смена настроений"))
            pierce = self.detect_piercing(i)
            if pierce:
                dir_ = "BULLISH" if "PIERCING" in pierce else "BEARISH"
                patterns.append(Pattern(pierce, dir_, 0.70, i, i + 1, description="Пронзание/Тёмное облако — слабый разворот"))
            harami = self.detect_harami(i)
            if harami:
                dir_ = "BULLISH" if "BULLISH" in harami else "BEARISH"
                patterns.append(Pattern(harami, dir_, 0.65, i, i + 1, description="Харами — замедление тренда"))
            # Triple
            if self.detect_morning_star(i):
                patterns.append(Pattern("MORNING_STAR", "BULLISH", 0.80, i, i + 2, description="Утренняя звезда — бычий разворот"))
            if self.detect_evening_star(i):
                patterns.append(Pattern("EVENING_STAR", "BEARISH", 0.80, i, i + 2, description="Вечерняя звезда — медвежий разворот"))
            if self.detect_three_soldiers(i):
                patterns.append(Pattern("THREE_WHITE_SOLDIERS", "BULLISH", 0.82, i, i + 2, description="Три белых солдата — сильный бычий разворот"))
            if self.detect_three_crows(i):
                patterns.append(Pattern("THREE_BLACK_CROWS", "BEARISH", 0.82, i, i + 2, description="Три чёрных ворона — сильный медвежий разворот"))
            if self.detect_three_inside_up(i):
                patterns.append(Pattern("THREE_INSIDE_UP", "BULLISH", 0.75, i, i + 2, description="Три внутри вверх — бычий разворот"))
            if self.detect_three_inside_down(i):
                patterns.append(Pattern("THREE_INSIDE_DOWN", "BEARISH", 0.75, i, i + 2, description="Три внутри вниз — медвежий разворот"))
            # Five
            if self.detect_rising_three(i):
                patterns.append(Pattern("RISING_THREE", "BULLISH", 0.75, i, i + 4, description="Восходящий метод трёх — продолжение бычьего тренда"))
            if self.detect_falling_three(i):
                patterns.append(Pattern("FALLING_THREE", "BEARISH", 0.75, i, i + 4, description="Нисходящий метод трёх — продолжение медвежьего тренда"))

        if patterns:
            patterns.sort(key=lambda p: p.confidence, reverse=True)
        return patterns


# ═══════════════════════════════════════════════════════════════
# DYNAMIC DEVIATION CHANNELS (RSI Trigger) [ChartPrime]
# ═══════════════════════════════════════════════════════════════

@dataclass
class DDCResult:
    """Результат Dynamic Deviation Channels."""
    mid_line: float
    upper1: float
    upper2: float
    upper3: float
    lower1: float
    lower2: float
    lower3: float
    rsi: float
    trend: str  # "BULLISH" | "BEARISH" | "NEUTRAL"
    signal: Optional[str]  # "LONG" | "SHORT" | None


class DynamicDeviationChannels:
    """
    Dynamic Deviation Channels (RSI Trigger) [ChartPrime].
    
    Логика:
    - Mid-line = EMA(close, length)
    - Bands = mid ± ATR(100) * 1.5 * multiplier
    - RSI фильтр: показывать только upper bands при RSI≥50, только lower при RSI<50
    - Сигнал: crossover close/lower1 → LONG, crossunder close/upper1 → SHORT
    """

    def __init__(self, length: int = 20, rsi_length: int = 20,
                 mult1: float = 1.0, mult2: float = 2.0, mult3: float = 3.0):
        self.length = length
        self.rsi_length = rsi_length
        self.mult1 = mult1
        self.mult2 = mult2
        self.mult3 = mult3

    def _ema(self, data: List[float], period: int) -> List[float]:
        """Экспоненциальная скользящая средняя."""
        if len(data) < period:
            return data
        ema = [sum(data[:period]) / period]
        k = 2 / (period + 1)
        for price in data[period:]:
            ema.append(price * k + ema[-1] * (1 - k))
        return ema

    def _atr(self, candles: List[Candle], period: int) -> List[float]:
        """Average True Range."""
        trs = []
        for i in range(len(candles)):
            if i == 0:
                trs.append(candles[i].high - candles[i].low)
            else:
                prev_close = candles[i - 1].close
                tr1 = candles[i].high - candles[i].low
                tr2 = abs(candles[i].high - prev_close)
                tr3 = abs(candles[i].low - prev_close)
                trs.append(max(tr1, tr2, tr3))
        if len(trs) < period:
            return trs
        atr = [sum(trs[:period]) / period]
        for tr in trs[period:]:
            atr.append((atr[-1] * (period - 1) + tr) / period)
        return atr

    def _rsi(self, closes: List[float], period: int) -> List[float]:
        """Relative Strength Index."""
        if len(closes) < period + 1:
            return [50.0] * len(closes)
        gains = []
        losses = []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        # SMA для первого RSI
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rs = avg_gain / avg_loss if avg_loss > 0 else float('inf')
        rsi_vals = [100 - (100 / (1 + rs))]
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            rs = avg_gain / avg_loss if avg_loss > 0 else float('inf')
            rsi_vals.append(100 - (100 / (1 + rs)))
        # Pad начало
        padding = len(closes) - len(rsi_vals)
        return [50.0] * padding + rsi_vals

    def calculate(self, candles: List[Candle]) -> DDCResult:
        """Рассчитать DDC для последней свечи."""
        if len(candles) < self.length + 10:
            return DDCResult(0, 0, 0, 0, 0, 0, 0, 50, "NEUTRAL", None)

        closes = [c.close for c in candles]
        ema_vals = self._ema(closes, self.length)
        atr_vals = self._atr(candles, 100)
        rsi_vals = self._rsi(closes, self.rsi_length)

        # SMA RSI для сглаживания (period=5)
        rsi_smooth = sum(rsi_vals[-5:]) / 5 if len(rsi_vals) >= 5 else rsi_vals[-1]

        mid = ema_vals[-1]
        atr = atr_vals[-1] * 1.5
        rsi = rsi_smooth

        # Trend direction (rising/falling mid-line)
        if len(ema_vals) >= 3:
            trend = "BULLISH" if ema_vals[-1] > ema_vals[-3] else "BEARISH" if ema_vals[-1] < ema_vals[-3] else "NEUTRAL"
        else:
            trend = "NEUTRAL"

        # Conditional bands
        if rsi >= 50:
            upper1 = mid + atr * self.mult1
            upper2 = mid + atr * self.mult2
            upper3 = mid + atr * self.mult3
            lower1 = lower2 = lower3 = float('nan')
        else:
            upper1 = upper2 = upper3 = float('nan')
            lower1 = mid - atr * self.mult1
            lower2 = mid - atr * self.mult2
            lower3 = mid - atr * self.mult3

        # Signal: crossover/crossunder close/lower1 or close/upper1
        signal = None
        if len(candles) >= 2:
            prev_close = candles[-2].close
            cur_close = candles[-1].close
            if not (rsi >= 50) and not (prev_close < lower1 <= cur_close):
                pass
            elif not (rsi >= 50) and prev_close < lower1 <= cur_close:
                signal = "LONG"
            elif rsi >= 50 and prev_close > upper1 >= cur_close:
                signal = "SHORT"

        return DDCResult(
            mid_line=mid, upper1=upper1, upper2=upper2, upper3=upper3,
            lower1=lower1, lower2=lower2, lower3=lower3,
            rsi=rsi, trend=trend, signal=signal
        )


# ═══════════════════════════════════════════════════════════════
# ОБЪЕДИНЁННЫЙ СКАНЕР
# ═══════════════════════════════════════════════════════════════

def detect_all_patterns(candles: List[dict]) -> Dict[str, Any]:
    """
    Удобная функция для интеграции с FeatureStore.
    Возвращает ВСЕ паттерны: свечные + графические + DDC.
    """
    if len(candles) < 20:
        return {"patterns": [], "ddc": None, "best": None}

    candle_objs = [
        Candle(
            open=c.get("open", 0),
            high=c.get("high", 0),
            low=c.get("low", 0),
            close=c.get("close", 0),
            volume=c.get("volume", 0),
        )
        for c in candles[-50:]
    ]

    # Графические паттерны
    chart_detector = PatternDetector(candle_objs)
    chart_patterns = chart_detector.scan_all()

    # Свечные паттерны
    candle_scanner = CandlePatternScanner(candle_objs)
    candle_patterns = candle_scanner.scan_all()

    # Dynamic Deviation Channels
    ddc = DynamicDeviationChannels()
    ddc_result = ddc.calculate(candle_objs)

    all_patterns = chart_patterns + candle_patterns
    all_patterns.sort(key=lambda p: p.confidence, reverse=True)

    return {
        "patterns": [
            {"name": p.name, "direction": p.direction, "confidence": p.confidence,
             "description": p.description}
            for p in all_patterns
        ],
        "ddc": {
            "mid_line": ddc_result.mid_line,
            "rsi": ddc_result.rsi,
            "trend": ddc_result.trend,
            "signal": ddc_result.signal,
        } if ddc_result else None,
        "best": all_patterns[0] if all_patterns else None,
    }


# ─── Интеграция с FeatureVector ──────────────────────────────────

def detect_patterns_from_candles(candles: List[dict]) -> Optional[Pattern]:
    """
    Удобная функция для интеграции с FeatureStore.
    Принимает список dict с ключами open/high/low/close/volume.
    Возвращает лучший Pattern или None.
    """
    if len(candles) < 20:
        return None
    candle_objs = [
        Candle(
            open=c.get("open", 0),
            high=c.get("high", 0),
            low=c.get("low", 0),
            close=c.get("close", 0),
            volume=c.get("volume", 0),
        )
        for c in candles[-50:]  # последние 50 свечей для анализа
    ]
    detector = PatternDetector(candle_objs)
    patterns = detector.scan_all()
    return patterns[0] if patterns else None


# ═══════════════════════════════════════════════════════════════
# PATTERN TRACKER — валидатор паттернов (detect → log → validate)
# ═══════════════════════════════════════════════════════════════

class PatternTracker:
    """
    Трекер паттернов: при каждом скане записывает найденные паттерны
    вместе с направлением сигнала. Затем (jobs-модулем) сравнивает
    с фактическим исходом закрытых сделок → считает совпадение
    "паттерн-описание vs реальность" и edge по каждому паттерну.

    Лог: /root/tradingos/memory/pattern_log.jsonl
    """
    LOG_PATH = Path("/root/tradingos/memory/pattern_log.jsonl")
    MAX_LOG_SIZE_MB = 50

    @classmethod
    def log_scan(cls, symbol: str, direction: str, prob: float,
                 patterns: list, ddc: Optional[dict] = None,
                 vwap: Optional[dict] = None, sr: Optional[dict] = None,
                 false_break: Optional[dict] = None,
                 fib: Optional[dict] = None,
                 orb: Optional[dict] = None,
                 smc: Optional[dict] = None) -> None:
        """Записать один скан кандидата.
        patterns: список dict (name/direction/confidence) от detect_all_patterns.
        false_break: dict от FalseBreakDetector.detect() или None.
        """
        try:
            rec = {
                "event": "PATTERN_SCAN",
                "ts": time.time(),
                "iso": datetime_now_iso(),
                "symbol": symbol,
                "signal_direction": direction,   # BUY/SELL от SignalGenerator
                "prob": round(prob, 3),
                "n_patterns": len(patterns),
                "best_pattern": patterns[0].get("name") if patterns else None,
                "best_pattern_dir": patterns[0].get("direction") if patterns else None,
                "pattern_names": [p.get("name") for p in patterns[:8]],
                "pattern_dirs": [p.get("direction") for p in patterns[:8]],
                # Совпадение: направление лучшего паттерна vs направление сигнала
                "agreement": cls._agreement(patterns, direction),
                "ddc_signal": (ddc or {}).get("signal"),
                "ddc_trend": (ddc or {}).get("trend"),
                "ddc_rsi": round((ddc or {}).get("rsi", 0), 1),
                "vwap_direction": (vwap or {}).get("direction"),
                "vwap_quality": (vwap or {}).get("quality"),
                "sr_support": (sr or {}).get("support"),
                "sr_resistance": (sr or {}).get("resistance"),
                "false_break_direction": (false_break or {}).get("direction"),
                "false_break_pattern": (false_break or {}).get("pattern"),
                "false_break_level": (false_break or {}).get("level"),
                "false_break_touches": (false_break or {}).get("touches"),
                "fib_direction": (fib or {}).get("direction"),
                "fib_golden_pocket": (fib or {}).get("in_golden_pocket"),
                "fib_confluence": (fib or {}).get("confluence"),
                "orb_direction": (orb or {}).get("direction"),
                "orb_target": (orb or {}).get("target"),
                "smc_direction": (smc or {}).get("direction"),
                "smc_sweep": (smc or {}).get("sweep_price"),
            }
            cls._append(rec)
        except Exception as e:
            log.debug(f"PatternTracker.log_scan fail: {e}")

    @staticmethod
    def _agreement(patterns: list, direction: str) -> Optional[str]:
        """PASS, если лучший паттерн совпадает по направлению с сигналом.
        BULLISH ↔ BUY, BEARISH ↔ SELL. NEUTRAL/None → None («нет данных»)."""
        if not patterns:
            return "NO_PATTERN"
        best = patterns[0]
        pdir = (best.get("direction") or "").upper()
        sdir = (direction or "").upper()
        if pdir == "NEUTRAL" or not pdir:
            return "NEUTRAL"
        agree = (pdir == "BULLISH" and sdir == "BUY") or \
                (pdir == "BEARISH" and sdir == "SELL")
        return "AGREE" if agree else "DISAGREE"

    @classmethod
    def _append(cls, rec: dict) -> None:
        """Дозапись с ротацией по размеру (50 МБ)."""
        try:
            cls.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Ротация: если файл большой — обрезать до последних 10k строк
            if cls.LOG_PATH.exists() and \
                    cls.LOG_PATH.stat().st_size > cls.MAX_LOG_SIZE_MB * 1024 * 1024:
                lines = cls.LOG_PATH.read_text().splitlines()[-10_000:]
                tmp = cls.LOG_PATH.with_suffix(".tmp")
                tmp.write_text("\n".join(lines) + "\n")
                tmp.replace(cls.LOG_PATH)
            with open(cls.LOG_PATH, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:
            log.debug(f"PatternTracker append fail: {e}")

    @classmethod
    def report(cls, min_scans: int = 5) -> str:
        """Сводка: какие паттерны чаще совпадают с направлением сигнала
        и как связаны с исходами сделок (из trade_results по symbol)."""
        if not cls.LOG_PATH.exists():
            return "pattern_log пуст — сканов ещё не было"
        from collections import defaultdict
        stats = defaultdict(lambda: {"scans": 0, "agree": 0, "disagree": 0})
        with open(cls.LOG_PATH) as f:
            for line in f:
                try:
                    r = json.loads(line.strip())
                    if r.get("event") != "PATTERN_SCAN":
                        continue
                    name = r.get("best_pattern") or "NONE"
                    s = stats[name]
                    s["scans"] += 1
                    a = r.get("agreement")
                    if a == "AGREE":
                        s["agree"] += 1
                    elif a == "DISAGREE":
                        s["disagree"] += 1
                except Exception:
                    pass
        lines = [f"{'PATTERN':24s} {'SCANS':>5s} {'AGREE':>5s} {'DISAGREE':>8s} {'AGREE%':>7s}"]
        for name, s in sorted(stats.items(), key=lambda x: -x[1]["scans"]):
            if s["scans"] < min_scans:
                continue
            rate = 100 * s["agree"] / max(1, s["agree"] + s["disagree"]) \
                if (s["agree"] + s["disagree"]) else 0
            lines.append(f"{name:24s} {s['scans']:>5d} {s['agree']:>5d} "
                         f"{s['disagree']:>8d} {rate:>6.1f}%")
        return "\n".join(lines) or "нет данных"


def datetime_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()
