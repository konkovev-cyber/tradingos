"""
strategies/arc/detector.py
ARC Detector v0.2 — Explain Mode.
Each function returns (result, reason) so caller knows why something failed.
"""
import logging
from typing import List, Optional, Tuple
from .models import AreaLevel, RangeZone, CandlePattern

logger = logging.getLogger("arc.detector")


def detect_areas(
    prev_day_high: float, prev_day_low: float,
    swing_high: float, swing_low: float,
    current_price: float,
) -> Tuple[List[AreaLevel], str]:
    """Identify key area levels. Returns (levels, explain)."""
    levels = []
    reasons = []
    if prev_day_high:
        levels.append(AreaLevel("PDH", prev_day_high, 0.9))
        reasons.append(f"PDH={prev_day_high:.1f}")
    if prev_day_low:
        levels.append(AreaLevel("PDL", prev_day_low, 0.9))
        reasons.append(f"PDL={prev_day_low:.1f}")
    if swing_high:
        levels.append(AreaLevel("SWING_HIGH", swing_high, 0.7))
        reasons.append(f"SWING_HIGH={swing_high:.1f}")
    if swing_low:
        levels.append(AreaLevel("SWING_LOW", swing_low, 0.7))
        reasons.append(f"SWING_LOW={swing_low:.1f}")

    if not levels:
        return [], "✖ No area data (prev_day/swing levels missing)"

    explain = f"✔ Areas: {', '.join(reasons)} | price={current_price:.1f}"
    return levels, explain


def detect_range(levels: List[AreaLevel], current_price: float) -> Tuple[Optional[RangeZone], str]:
    """Find the active trading range. Returns (zone, explain)."""
    if len(levels) < 2:
        return None, "✖ Range: need ≥2 area levels"

    prices = [l.price for l in levels]
    top = max(prices)
    bottom = min(prices)
    mid = (top + bottom) / 2
    size = top - bottom

    zone = RangeZone(top=top, bottom=bottom, mid=mid, size_pips=size)

    if bottom <= current_price <= top:
        return zone, f"✔ Range: {bottom:.1f}-{top:.1f} (price {current_price:.1f} inside)"

    return zone, f"⚠️ Range: {bottom:.1f}-{top:.1f} (price {current_price:.1f} OUTSIDE)"


def detect_candle(
    open_p: float, high: float, low: float, close: float, range_size: float,
    hammer_ratio: float = 2.0, rej_pct: float = 0.7, break_pct: float = 0.7,
) -> Tuple[Optional[CandlePattern], str]:
    """Detect candle pattern with explanation. Now accepts custom thresholds."""
    body = abs(close - open_p)
    upper_wick = high - max(open_p, close)
    lower_wick = min(open_p, close) - low
    total = high - low

    explain_parts = [f"body={body:.1f}", f"up_wick={upper_wick:.1f}", f"low_wick={lower_wick:.1f}"]
    explain_base = ", ".join(explain_parts)

    if total == 0:
        return None, f"✖ Candle: zero range ({explain_base})"

    bullish = close > open_p
    bearish = close < open_p

    # HAMMER
    if lower_wick > body * hammer_ratio and upper_wick < body * 0.5 and bearish:
        return (CandlePattern("HAMMER", "BUY", close, 0.7),
                f"✔ HAMMER (BUY) — {explain_base}, lower_wick > {hammer_ratio}×body")

    # INV_HAMMER
    if upper_wick > body * hammer_ratio and lower_wick < body * 0.5 and bullish:
        return (CandlePattern("INV_HAMMER", "SELL", close, 0.7),
                f"✔ INV_HAMMER (SELL) — {explain_base}, upper_wick > {hammer_ratio}×body")

    # REJECTION_WICK
    if upper_wick > total * rej_pct and lower_wick < total * 0.1:
        return (CandlePattern("REJECTION_WICK", "SELL", close, 0.8),
                f"✔ REJECTION_WICK (SELL) — {explain_base}, upper_wick={upper_wick/total*100:.0f}% of range (threshold {rej_pct*100:.0f}%)")
    if lower_wick > total * rej_pct and upper_wick < total * 0.1:
        return (CandlePattern("REJECTION_WICK", "BUY", close, 0.8),
                f"✔ REJECTION_WICK (BUY) — {explain_base}, lower_wick={lower_wick/total*100:.0f}% of range (threshold {rej_pct*100:.0f}%)")

    # BREAKOUT
    if body > total * break_pct:
        if bullish:
            return (CandlePattern("BREAKOUT", "BUY", close, 0.8),
                    f"✔ BREAKOUT (BUY) — {explain_base}, body={body/total*100:.0f}% of range (threshold {break_pct*100:.0f}%)")
        if bearish:
            return (CandlePattern("BREAKOUT", "SELL", close, 0.8),
                    f"✔ BREAKOUT (SELL) — {explain_base}, body={body/total*100:.0f}% of range (threshold {break_pct*100:.0f}%)")

    # No pattern
    return None, f"✖ No pattern: {explain_base}, range={total:.1f}"
