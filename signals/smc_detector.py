"""
smc_detector.py — Smart Money Concepts (SMC) ликвидность + Order Block.

Логика (Smart Risk):
1. Liquidity Sweep: цена делает ложный пробой за уровень
   (предыдущий high/low), собирает стоп-лоссы, затем разворачивается.
2. Order Block (OB): зона перед импульсом, где крупные игроки
   размещали лимитные ордера. После sweep цена возвращается в OB
   → вход в направлении тренда.
3. Fair Value Gap (FVG): разрыв между low свечи N и high свечи N-2
   (или наоборот) — «незаполненная» зона, к которой цена вернётся.
4. Entry: sweep + возврат в OB/FVG + подтверждающая свеча.

Shadow-режим, логируется через PatternTracker.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional, Dict

log = logging.getLogger("SMCDetector")


@dataclass
class SMCSignal:
    direction: str   # "LONG" | "SHORT" | None
    sweep_price: float
    ob_zone_low: float
    ob_zone_high: float
    entry: float
    stop: float
    target: float
    reason: str


class SMCDetector:
    """Smart Money Concepts: Liquidity Sweep + Order Block + FVG."""

    def __init__(self, lookback: int = 20, ob_candles: int = 3,
                 fvg_tol: float = 0.002):
        # Искать sweep на последних N свечах
        self.lookback = lookback
        # Order Block = N свечей перед импульсом
        self.ob_candles = ob_candles
        # Допуск FVG
        self.fvg_tol = fvg_tol

    def _find_sweep(self, candles: List[dict]) -> Optional[Dict]:
        """Найти liquidity sweep: пробой предыдущего экстремума + возврат."""
        if len(candles) < 5:
            return None
        # Последние N свечей до sweep
        prior = candles[:-3]
        if not prior:
            return None
        prior_high = max(c["high"] for c in prior[-self.lookback:])
        prior_low = min(c["low"] for c in prior[-self.lookback:])

        last = candles[-1]
        prev = candles[-2]
        prev2 = candles[-3]

        # BULLISH sweep: цена пробила low ниже prior_low, но закрылась выше
        if prev["low"] < prior_low and last["close"] > prior_low:
            return {"kind": "BULLISH", "sweep": prev["low"],
                    "prior_low": prior_low, "prior_high": prior_high}

        # BEARISH sweep: цена пробила high выше prior_high, но закрылась ниже
        if prev["high"] > prior_high and last["close"] < prior_high:
            return {"kind": "BEARISH", "sweep": prev["high"],
                    "prior_low": prior_low, "prior_high": prior_high}
        return None

    def _find_order_block(self, candles: List[dict], direction: str) -> Optional[Dict]:
        """Найти Order Block — последние бычьи/медвежьи свечи перед импульсом."""
        n = len(candles)
        if n < self.ob_candles + 3:
            return None
        # Импульс = последние 2 свечи
        if direction == "LONG":
            # Бычий OB = медвежьи свечи перед бычьим импульсом
            # Находим: импульс вверх (close растёт), берём свечи перед ним
            for i in range(n - 3, n - self.ob_candles - 3, -1):
                if i < 0:
                    break
                # Проверяем, что после свечи i был бычий импульс
                if candles[i + 1]["close"] > candles[i + 1]["open"] and \
                        candles[i + 2]["close"] > candles[i + 2]["open"]:
                    ob_high = max(candles[j]["high"] for j in range(i, min(i + self.ob_candles, n)))
                    ob_low = min(candles[j]["low"] for j in range(i, min(i + self.ob_candles, n)))
                    return {"high": ob_high, "low": ob_low, "idx": i}
        else:  # SHORT
            for i in range(n - 3, n - self.ob_candles - 3, -1):
                if i < 0:
                    break
                if candles[i + 1]["close"] < candles[i + 1]["open"] and \
                        candles[i + 2]["close"] < candles[i + 2]["open"]:
                    ob_high = max(candles[j]["high"] for j in range(i, min(i + self.ob_candles, n)))
                    ob_low = min(candles[j]["low"] for j in range(i, min(i + self.ob_candles, n)))
                    return {"high": ob_high, "low": ob_low, "idx": i}
        return None

    def _find_fvg(self, candles: List[dict], direction: str) -> Optional[Dict]:
        """Fair Value Gap: разрыв между свечами."""
        if len(candles) < 3:
            return None
        # BULLISH FVG: low свечи N > high свечи N-2
        # BEARISH FVG: high свечи N < low свечи N-2
        if direction == "LONG":
            for i in range(len(candles) - 1, 2, -1):
                if candles[i]["low"] > candles[i - 2]["high"]:
                    return {"low": candles[i - 2]["high"], "high": candles[i]["low"]}
        else:
            for i in range(len(candles) - 1, 2, -1):
                if candles[i]["high"] < candles[i - 2]["low"]:
                    return {"low": candles[i]["high"], "high": candles[i - 2]["low"]}
        return None

    def detect_choch(self, candles: List[dict], lookback: int = 8) -> Optional[dict]:
        """
        Change of Character (ChoCh) — смена характера движения (owner, 2026-09-01).

        Определение из стратегии FVG+ChoCh:
        - До сигнала был тренд (серия более низких максимумов для даунтренда).
        - Цена пробивает предыдущий значимый максимум/минимум → смена характера.
        - Бычий ChoCh: даунтренд, затем пробитие предыдущего significant high
          (higher-high break) — сдвиг вверх.
        - Медвежий ChoCh: аптренд, затем пробитие предыдущего significant low
          (lower-low break) — сдвиг вниз.

        Возвращает {"kind": "BULLISH"|"BEARISH", "break_price": float, "idx": int} или None.
        """
        n = len(candles)
        if n < lookback + 3:
            return None

        # Свинг-экстремумы на окне перед последними 2 свечами
        window = candles[-lookback - 2:-2]
        if len(window) < 4:
            return None
        breaks = []
        for i in range(2, len(window) - 2):
            seg_h = [c["high"] for c in window[i-2:i+3]]
            seg_l = [c["low"] for c in window[i-2:i+3]]
            if window[i]["high"] >= max(seg_h):
                breaks.append(("H", window[i]["high"], int(i)))
            if window[i]["low"] <= min(seg_l):
                breaks.append(("L", window[i]["low"], int(i)))

        if len(breaks) < 2:
            return None
        # Последний значимый экстремум (свинг) перед текущим движением
        last_swing = breaks[-1]
        kind, sw_price, sw_idx = last_swing
        c_last, c_prev = candles[-1], candles[-2]

        # Бычий ChoCh: был down-swing (L), затем пробит above it
        if kind == "L" and (c_last["close"] > sw_price or c_prev["close"] > sw_price):
            return {"kind": "BULLISH", "break_price": sw_price, "idx": sw_idx}
        # Медвежий ChoCh: был up-swing (H), затем пробит снизу
        if kind == "H" and (c_last["close"] < sw_price or c_prev["close"] < sw_price):
            return {"kind": "BEARISH", "break_price": sw_price, "idx": sw_idx}
        return None

    def detect(self, candles: List[dict]) -> Optional[SMCSignal]:
        """Полный цикл: sweep -> OB -> FVG -> signal."""
        if len(candles) < self.lookback + 5:
            return None

        sweep = self._find_sweep(candles)
        if not sweep:
            return None

        direction = sweep["kind"]
        ob = self._find_order_block(candles, direction)
        fvg = self._find_fvg(candles, direction)

        last = candles[-1]
        close = last["close"]

        # LONG: цена после sweep внутри OB или FVG
        if direction == "LONG":
            in_ob = ob and ob["low"] <= close <= ob["high"] if ob else False
            in_fvg = fvg and fvg["low"] <= close <= fvg["high"] if fvg else False
            if in_ob or in_fvg:
                entry = close
                stop = min(sweep["sweep"], (ob["low"] if ob else entry)) - abs(entry - sweep["sweep"]) * 0.1
                target = sweep["prior_high"] + (sweep["prior_high"] - sweep["prior_low"]) * 0.5
                return SMCSignal(
                    direction="LONG",
                    sweep_price=sweep["sweep"],
                    ob_zone_low=ob["low"] if ob else entry,
                    ob_zone_high=ob["high"] if ob else entry,
                    entry=entry,
                    stop=stop,
                    target=target,
                    reason=f"SMC LONG: sweep {sweep['sweep']:.6g} + OB/FVG ретест, стоп {stop:.6g}, цель {target:.6g}"
                )

        # SHORT
        if direction == "SHORT":
            in_ob = ob and ob["low"] <= close <= ob["high"] if ob else False
            in_fvg = fvg and fvg["low"] <= close <= fvg["high"] if fvg else False
            if in_ob or in_fvg:
                entry = close
                stop = max(sweep["sweep"], (ob["high"] if ob else entry)) + abs(entry - sweep["sweep"]) * 0.1
                target = sweep["prior_low"] - (sweep["prior_high"] - sweep["prior_low"]) * 0.5
                return SMCSignal(
                    direction="SHORT",
                    sweep_price=sweep["sweep"],
                    ob_zone_low=ob["low"] if ob else entry,
                    ob_zone_high=ob["high"] if ob else entry,
                    entry=entry,
                    stop=stop,
                    target=target,
                    reason=f"SMC SHORT: sweep {sweep['sweep']:.6g} + OB/FVG ретест, стоп {stop:.6g}, цель {target:.6g}"
                )
        return None

    def detect_fvg_entry(self, candles: List[dict], target_rr: float = 4.0) -> Optional[dict]:
        """
        Полная стратегия FVG + ChoCh (owner, 2026-09-01, из описания видео):
        1. ChoCh: смена характера (пробитие предыдущего свинг-экстремума).
        2. Первый FVG в направлении нового тренда.
        3. Откат цены в СЕРЕДИНУ FVG → вход.
        4. SL за границей FVG (ниже минимума для LONG, выше максимума для SHORT).
        5. TP = риск × target_rr (по умолчанию 1:4).

        Возвращает dict с полями сигнала или None.
        """
        choch = self.detect_choch(candles)
        if not choch:
            return None
        direction = "LONG" if choch["kind"] == "BULLISH" else "SHORT"
        fvg = self._find_fvg(candles, direction)
        if not fvg:
            return None
        last = candles[-1]
        close = last["close"]
        # Вход при откате в зону FVG (или цена уже в середине)
        fvg_mid = (fvg["low"] + fvg["high"]) / 2
        if not (fvg["low"] <= close <= fvg["high"]):
            return None  # ждём откат в зону
        if direction == "LONG":
            entry = close
            stop = fvg["low"] - (fvg["high"] - fvg["low"]) * 0.5  # за границей FVG
            if stop >= entry:
                return None
            risk = entry - stop
            target = entry + risk * target_rr
            return {
                "direction": "LONG", "entry": entry, "stop": stop,
                "target": target, "fvg_mid": fvg_mid, "fvg_low": fvg["low"],
                "fvg_high": fvg["high"], "choch": choch["kind"],
                "reason": f"FVG+ChoCh LONG: ChoCh {choch['break_price']:.6g}, "
                          f"FVG {fvg['low']:.6g}-{fvg['high']:.6g}, entry {entry:.6g}, "
                          f"SL {stop:.6g}, TP 1:{target_rr} = {target:.6g}"
            }
        else:
            entry = close
            stop = fvg["high"] + (fvg["high"] - fvg["low"]) * 0.5
            if stop <= entry:
                return None
            risk = stop - entry
            target = entry - risk * target_rr
            return {
                "direction": "SHORT", "entry": entry, "stop": stop,
                "target": target, "fvg_mid": fvg_mid, "fvg_low": fvg["low"],
                "fvg_high": fvg["high"], "choch": choch["kind"],
                "reason": f"FVG+ChoCh SHORT: ChoCh {choch['break_price']:.6g}, "
                          f"FVG {fvg['low']:.6g}-{fvg['high']:.6g}, entry {entry:.6g}, "
                          f"SL {stop:.6g}, TP 1:{target_rr} = {target:.6g}"
            }