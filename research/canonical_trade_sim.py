#!/usr/bin/env python3
"""
canonical_trade_sim.py — Single source of truth for trade simulation.

CORRECT SL/TP handling:
  LONG  (dir=+1): SL below entry → hit when LOW <= SL; TP above → hit when HIGH >= TP
  SHORT (dir=-1): SL above entry → hit when HIGH >= SL; TP below → hit when LOW <= TP

Same-bar conservative: if both SL and TP hit on same candle → SL first.

MFE/MAE are direction-aware:
  LONG:  MFE = (HIGH - entry) / sl_dist;  MAE = (entry - LOW) / sl_dist
  SHORT: MFE = (entry - LOW) / sl_dist;   MAE = (HIGH - entry) / sl_dist

NO LOOK-AHEAD: only bars after entry are used. MFE/MAE tracked incrementally.
"""
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Tuple

@dataclass
class TradeResult:
    exit_time: Optional[int] = None
    exit_price: float = 0.0
    exit_reason: str = ""  # "SL", "TP", "TIMEOUT"
    gross_R: float = 0.0
    fee_R: float = 0.0
    slippage_R: float = 0.0
    net_R: float = 0.0
    mfe_R: float = 0.0
    mae_R: float = 0.0
    bars_held: int = 0

def simulate(
    entry_price: float,
    side: str,          # "LONG" or "SHORT"
    sl_price: float,
    tp_price: float,
    highs: np.ndarray,  # OHLC arrays for bars AFTER entry (index 0 = first bar after)
    lows: np.ndarray,
    closes: np.ndarray,
    timestamps: np.ndarray,
    entry_fee_bps: float = 5.5,   # taker 5.5bps per side
    exit_fee_bps: float = 5.5,
    slippage_bps: float = 1.0,    # estimated slippage per side
    max_horizon: int = 96,
) -> TradeResult:
    """Simulate a single trade. Returns TradeResult.
    
    PRECONDITIONS:
      - side in {"LONG", "SHORT"}
      - For LONG: sl_price < entry_price < tp_price
      - For SHORT: tp_price < entry_price < sl_price
      - highs/lows/closes/timestamps have same length >= 1
    """
    dir = 1 if side == "LONG" else -1
    sl_dist = abs(sl_price - entry_price)
    if sl_dist <= 0:
        return TradeResult(exit_reason="INVALID_SL", net_R=-1.0)
    
    # Validate geometry
    if side == "LONG":
        if sl_price >= entry_price or tp_price <= entry_price:
            return TradeResult(exit_reason="INVALID_GEOMETRY", net_R=-1.0)
    else:
        if sl_price <= entry_price or tp_price >= entry_price:
            return TradeResult(exit_reason="INVALID_GEOMETRY", net_R=-1.0)
    
    tp_R = abs(tp_price - entry_price) / sl_dist
    
    mfe_R = 0.0
    mae_R = 0.0
    
    n = min(len(highs), max_horizon)
    
    for j in range(n):
        h = highs[j]
        l = lows[j]
        c = closes[j]
        
        if side == "LONG":
            # MFE = how far price went UP (favorable for LONG)
            mfe_R = max(mfe_R, (h - entry_price) / sl_dist)
            # MAE = how far price went DOWN (adverse for LONG)
            mae_R = max(mae_R, (entry_price - l) / sl_dist)
            # SL hit: LOW reaches SL
            sl_hit = l <= sl_price
            # TP hit: HIGH reaches TP
            tp_hit = h >= tp_price
        else:  # SHORT
            # MFE = how far price went DOWN (favorable for SHORT)
            mfe_R = max(mfe_R, (entry_price - l) / sl_dist)
            # MAE = how far price went UP (adverse for SHORT)
            mae_R = max(mae_R, (h - entry_price) / sl_dist)
            # SL hit: HIGH reaches SL
            sl_hit = h >= sl_price
            # TP hit: LOW reaches TP
            tp_hit = l <= tp_price
        
        # Conservative same-bar: SL first
        if sl_hit and tp_hit:
            # SL first → loss
            result = TradeResult(
                exit_time=timestamps[j] if j < len(timestamps) else None,
                exit_price=sl_price,
                exit_reason="SL",
                gross_R=-1.0,
                mfe_R=mfe_R, mae_R=mae_R,
                bars_held=j + 1,
            )
            return _apply_costs(result, entry_price, sl_dist, entry_fee_bps, exit_fee_bps, slippage_bps)
        elif sl_hit:
            result = TradeResult(
                exit_time=timestamps[j] if j < len(timestamps) else None,
                exit_price=sl_price,
                exit_reason="SL",
                gross_R=-1.0,
                mfe_R=mfe_R, mae_R=mae_R,
                bars_held=j + 1,
            )
            return _apply_costs(result, entry_price, sl_dist, entry_fee_bps, exit_fee_bps, slippage_bps)
        elif tp_hit:
            result = TradeResult(
                exit_time=timestamps[j] if j < len(timestamps) else None,
                exit_price=tp_price,
                exit_reason="TP",
                gross_R=tp_R,
                mfe_R=mfe_R, mae_R=mae_R,
                bars_held=j + 1,
            )
            return _apply_costs(result, entry_price, sl_dist, entry_fee_bps, exit_fee_bps, slippage_bps)
    
    # Timeout: exit at last close
    last_close = closes[n - 1] if n > 0 else entry_price
    if side == "LONG":
        timeout_R = (last_close - entry_price) / sl_dist
    else:
        timeout_R = (entry_price - last_close) / sl_dist
    
    result = TradeResult(
        exit_time=timestamps[n - 1] if n > 0 and n - 1 < len(timestamps) else None,
        exit_price=last_close,
        exit_reason="TIMEOUT",
        gross_R=timeout_R,
        mfe_R=mfe_R, mae_R=mae_R,
        bars_held=n,
    )
    return _apply_costs(result, entry_price, sl_dist, entry_fee_bps, exit_fee_bps, slippage_bps)

def _apply_costs(
    result: TradeResult,
    entry_price: float,
    sl_dist: float,
    entry_fee_bps: float,
    exit_fee_bps: float,
    slippage_bps: float,
) -> TradeResult:
    """Apply fees and slippage to convert gross_R to net_R."""
    # Cost as fraction of sl_dist (R units)
    # fee_R = (fee_bps / 10000) * entry_price / sl_dist
    # For both entry and exit
    total_fee_bps = entry_fee_bps + exit_fee_bps
    total_slip_bps = slippage_bps * 2  # entry + exit
    
    cost_pct = (total_fee_bps + total_slip_bps) / 10000.0
    # Convert to R: cost_R = cost_pct * entry_price / sl_dist
    if sl_dist > 0:
        result.fee_R = (total_fee_bps / 10000.0) * entry_price / sl_dist
        result.slippage_R = (total_slip_bps / 10000.0) * entry_price / sl_dist
    else:
        result.fee_R = 0.0
        result.slippage_R = 0.0
    
    result.net_R = result.gross_R - result.fee_R - result.slippage_R
    return result