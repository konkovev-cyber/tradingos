"""Phase3 adapter — bridges BTC shadow process events to Data Lake."""
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from core.data_lake.sqlite_backend import DataLake


HEARTBEAT_FILE = Path(__file__).parent.parent.parent.parent / "trading_brain_v4" / "shadow_logs" / "heartbeat_btc.jsonl"
HEARTBEAT_FILE2 = Path(__file__).parent.parent.parent.parent / "trading_brain_v4" / "shadow_logs" / "phase3_btc.log"
STDERR_FILE = Path(__file__).parent.parent.parent.parent / "trading_brain_v4" / "shadow_logs" / "phase3_btc_stderr.log"
SIGNAL_PATTERNS = ["SHADOW SIGNAL", "SHADOW OPEN", "SHADOW CLOSE", "CYCLE", "Regime:"]
EVENT_TYPES = {
    "CYCLE": "CycleCompleted",
    "SHADOW SIGNAL": "SignalGenerated",
    "SHADOW OPEN": "PositionOpened",
    "SHADOW CLOSE": "PositionClosed",
    "Regime:": "RegimeDetected",
    "HEARTBEAT": "Heartbeat",
}


def parse_log_line(line: str) -> dict | None:
    """Parse a stderr log line into a Data Lake event dict."""
    if not line or "Phase3Crypto" not in line:
        return None

    now = datetime.now(timezone.utc).isoformat()
    event_type = None
    payload = {"raw": line.strip()}

    for pattern, etype in EVENT_TYPES.items():
        if pattern in line:
            event_type = etype
            break

    if not event_type:
        return None

    if "CYCLE" in line:
        try:
            price = float(line.split("price=")[1].split()[0])
            atr = float(line.split("atr=")[1].split()[0])
            adx = float(line.split("adx=")[1].split()[0])
            vol = float(line.split("vol=")[1].split()[0])
            cycle_num = int(line.split("CYCLE ")[1].split(":")[0])
            payload.update({"price": price, "atr": atr, "adx": adx, "volume": vol, "cycle": cycle_num})
        except Exception:
            pass

    elif "Regime:" in line:
        try:
            regime = line.split("Regime:")[1].split("(")[0].strip()
            conf = float(line.split("conf=")[1].split(")")[0])
            payload.update({"regime": regime, "confidence": conf})
        except Exception:
            pass

    elif "SHADOW SIGNAL" in line:
        try:
            parts = line.split("SHADOW SIGNAL:")[1].strip().split()
            direction = parts[0]
            symbol = parts[1]
            price = float(parts[2])
            regime = line.split("regime=")[1].split()[0]
            score = float(line.split("score=")[1].split()[0])
            payload.update({
                "direction": direction, "symbol": symbol, "price": price,
                "regime": regime, "score": score,
            })
        except Exception:
            pass

    elif "SHADOW OPEN" in line:
        try:
            trade_id = line.split("SHADOW OPEN:")[1].strip().split()[0]
            direction = line.split("SHADOW OPEN:")[1].strip().split()[1]
            price = float(line.split("@ ")[1].split()[0])
            payload.update({"trade_id": trade_id, "direction": direction, "price": price})
        except Exception:
            pass

    elif "SHADOW CLOSE" in line:
        try:
            trade_id = line.split("SHADOW CLOSE:")[1].strip().split()[0]
            exit_price = float(line.split("exit=")[1].split()[0])
            pnl = float(line.split("pnl=")[1].split()[0])
            payload.update({"trade_id": trade_id, "exit_price": exit_price, "pnl": pnl})
        except Exception:
            pass

    return {
        "event_type": event_type,
        "timestamp": now,
        "source_module": "phase3_adapter",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "severity": "info",
        "payload": payload,
    }


def ingest_once(dal: DataLake) -> int:
    """Read stderr log and ingest new events. Returns count written."""
    if not STDERR_FILE.exists():
        return 0

    with open(STDERR_FILE, "r") as f:
        lines = f.readlines()

    written = 0
    for line in lines:
        if "Phase3Crypto" not in line:
            continue
        event = parse_log_line(line)
        if event:
            dal.write(event)
            written += 1
    return written


def run_daemon(interval: int = 60):
    """Continuously ingest Phase3 events."""
    dal = DataLake()
    print(f"Phase3 adapter started. Ingesting every {interval}s from {STDERR_FILE}")
    last_pos = 0
    try:
        while True:
            if not STDERR_FILE.exists():
                time.sleep(interval)
                continue

            with open(STDERR_FILE, "r") as f:
                f.seek(last_pos)
                new_lines = f.readlines()
                last_pos = f.tell()

            written = 0
            for line in new_lines:
                event = parse_log_line(line)
                if event:
                    dal.write(event)
                    written += 1

            if written:
                print(f"[{datetime.now().isoformat()}] Ingested {written} events")

            time.sleep(interval)
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        dal.close()
