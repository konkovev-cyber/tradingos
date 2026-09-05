"""
core/signals/validation.py
HA EMA100 Validation — quality-controlled backtest with lookahead protection.
"""
import csv
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple
from pathlib import Path

from .models import Signal, Direction
from .base import SignalEngine

# ─── Data classes ─────────────────────────────────────────────────────
@dataclass
class OHLCV:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float = 0.0

@dataclass
class HACandle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float

@dataclass
class Trade:
    entry_time: datetime
    entry_price: float
    direction: Direction
    sl: float
    tp: float
    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    result: Optional[float] = None
    duration_bars: int = 0

@dataclass
class ValidationResult:
    symbol: str
    total_signals: int
    trades: List[Trade]
    win_rate: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0
    max_consecutive_losses: int = 0
    max_drawdown: float = 0.0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0

# ─── Data Quality ─────────────────────────────────────────────────────
def check_data_quality(rows: List[OHLCV]) -> List[str]:
    issues = []
    for i in range(1, len(rows)):
        dt1, dt2 = rows[i-1].timestamp, rows[i].timestamp
        gap = (dt2 - dt1).total_seconds()
        if gap > 120:  # >2min gap on M1
            issues.append(f"GAP {gap}s at {dt1}")
        if dt2 <= dt1:
            issues.append(f"TIMESTAMP ORDER at {dt1}")
    # Duplicate check
    seen = set()
    for r in rows:
        h = hashlib.md5(str(r).encode()).hexdigest()
        if h in seen:
            issues.append(f"DUPLICATE at {r.timestamp}")
        seen.add(h)
    return issues

# ─── Heikin Ashi ──────────────────────────────────────────────────────
def heikin_ashi(rows: List[OHLCV]) -> List[HACandle]:
    ha = []
    for i, r in enumerate(rows):
        ha_close = (r.open + r.high + r.low + r.close) / 4.0
        if i == 0:
            ha_open = r.open
        else:
            ha_open = (ha[-1].open + ha[-1].close) / 2.0
        ha_high = max(r.high, ha_open, ha_close)
        ha_low = min(r.low, ha_open, ha_close)
        ha.append(HACandle(r.timestamp, ha_open, ha_high, ha_low, ha_close))
    return ha

