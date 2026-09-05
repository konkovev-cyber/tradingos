"""
fib_detector.py — Fibonacci retrace detector (Karen Fu approach).

Логика:
1. Определить импульс (swing high/low за lookback свечей).
2. Посчитать уровни 0.382 / 0.5 / 0.618 / 0.786 от импульса.
3. Golden Pocket = зона 0.618–0.65 — приоритетная зона коррекции.
4. КОНФЛЮЭНЦИЯ: fib-уровень совпадает с S/R-уровнем (±0.3%) → зона повышенной силы.
5. Вход только с подтверждением: цена в Golden Pocket ИЛИ на fib-уровне
   с конфлюэнцией + бычье/медвежье закрытие (Price Action).

Shadow-режим: детектор логируется через PatternTracker, не блокирует кандидатов.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger("FibDetector")

FIB_LEVELS = [0.382, 0.5, 0.618, 0.786]
GOLDEN_POCKET = (0.618, 0.65)


@dataclass
class FibResult:
    direction: str           # "LONG" | "SHORT" | None
    impulse_high: float
    impulse_low: float
    retracement: float       # глубина текущего отката 0-1 (в долях импульса)
    nearest_fib: float       # ближайший fib-уровень
    in_golden_pocket: bool   # откат 0.618-0.65
    confluence_level: Optional[float]  # S/R-уровень, совпавший с fib
    confluence_with: Optional[str]     # "support"|"resistance"
    reason: str


class FibonacciDetector:
    """Fib-ретрейсмент импульса + конфлюэнция с S/R."""

    def __init__(self, lookback: int = 30, confluence_tol: float = 0.003):
        # Искать импульс на последних N свечах
        self.lookback = lookback
        # Fib-уровень и S/R совпадают, если дальше 0.3% цены
        self.confluence_tol = confluence_tol

    def _find_impulse(self, candles: List[dict]) -> tuple:
        """Найти swing high/low (импульс) на lookback-свечах.
        Импульс = от последнего значимого экстремума к противоположному."""
        window = candles[-self.lookback:]
        hi_idx = max(range(len(window)), key=lambda i: window[i]["high"])
        lo_idx = min(range(len(window)), key=lambda i: window[i]["low"])
        return (window[hi_idx]["high"], hi_idx,
                window[lo_idx]["low"], lo_idx)

    def detect(self, candles: List[dict],
               sr_levels: Optional[list] = None) -> Optional[FibResult]:
        """
        candles: последние N закрытых свечей (dict).
        sr_levels: список SRLevel от SRLevelDetector для конфлюэнции.
        """
        if not sr_levels:
            try:
                from tradingos.signals.vwap_sr import SRLevelDetector
                sr_levels = SRLevelDetector().detect(candles)
            except Exception:
                sr_levels = []
        if not candles or len(candles) < self.lookback:
            return None

        imp_high, hi_idx, imp_low, lo_idx = self._find_impulse(candles)
        rng = imp_high - imp_low
        if rng <= 0:
            return None
        last = candles[-1]

        # ─── Бычий сетап: импульс вверх (низ раньше, верх позже),
        # сейчас откат вниз к fib ───
        if lo_idx < hi_idx:
            retr = (imp_high - last["close"]) / rng  # 0 = на пике, 1 = на дне
            if last["close"] < imp_low:
                return None  # импульс сломан
            gp_lo = rng * GOLDEN_POCKET[0]
            gp_hi = rng * GOLDEN_POCKET[1]
            in_gp = gp_lo <= (imp_high - last["close"]) <= gp_hi
            nearest = min(FIB_LEVELS, key=lambda f: abs(rng * f - (imp_high - last["close"])))
            conf, conf_kind = self._confluence(
                imp_high - rng * nearest, sr_levels, "support")
            return FibResult(
                "LONG" if (in_gp or conf) else None,
                imp_high, imp_low, round(retr, 3), nearest, in_gp,
                conf, conf_kind,
                f"Fib бычий откат: retr={retr:.1%}, GP={in_gp}, "
                f"confluence={conf} ({conf_kind or '-'})"
            )

        # ─── Медвежий сетап: импульс вниз, откат вверх к fib ───
        if hi_idx < lo_idx:
            retr = (last["close"] - imp_low) / rng
            if last["close"] > imp_high:
                return None
            gp_lo = rng * GOLDEN_POCKET[0]
            gp_hi = rng * GOLDEN_POCKET[1]
            in_gp = gp_lo <= (last["close"] - imp_low) <= gp_hi
            nearest = min(FIB_LEVELS, key=lambda f: abs(rng * f - (last["close"] - imp_low)))
            conf, conf_kind = self._confluence(
                imp_low + rng * nearest, sr_levels, "resistance")
            return FibResult(
                "SHORT" if (in_gp or conf) else None,
                imp_high, imp_low, round(retr, 3), nearest, in_gp,
                conf, conf_kind,
                f"Fib медвежий откат: retr={retr:.1%}, GP={in_gp}, "
                f"confluence={conf} ({conf_kind or '-'})"
            )
        return None

    def _confluence(self, fib_price: float, sr_levels: list,
                    prefer_kind: str) -> tuple:
        """Совпадает ли fib-уровень с сильным S/R-уровнем (±tol)."""
        for lv in sr_levels:
            if lv.touches < 3:
                continue
            if abs(fib_price - lv.price) / max(lv.price, 1e-9) <= self.confluence_tol:
                return lv.price, lv.kind
        return None, None