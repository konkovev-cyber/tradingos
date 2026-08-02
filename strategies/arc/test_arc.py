"""
strategies/arc/test_arc.py
Minimal test — feed XAUUSD M5 history through ARC, show signals.
"""
import sys, csv
from pathlib import Path
from datetime import datetime
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from strategies.base import MarketSnapshot
from strategies.arc.rules import evaluate

DATA_DIR = Path("/root/mt5_trading_bot/data")
RESULT_LOG = Path("/root/tradingos/logs/signals/arc_signals.jsonl")

def load_candles(path: str) -> list:
    """Load XAUUSD M5 historical data."""
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("open") or not row.get("high"):
                continue
            rows.append({
                "time": row.get("time", ""),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0)),
            })
    return rows

def run_test():
    # Find XAUUSD M5 data
    csv_path = DATA_DIR / "XAUUSD_M5_full.csv"
    if not csv_path.exists():
        print(f"Data not found: {csv_path}")
        return

    candles = load_candles(str(csv_path))
    print(f"Loaded {len(candles)} XAUUSD M5 candles")

    # Create logs dir
    RESULT_LOG.parent.mkdir(parents=True, exist_ok=True)

    signals_found = 0
    prev_day_high = 0.0
    prev_day_low = 0.0

    for i in range(100, len(candles), 1):
        c = candles[i]
        # Rolling swing levels (20-period)
        window = candles[max(0, i-20):i]
        swing_high = max(w["high"] for w in window)
        swing_low = min(w["low"] for w in window)

        # Previous day levels (approx — use last 288 M5 candles as "day")
        day_window = candles[max(0, i-288):i]
        prev_day_high = max(w["high"] for w in day_window)
        prev_day_low = min(w["low"] for w in day_window)

        snap = MarketSnapshot(
            symbol="XAUUSD",
            timeframe="M5",
            open=c["open"],
            high=c["high"],
            low=c["low"],
            close=c["close"],
            volume=c["volume"],
            previous_day_high=prev_day_high,
            previous_day_low=prev_day_low,
            swing_high=swing_high,
            swing_low=swing_low,
        )

        signal = evaluate(snap)
        if signal:
            signals_found += 1
            log_line = (
                f"[{c['time']}] ARC {signal.direction} "
                f"entry={signal.entry:.2f} SL={signal.stop_loss:.2f} "
                f"TP={signal.take_profit:.2f} RR={signal.risk_reward} "
                f"conf={signal.confidence} | {'; '.join(signal.reasons)}"
            )
            print(log_line)

            # Save to log
            with RESULT_LOG.open("a") as f:
                f.write(f"{{\"time\":\"{c['time']}\",\"symbol\":\"XAUUSD\","
                       f"\"strategy\":\"ARC_v0.1\",\"direction\":\"{signal.direction}\","
                       f"\"entry\":{signal.entry},\"sl\":{signal.stop_loss},"
                       f"\"tp\":{signal.take_profit},\"rr\":{signal.risk_reward},"
                       f"\"confidence\":{signal.confidence},"
                       f"\"reasons\":{signal.reasons}}}\n")

    print(f"\nTotal ARC signals: {signals_found} / {len(candles)} candles ({signals_found/len(candles)*100:.2f}%)")
    print(f"Signal log: {RESULT_LOG}")

if __name__ == "__main__":
    run_test()
