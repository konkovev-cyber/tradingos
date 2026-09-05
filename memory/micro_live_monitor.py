#!/usr/bin/env python3
"""
TradingOS Micro Live Monitor — JSONL-based.

Polls position every 30s, writes every state change to a JSONL log,
tracks MFE/MAE/giveback, exits when position closes.

No Telegram. No acceptance tracker. Just evidence.
"""
import sys, os, time, hmac, hashlib, subprocess, json
from datetime import datetime, timezone
from pathlib import Path

API_KEY = "FyZUY69SLCbYUqw2jM"
API_SECRET = "Cg2YkO6Y45WqvFlbnTPa4lucWN8GXy6pnI1B"
BASE = "https://api.bybit.com"

LOG_DIR = Path("/root/tradingos/memory/micro_live")
LOG_DIR.mkdir(parents=True, exist_ok=True)
DECISION_LOG = LOG_DIR / "decision_log.jsonl"
TIMELINE_LOG = LOG_DIR / "timeline.jsonl"
SYMBOL = "DOGEUSDT"


def sign_request(params: dict, method: str) -> tuple:
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    if method == "GET":
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign_str = ts + API_KEY + recv_window + query
        url = f"{BASE}{sys.argv[1] if len(sys.argv) > 1 else ''}?{query}"
        # We'll construct path per call
    payload = ts + API_KEY + recv_window + json.dumps(params, separators=(',', ':'), sort_keys=True)
    sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return ts, recv_window, sig


def call(path: str, params: dict, method: str = "GET") -> dict:
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    if method == "GET":
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        body_for_sign = query
        sign_str = ts + API_KEY + recv_window + body_for_sign
    else:
        body = json.dumps(params, separators=(',', ':'), sort_keys=True)
        body_for_sign = body
        sign_str = ts + API_KEY + recv_window + body_for_sign
    
    sig = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    
    headers = {
        'X-BAPI-API-KEY': API_KEY,
        'X-BAPI-TIMESTAMP': ts,
        'X-BAPI-SIGN': sig,
        'X-BAPI-RECV-WINDOW': recv_window
    }
    
    cmd = ['curl', '-s', '--max-time', '8', '--noproxy', '*']
    for k, v in headers.items():
        cmd.extend(['-H', f'{k}: {v}'])
    
    if method == "POST":
        cmd.extend(['-X', 'POST', '-H', 'Content-Type: application/json', '-d', body_for_sign])
        url = f"{BASE}{path}"
    else:
        url = f"{BASE}{path}?{body_for_sign}"
    
    cmd.append(url)
    
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    if r.stdout:
        try:
            return json.loads(r.stdout)
        except:
            return {}
    return {}


def get_position() -> dict | None:
    result = call("/v5/position/list", {"category": "linear", "settleCoin": "USDT", "symbol": SYMBOL})
    positions = [p for p in result.get('result', {}).get('list', []) if float(p.get('size', 0)) > 0]
    return positions[0] if positions else None


def log_event(event: dict):
    """Append to JSONL log with ISO timestamp."""
    event['ts'] = datetime.now(timezone.utc).isoformat()
    with DECISION_LOG.open('a') as f:
        f.write(json.dumps(event) + '\n')
    with TIMELINE_LOG.open('a') as f:
        f.write(json.dumps(event) + '\n')


def main():
    entry = None
    side = None
    mfe_pct = 0.0
    mae_pct = 0.0
    first_state = True
    
    print(f"[monitor] Starting Micro Live monitor for {SYMBOL}")
    log_event({"event": "MONITOR_START", "symbol": SYMBOL})
    
    poll_count = 0
    while True:
        poll_count += 1
        pos = get_position()
        
        if pos is None:
            print(f"[monitor] Position closed (poll #{poll_count})")
            log_event({
                "event": "POSITION_CLOSED",
                "symbol": SYMBOL,
                "entry": entry,
                "side": side,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
                "polls": poll_count
            })
            break
        
        if first_state:
            entry = float(pos.get('avgPrice', 0))
            side = pos.get('side', 'Buy')
            first_state = False
            log_event({
                "event": "POSITION_INITIAL",
                "symbol": SYMBOL,
                "side": side,
                "entry": entry,
                "qty": float(pos.get('size', 0)),
                "sl": pos.get('stopLoss'),
                "tp": pos.get('takeProfit')
            })
        
        current_entry = float(pos.get('avgPrice', entry))
        mark = float(pos.get('markPrice', 0))
        current_sl = pos.get('stopLoss')
        current_tp = pos.get('takeProfit')
        
        if side == 'Buy':
            pnl_pct = (mark - current_entry) / current_entry * 100
        else:
            pnl_pct = (current_entry - mark) / current_entry * 100
        
        if pnl_pct > mfe_pct:
            mfe_pct = pnl_pct
        if pnl_pct < mae_pct:
            mae_pct = pnl_pct
        
        giveback_pct = mfe_pct - pnl_pct if mfe_pct > 0 else 0
        
        # Detect protection changes
        ts_str = datetime.now(timezone.utc).isoformat()
        
        log_entry = {
            "event": "POLL",
            "poll": poll_count,
            "mark": mark,
            "pnl_pct": round(pnl_pct, 4),
            "mfe_pct": round(mfe_pct, 4),
            "mae_pct": round(mae_pct, 4),
            "giveback_pct": round(giveback_pct, 4),
            "sl": current_sl,
            "tp": current_tp,
            "ts": ts_str
        }
        
        # Write timeline (only key events for size)
        with TIMELINE_LOG.open('a') as f:
            f.write(json.dumps(log_entry) + '\n')
        
        # Log only state changes (decisions)
        # Track previous SL/TP
        if not hasattr(main, '_last_sl'):
            main._last_sl = current_sl
            main._last_tp = current_tp
        
        if current_sl != main._last_sl:
            log_event({
                "event": "SL_CHANGED",
                "symbol": SYMBOL,
                "poll": poll_count,
                "before": main._last_sl,
                "after": current_sl,
                "mfe_pct": round(mfe_pct, 4),
                "reason": "GUARDIAN_PROTECTION"
            })
            main._last_sl = current_sl
        
        if current_tp != main._last_tp:
            log_event({
                "event": "TP_CHANGED",
                "symbol": SYMBOL,
                "poll": poll_count,
                "before": main._last_tp,
                "after": current_tp,
                "reason": "OPERATOR_OR_GUARDIAN"
            })
            main._last_tp = current_tp
        
        # Print summary every 5 polls (5*30s = ~2.5min)
        if poll_count % 5 == 0:
            print(f"[poll #{poll_count}] mark={mark} pnl={pnl_pct:+.2f}% mfe={mfe_pct:+.2f}% sl={current_sl} tp={current_tp}")
        
        time.sleep(30)


if __name__ == "__main__":
    main()
