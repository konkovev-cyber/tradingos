"""
orb_detector.py — Opening Range Breakout (ORB) стратегия.

Логика (Trade Your Edge):
1. Первые N свечей после «открытия» (для крипто — последние N свечей) → диапазон.
2. Ждём закрытия за пределами диапазона (пробой).
3. Вход НЕ на пробое, а на ретесте (возврат к пробитой границе + отскок).
4. Стоп за противоположную границу диапазона.
5. Тренд-фильтр: только в направлении EMA20 (старший ТФ).

Интеграция: shadow-режим, логируется через PatternTracker.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict

log = logging.getLogger("ORBDetector")


@dataclass
class ORBSignal:
    direction: str  # "LONG" | "SHORT" | None
    high: float
    low: float
    entry: float
    stop: float
    target: float
    reason: str


class ORBDetector:
    """Opening Range Breakout с ретест-входом."""

    def __init__(self, range_bars: int = 5, confirmation_bars: int = 3,
                 ema_trend_filter: bool = True):
        # Диапазон формируется на первых N свечах
        self.range_bars = range_bars
        # Максимум N свечей ждём подтверждения после пробоя
        self.confirmation_bars = confirmation_bars
        # Только в направлении EMA20
        self.ema_filter = ema_trend_filter

    def detect(self, candles: List[dict]) -> Optional[ORBSignal]:
        """
        candles: последние M свечей (dict с open/high/low/close).
        Ищем диапазон на [0:range_bars], затем пробой + ретест.
        """
        if not candles or len(candles) < self.range_bars + 2:
            return None

        # Диапазон
        range_candles = candles[-self.range_bars:]
        rng_high = max(c["high"] for c in range_candles)
        rng_low = min(c["low"] for c in range_candles)
        rng_size = rng_high - rng_low
        if rng_size <= 0:
            return None

        # Последующие свечи после диапазона
        post = candles[:-self.range_bars]
        if not post:
            return None

        # Тренд-фильтр (EMA20 proxy: просто среднее close последних 20)
        closes = [c["close"] for c in candles[-20:]]
        ema20 = sum(closes) / len(closes) if closes else 0
        last_close = candles[-1]["close"]
        trend_up = last_close > ema20

        # Проверяем пробой в последних confirmation_bars свечах
        confirm_window = post[-self.confirmation_bars:]
        breakout_up = any(c["close"] > rng_high for c in confirm_window)
        breakout_down = any(c["close"] < rng_low for c in confirm_window)

        # LONG: пробой вверх + ретест (последняя свеча коснулась high и закрылась выше)
        if breakout_up and (not self.ema_filter or trend_up):
            last = candles[-1]
            if rng_low <= last["low"] <= rng_high and last["close"] > rng_high * 0.999:
                return ORBSignal(
                    direction="LONG",
                    high=rng_high,
                    low=rng_low,
                    entry=last["close"],
                    stop=rng_low - rng_size * 0.1,
                    target=rng_high + rng_size,
                    reason=f"ORB LONG: пробой {rng_high:.6g}, ретест, стоп {rng_low:.6g}, цель {rng_high+rng_size:.6g}"
                )

        # SHORT: пробой вниз + ретест
        if breakout_down and (not self.ema_filter or not trend_up):
            last = candles[-1]
            if rng_low <= last["high"] <= rng_high and last["close"] < rng_low * 1.001:
                return ORBSignal(
                    direction="SHORT",
                    high=rng_high,
                    low=rng_low,
                    entry=last["close"],
                    stop=rng_high + rng_size * 0.1,
                    target=rng_low - rng_size,
                    reason=f"ORB SHORT: пробой {rng_low:.6g}, ретест, стоп {rng_high:.6g}, цель {rng_low-rng_size:.6g}"
                )
        return None