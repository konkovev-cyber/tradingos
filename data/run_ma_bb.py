#!env python3
"""
data/run_ma_bb.py — МА30+Bollinger контур (2026-09-02, owner-approved).

Стратегия: MA30 закрылась ниже нижней BB(15,1.5) → LONG; выше верхней → SHORT.
SL = 1×ATR, TP = 2×ATR (R:R 1:2). H1.
Доказано на исправленном стенде: 9/10 стабильных символов, +241R/626 сделок.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("ma_bb")

_ENV_PATH = "/root/trading_brain_v4/research/execution/.env"
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as f:
        for _l in f:
            _l = _l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k, _v = _l.split("=", 1)
                os.environ[_k.strip()] = _v.strip()

for _p in ["/root/tradingos", "/root/trading_brain_v4"]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tradingos.strategies.trade_executor import TradeProposal, _execute_reality
from tradingos.data.run_observation import generate_decision_id

# ─── Стабильные символы (OOS 9/10) ───
SYMBOLS = ["CHILLGUYUSDT","SOLUSDT","PENGUUSDT","INJUSDT","LTCUSDT",
           "TRXUSDT","BOMEUSDT","XRPUSDT","VETUSDT"]
STATE_PATH = Path("/root/tradingos/operations/ma_bb_state.json")

def _load_tm():
    try: return json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
    except: return {}

def _load_state():
    try:
        if STATE_PATH.exists(): return json.loads(STATE_PATH.read_text())
    except: pass
    return {"fired": {}}

def _save_state(s):
    try: STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2))
    except Exception as e: log.warning(f"state: {e}")

def _fetch_h1(symbol):
    import httpx
    r = httpx.get("https://api.bybit.com/v5/market/kline",
                  params={"category":"linear","symbol":symbol,"interval":"60","limit":300}, timeout=15)
    data = r.json()
    if data.get("retCode") != 0: return []
    out = []
    for row in reversed(data.get("result", {}).get("list", [])):
        try:
            out.append({"ts":int(row[0]),"open":float(row[1]),"high":float(row[2]),
                        "low":float(row[3]),"close":float(row[4]),"volume":float(row[5])})
        except: continue
    return out

def _ema(vals, n):
    out=[None]*len(vals)
    if len(vals)<n: return out
    k=2/(n+1); out[n-1]=sum(vals[:n])/n
    for i in range(n,len(vals)): out[i]=vals[i]*k+out[i-1]*(1-k)
    return out

def _sma(vals, n):
    out=[None]*len(vals)
    for i in range(len(vals)):
        if i>=n-1: out[i]=sum(vals[i-n+1:i+1])/n
    return out

def _atr(candles, n=14):
    if len(candles)<n+1: return 0.0
    trs=[]
    for i in range(len(candles)-n, len(candles)):
        c,p = candles[i], candles[i-1]
        trs.append(max(c["high"]-c["low"], abs(c["high"]-p["close"]), abs(c["low"]-p["close"])))
    return sum(trs)/len(trs)

def _signal(candles):
    """MA30 vs BB(15,1.5). Возвращает (side, entry, sl, tp) или None."""
    if len(candles) < 50: return None
    closes=[c["close"] for c in candles]
    ma30 = _ema(closes, 30)
    bb_mid = _sma(closes, 15)
    # BB std
    bb_std = [None]*len(closes)
    for i in range(len(closes)):
        if i>=14:
            w = closes[i-14:i+1]
            m = sum(w)/15
            var = sum((x-m)**2 for x in w)/15
            bb_std[i] = var**0.5
    i = len(candles)-1
    a = _atr(candles)
    if a<=0 or not ma30[i] or not bb_mid[i] or bb_std[i] is None: return None
    upper = bb_mid[i] + 1.5*bb_std[i]
    lower = bb_mid[i] - 1.5*bb_std[i]
    c = candles[i]
    if ma30[i] < lower:
        entry=c["close"]; sl=entry-a; tp=entry+a*2.0
        if sl<entry: return "BUY", entry, sl, tp
    elif ma30[i] > upper:
        entry=c["close"]; sl=entry+a; tp=entry-a*2.0
        if sl>entry: return "SELL", entry, sl, tp
    return None

def _position_open(symbol):
    try:
        from tradingos.strategies.bybit_position_check import get_open_position_symbols
        return symbol in get_open_position_symbols()
    except: return True

async def _scan_once():
    tm = _load_tm()
    if not bool(tm.get("live_trading_enabled", False)):
        log.info("⏸ ma_bb: live off"); return
    state = _load_state()
    try:
        from tradingos.strategies.bybit_position_check import count_open_positions
        open_count = count_open_positions()
    except: open_count = 0
    max_pos = int(tm.get("max_positions", 4))

    for sym in SYMBOLS:
        if _position_open(sym): continue
        if open_count+1 > max_pos: break
        candles = _fetch_h1(sym)
        if len(candles) < 50: continue
        sig = _signal(candles)
        if not sig: continue
        side, entry, sl, tp = sig
        h1_ts = candles[-1]["ts"]
        if state["fired"].get(sym) == h1_ts: continue

        proposal = TradeProposal(
            symbol=sym, side=side, entry=entry, stop_loss=sl, take_profit=tp,
            rr=2.0, confidence=0.6, strategy="MA_BB_H1",
            decision_id=f"MB-{generate_decision_id()}",
            reason=[f"MA30+Bollinger: {sym} {side} MA30 vs BB, SL={sl:.6g} TP={tp:.6g} (R:R 1:2)"],
            session="MA_BB", timestamp=datetime.now(timezone.utc).isoformat())
        valid, msg = proposal.validate()
        if not valid:
            log.warning(f"⏭️ ma_bb {sym}: {msg}"); continue
        proposal.status = "APPROVED"
        log.info(f"🎯 MA_BB signal: {sym} {side} entry={entry:.6g} SL={sl:.6g} TP={tp:.6g}")
        res = await _execute_reality(proposal)
        status = res.get("status","?") if isinstance(res,dict) else str(res)
        log.info(f"🛑 MA_BB EXEC {sym}: {status}")
        if isinstance(res,dict) and status in ("FILLED","SUBMITTED"):
            state["fired"][sym] = h1_ts
            _save_state(state)
        open_count += 1
    _save_state(state)

async def main():
    log.info("🚀 MA_BB контур запущен (MA30+Bollinger, R:R 1:2, H1)")
    while True:
        try: await _scan_once()
        except Exception as e: log.error(f"scan: {e}")
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