# ─── EMA ──────────────────────────────────────────────────────────────
def ema(values: List[float], period: int) -> List[Optional[float]]:
    result: List[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return result
    k = 2.0 / (period + 1)
    ema_val = sum(values[:period]) / period
    result[period-1] = ema_val
    for i in range(period, len(values)):
        ema_val = values[i] * k + ema_val * (1 - k)
        result[i] = ema_val
    return result

# ─── Doji Detection ───────────────────────────────────────────────────
def is_doji(candle: HACandle, threshold: float = 0.3) -> bool:
    """Detect a Doji candle."""
    body = abs(candle.close - candle.open)
    wick = candle.high - candle.low
    if wick == 0:
        return False
    return body < threshold * wick

# ─── Signal Generation ────────────────────────────────────────────────
def generate_ha_ema100_signals(
    rows: List[OHLCV], symbol: str, timeframe: str
) -> List[Signal]:
    ha = heikin_ashi(rows)
    ha_close = [c.close for c in ha]
    ema_vals = ema(ha_close, 100)
    signals = []

    for i in range(100, len(rows)):
        if ema_vals[i] is None:
            continue
        ha_close_val = ha_close[i]
        trend_up = ha_close_val > ema_vals[i]
        trend_down = ha_close_val < ema_vals[i]

        # Need 3 prior candles for pullback + doji check
        if i < 3:
            continue

        c0 = ha[i]     # current (doji candidate)
        c1 = ha[i-1]
        c2 = ha[i-2]

        if not is_doji(c0):
            continue

        # Doji must have range > one of previous two
        r0 = c0.high - c0.low
        r1 = c1.high - c1.low
        r2 = c2.high - c2.low
        if not (r0 > r1 or r0 > r2):
            continue

        # LONG signal: trend up, 2 bearish pullback candles, doji
        if trend_up and c1.close < c1.open and c2.close < c2.open:
            # Check clean pullback (flat bottom for bearish HA)
            if c1.low == min(c1.open, c1.close):
                sl = c0.low - 0.0001
                entry = rows[i].close  # close of doji bar
                tp = entry + (entry - sl)
                signal_id = hashlib.md5(
                    f"HA100_{symbol}_{i}".encode()
                ).hexdigest()[:8]
                signals.append(Signal(
                    id=signal_id,
                    timestamp=rows[i].timestamp,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=Direction.LONG,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name="HA_EMA100",
                    reason=f"Doji after 2 bearish HA pullback above EMA100",
                    metadata={
                        "bar_index": i,
                        "ema": ema_vals[i],
                        "ha_close": ha_close_val,
                    }
                ))

        # SHORT signal: trend down, 2 bullish pullback candles, doji
        if trend_down and c1.close > c1.open and c2.close > c2.open:
            if c1.high == max(c1.open, c1.close):
                sl = c0.high + 0.0001
                entry = rows[i].close
                tp = entry - (sl - entry)
                signal_id = hashlib.md5(
                    f"HA100_{symbol}_{i}".encode()
                ).hexdigest()[:8]
                signals.append(Signal(
                    id=signal_id,
                    timestamp=rows[i].timestamp,
                    symbol=symbol,
                    timeframe=timeframe,
                    direction=Direction.SHORT,
                    entry_price=entry,
                    stop_loss=sl,
                    take_profit=tp,
                    strategy_name="HA_EMA100",
                    reason=f"Doji after 2 bullish HA pullback below EMA100",
                    metadata={
                        "bar_index": i,
                        "ema": ema_vals[i],
                        "ha_close": ha_close_val,
                    }
                ))

    return signals

# ─── Virtual Trade Simulator ──────────────────────────────────────────
def simulate_trades(
    signals: List[Signal], rows: List[OHLCV], max_bars: int = 100,
    spread: float = 0.0001
) -> List[Trade]:
    trades = []
    for sig in signals:
        entry_idx = next(
            (i for i, r in enumerate(rows) if r.timestamp == sig.timestamp), None
        )
        if entry_idx is None:
            continue

        direction = sig.direction
        sl = sig.stop_loss
        tp = sig.take_profit
        entry_price = sig.entry_price + (spread if direction == Direction.LONG else -spread)

        for j in range(entry_idx + 1, min(entry_idx + max_bars, len(rows))):
            bar = rows[j]
            if direction == Direction.LONG:
                if bar.low <= sl:
                    trades.append(Trade(
                        entry_time=sig.timestamp,
                        entry_price=entry_price,
                        direction=direction,
                        sl=sl, tp=tp,
                        exit_time=bar.timestamp,
                        exit_price=sl,
                        result=-1.0,
                        duration_bars=j - entry_idx,
                    ))
                    break
                elif bar.high >= tp:
                    trades.append(Trade(
                        entry_time=sig.timestamp,
                        entry_price=entry_price,
                        direction=direction,
                        sl=sl, tp=tp,
                        exit_time=bar.timestamp,
                        exit_price=tp,
                        result=1.0,
                        duration_bars=j - entry_idx,
                    ))
                    break
        else:
            # Expired - exit at last available price
            last = rows[-1]
            pnl = (last.close - entry_price) / (tp - entry_price) if direction == Direction.LONG else (entry_price - last.close) / (entry_price - tp)
            trades.append(Trade(
                entry_time=sig.timestamp,
                entry_price=entry_price,
                direction=direction,
                sl=sl, tp=tp,
                exit_time=last.timestamp,
                exit_price=last.close,
                result=pnl,
                duration_bars=len(rows) - entry_idx,
            ))

    return trades

# ─── Report ───────────────────────────────────────────────────────────
def compute_stats(symbol: str, trades: List[Trade]) -> ValidationResult:
    res = ValidationResult(symbol=symbol, total_signals=len(trades), trades=trades)
    if not trades:
        return res

    wins = [t for t in trades if t.result is not None and t.result > 0]
    losses = [t for t in trades if t.result is not None and t.result <= 0]

    res.win_rate = len(wins) / len(trades) if trades else 0
    res.avg_r = sum(t.result or 0 for t in trades) / len(trades) if trades else 0

    gross_profit = sum(t.result for t in wins)
    gross_loss = abs(sum(t.result for t in losses))
    res.profit_factor = gross_profit / gross_loss if gross_loss else float('inf')

    # Long/short split
    long_trades = [t for t in trades if t.direction == Direction.LONG]
    short_trades = [t for t in trades if t.direction == Direction.SHORT]
    res.long_win_rate = sum(1 for t in long_trades if t.result and t.result > 0) / len(long_trades) if long_trades else 0
    res.short_win_rate = sum(1 for t in short_trades if t.result and t.result > 0) / len(short_trades) if short_trades else 0

    # Consecutive losses
    streak = 0
    max_streak = 0
    for t in trades:
        if t.result and t.result <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    res.max_consecutive_losses = max_streak

    # Drawdown (equity curve)
    equity = 0.0
    peak = 0.0
    dd = 0.0
    for t in trades:
        equity += t.result or 0
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    res.max_drawdown = dd

    return res

def render_validation_report(results: List[ValidationResult]) -> str:
    lines = [
        "=" * 70,
        "  HA EMA100 FOREX VALIDATION REPORT",
        "=" * 70,
    ]
    for r in results:
        lines += [
            f"\n  {r.symbol}:",
            f"    Signals:      {r.total_signals}",
            f"    Win Rate:     {r.win_rate:.1%}  (LONG: {r.long_win_rate:.0%}  SHORT: {r.short_win_rate:.0%})",
            f"    Avg R:        {r.avg_r:.3f}",
            f"    Profit Factor:{r.profit_factor:.2f}",
            f"    Max Loss Streak: {r.max_consecutive_losses}",
            f"    Max DD:       {r.max_drawdown:.1f}R",
        ]

    # Combined
    all_trades = [t for r in results for t in r.trades]
    combined = compute_stats("ALL", all_trades)
    lines += [
        f"\n  {'='*50}",
        f"  COMBINED ({len(results)} symbols):",
        f"    Signals:      {combined.total_signals}",
        f"    Win Rate:     {combined.win_rate:.1%}",
        f"    Avg R:        {combined.avg_r:.3f}",
        f"    Profit Factor:{combined.profit_factor:.2f}",
        f"    Max Loss Streak: {combined.max_consecutive_losses}",
        f"    Max DD:       {combined.max_drawdown:.1f}R",
    ]
    lines += [
        "",
        "  DISCLAIMER:",
        "  - Virtual execution with fixed spread model",
        "  - No slippage, commissions, or funding costs",
        "  - Does NOT account for real order book dynamics",
        "  - Results pre-optimization (no parameter tuning)",
        "=" * 70,
    ]
    return "\n".join(lines)

# ─── Main Validator ───────────────────────────────────────────────────
def load_csv(path: str) -> List[OHLCV]:
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_str = row.get("datetime") or row.get("time") or row.get("timestamp")
            if not ts_str:
                continue
            if "base64" in ts_str or len(ts_str) < 8:
                continue  # corrupted row
            ts_str = ts_str.replace("T", " ")
            if " " in ts_str and "+" not in ts_str and ts_str.count("-") == 2:
                ts_str += "+00:00"
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue  # invalid timestamp, skip
            try:
                rows.append(OHLCV(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0)),
                ))
            except (ValueError, KeyError):
                continue  # invalid numeric data, skip
    return sorted(rows, key=lambda r: r.timestamp)

def validate_csv(path: str, symbol: str, timeframe: str = "M1") -> ValidationResult:
    rows = load_csv(path)
    issues = check_data_quality(rows)
    if issues:
        print(f"Data quality issues for {symbol}:")
        for iss in issues[:5]:
            print(f"  {iss}")

    signals = generate_ha_ema100_signals(rows, symbol, timeframe)
    trades = simulate_trades(signals, rows)
    return compute_stats(symbol, trades)
