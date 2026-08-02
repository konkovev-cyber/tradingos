"""
strategies/arc/rules.py
ARC Rules Engine v0.2 — Explain Mode.
Passes through detector explanations for scanner logging.
"""
import logging
from typing import Optional, List, Tuple
from ..base import Signal, MarketSnapshot
from .models import ARCSetup, AreaLevel, RangeZone, CandlePattern
from .detector import detect_areas, detect_range, detect_candle

logger = logging.getLogger("arc.rules")

SL_PERCENT = 0.002
RR_TARGET = 2.0


def evaluate(snapshot: MarketSnapshot) -> Tuple[Optional[Signal], List[str]]:
    """
    Run full ARC evaluation. Returns (signal, explain_log) where
    explain_log has one entry per step, including failures.
    """
    explain = []

    # 1. AREAS
    areas, area_explain = detect_areas(
        snapshot.previous_day_high, snapshot.previous_day_low,
        snapshot.swing_high, snapshot.swing_low,
        snapshot.close,
    )
    explain.append(area_explain)
    if not areas:
        return None, explain

    # 2. RANGE
    range_zone, range_explain = detect_range(areas, snapshot.close)
    explain.append(range_explain)
    if not range_zone:
        return None, explain

    # Check price inside range
    if snapshot.close < range_zone.bottom or snapshot.close > range_zone.top:
        explain.append(f"✖ Price {snapshot.close:.1f} outside range {range_zone.bottom:.1f}-{range_zone.top:.1f}")
        return None, explain

    # 3. CANDLE
    candle, candle_explain = detect_candle(
        snapshot.open, snapshot.high, snapshot.low, snapshot.close, range_zone.size_pips
    )
    explain.append(candle_explain)
    if not candle:
        return None, explain

    # 4. DECIDE DIRECTION
    direction = candle.direction
    entry = snapshot.close

    if direction == "BUY":
        sl = entry * (1 - SL_PERCENT)
        tp = entry + (entry - sl) * RR_TARGET
    else:
        sl = entry * (1 + SL_PERCENT)
        tp = entry - (sl - entry) * RR_TARGET

    rr = (tp - entry) / (entry - sl) if direction == "BUY" else (entry - tp) / (sl - entry)

    # RR check
    if rr < RR_TARGET - 0.01:
        explain.append(f"✖ RR={rr:.1f} < {RR_TARGET}")
        return None, explain

    explain.append(f"✔ RR={rr:.1f} ≥ {RR_TARGET}")

    confidence = min(candle.confidence * 1.1, 1.0)

    signal = Signal(
        symbol=snapshot.symbol,
        timeframe=snapshot.timeframe,
        direction=direction,
        entry=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=round(rr, 2),
        confidence=round(confidence, 2),
        reasons=explain,
        strategy="ARC_v0.2",
    )

    explain.append(f"🔥 SIGNAL: {direction} @ {entry:.2f} RR={rr:.1f}")
    return signal, explain


def evaluate_with_log(snapshot: MarketSnapshot) -> Optional[Signal]:
    """Convenience wrapper: returns signal only (for code that doesn't need explain)."""
    signal, _ = evaluate(snapshot)
    return signal
