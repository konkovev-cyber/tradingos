"""
multi_leg_live_detector.py — Multi-leg + multi-symbol DN-sweep live detector.

Runs in parallel to dn_sweep_live_detector.py (which does only single-leg paper-trade).
This detector:
1. Scans 20+ symbols every 30s for DN-sweep patterns
2. For each signal: calls leg_executor.on_signal_detected() which places Tier 1/2/3 legs
3. Maintains 12-20 active legs across the whitelist
4. Amends legs per policy (max 2 per leg, max 5 min to expiry cutoff)
5. Tracks state in /root/tradingos/orders/state.json
6. Logs all events to event_log.jsonl, paper_trades.jsonl

Currently PAPER mode. LIVE requires explicit owner approval.
"""
import json
import time
import sys
import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, "/root/tradingos")

from patterns.reversal_v1.dn_sweep_detector import detect_dn_sweeps
from orders.leg_executor import LegExecutor
from orders.leg_manager import LegManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger("multi_leg_live")

# FIX 2026-09-02 (audit HIGH): keys из .env вместо hardcoded (rotation-safe)
import os
from pathlib import Path as _Path
from dotenv import load_dotenv as _ld
_ld(_Path("/root/.bybit_executor.env"))
KEY = os.getenv("BYBIT_API_KEY", "")
SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE = "https://api-demo.bybit.com"

# Whitelist v2 (VALIDATION_v2_SL800.md, SL-only exit model, WR>=55% & med>0)
WHITELIST = [
    "ADAUSDT", "DOGEUSDT", "GMTUSDT", "INXUSDT", "ONDOUSDT", "OPUSDT",
    "SOLUSDT", "VETUSDT", "WLDUSDT", "ZKUSDT",
]

CYCLE_SEC = 30


def fetch_recent_klines(symbol: str, limit: int = 300):
    """Fetch latest M15 klines for a symbol. Applies bad-candle sanitization."""
    import hmac, hashlib, httpx
    end_ts = int(time.time() * 1000)
    q = f"category=linear&symbol={symbol}&interval=15&end={end_ts}&limit={limit}"
    ts = str(end_ts)
    sig = hmac.new(SECRET.encode(), (ts + KEY + "5000" + q).encode(), hashlib.sha256).hexdigest()
    try:
        r = httpx.get(f"{BASE}/v5/market/kline?{q}",
                      headers={"X-BAPI-API-KEY": KEY, "X-BAPI-TIMESTAMP": ts,
                               "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sig},
                      timeout=15)
        d = r.json()
        if d.get("retCode") != 0:
            return None
        rows = []
        for item in d.get("result", {}).get("list", []):
            rows.append({"ts": int(item[0]), "o": float(item[1]), "h": float(item[2]),
                         "l": float(item[3]), "c": float(item[4]), "v": float(item[5])})
        if not rows:
            return None
        import pandas as pd
        df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
        return sanitize_candles(df, symbol)
    except Exception as e:
        log.debug(f"fetch {symbol}: {e}")
        return None


def sanitize_candles(df, symbol: str):
    """Drop garbage candles from api-demo.

    Observed: TUTUSDT candle with close=10.0 while neighbors ~0.05 (bad feed).
    A candle is bad if its close deviates > BAD_CLOSE_DEV from the rolling
    median of surrounding closes, or OHLC relations are broken.
    """
    import pandas as pd
    if df is None or len(df) < 10:
        return df
    med = df["c"].rolling(11, center=True, min_periods=5).median()
    dev = (df["c"] - med).abs() / med.replace(0, pd.NA)
    bad = (dev > BAD_CLOSE_DEV) | (df["h"] < df["l"]) | (df["c"] <= 0) | (df["l"] <= 0)
    n_bad = int(bad.sum())
    if n_bad:
        log.warning(f"🧹 SANITIZE {symbol}: dropped {n_bad} bad candles")
        df = df[~bad].reset_index(drop=True)
    return df


BAD_CLOSE_DEV = 0.20  # close deviating >20% from local median = bad feed


def get_current_price(symbol: str) -> Optional[float]:
    """Get last price for a symbol."""
    import hmac, hashlib, httpx
    end_ts = int(time.time() * 1000)
    q = f"category=linear&symbol={symbol}"
    ts = str(end_ts)
    sig = hmac.new(SECRET.encode(), (ts + KEY + "5000" + q).encode(), hashlib.sha256).hexdigest()
    try:
        r = httpx.get(f"{BASE}/v5/market/tickers?{q}",
                      headers={"X-BAPI-API-KEY": KEY, "X-BAPI-TIMESTAMP": ts,
                               "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sig},
                      timeout=10)
        d = r.json()
        items = (d.get("result") or {}).get("list", [])
        if items:
            return float(items[0].get("lastPrice", 0))
    except Exception:
        pass
    return None


def predict_regime(symbol: str, signal_ts: int) -> tuple[int, str]:
    """Predict regime from features dataset."""
    try:
        features_path = Path("/root/tradingos/regimes_v1/regime_features_labeled.parquet")
        if not features_path.exists():
            return 1, "TRENDING_UP"
        df = pd.read_parquet(features_path, columns=["ts_s","symbol","regime"])
        sym_df = df[df["symbol"] == symbol].sort_values("ts_s")
        if len(sym_df) == 0:
            return 1, "TRENDING_UP"
        sig_s = signal_ts // 1000
        idx = (sym_df["ts_s"] - sig_s).abs().idxmin()
        if abs(sym_df.loc[idx, "ts_s"] - sig_s) > 14400:
            return 1, "TRENDING_UP"
        regime = int(sym_df.loc[idx, "regime"])
        name = ["VOL_SPIKE","TRENDING_UP","QUIET","BEAR_DROP"][regime]
        return regime, name
    except Exception:
        return 1, "TRENDING_UP"


import pandas as pd

# State for dedup
LAST_SIGNAL_TS = {}  # symbol -> last_ts


def scan_symbol(symbol: str, executor: LegExecutor):
    """Scan one symbol for DN-sweep, place multi-tier legs if found.

    FRESH_BARS filter: only signals from the last 4 M15 bars (1h) are
    considered live. Without this, after a restart the detector would
    re-place legs on a 75-hour-old sweep whose LIMIT window has long passed.
    """
    FRESH_BARS = 4
    df = fetch_recent_klines(symbol, limit=300)
    if df is None or len(df) < 50:
        return
    # FIX 2026-09-02 (audit MEDIUM): контекстный детектор (как в dn_sweep_live_detector)
    # вместо старого detect_dn_sweeps — иначе двойные сигналы с divergence.
    from patterns.reversal_v1.dn_sweep_context import detect_dn_sweeps_context
    sigs = detect_dn_sweeps_context(df, symbol=symbol, pivot_h=16, sweep_pierce_bps=20)
    if not sigs:
        return
    latest_bar_ts = int(df["ts"].iloc[-1])
    fresh_cutoff = latest_bar_ts - FRESH_BARS * 15 * 60 * 1000
    fresh_sigs = [s for s in sigs if s.ts >= fresh_cutoff]
    if not fresh_sigs:
        return
    latest = fresh_sigs[-1]
    # Skip if already logged
    if LAST_SIGNAL_TS.get(symbol, 0) >= latest.ts:
        return
    LAST_SIGNAL_TS[symbol] = latest.ts

    regime, regime_name = predict_regime(symbol, latest.ts)
    log.info(f"📍 DN-SWEEP {symbol} {latest.side} entry={latest.entry_zone:.6g} SL={latest.sl_price:.6g} TP={latest.tp_price:.6g} conf={latest.confidence:.2f} regime={regime_name}")

    ok, reason, leg = executor.on_signal_detected(
        symbol=symbol,
        signal_ts=latest.ts,
        entry_zone=latest.entry_zone,
        confidence=latest.confidence,
        regime=regime,
        regime_name=regime_name,
        sl_hint=latest.sl_price,
        tp_hint=latest.tp_price,
        metadata=latest.metadata,
        side=latest.side,
    )
    if not ok:
        log.debug(f"�️ LEG SKIPPED {symbol}: {reason}")


def main_loop():
    executor = LegExecutor(paper_mode=True)
    log.info(f"🚀 Multi-leg live detector started: {len(WHITELIST)} symbols, paper mode")
    log.info(f"   Target: 12-20 active legs across whitelist")
    cycle = 0
    while True:
        try:
            cycle += 1
            # Phase 1: scan all symbols for new signals
            for sym in WHITELIST:
                scan_symbol(sym, executor)
            # Phase 2: build probe map {symbol: (high_probe, low_probe)}.
            # For symbols with active legs, widen the probes with the current
            # M15 bar's high/low so a TP/SL touch between 30s polls is caught.
            sym_to_price = {}
            active_syms = {l.symbol for l in executor.mgr.legs.values()
                           if l.state in ("PLACED", "FILLED", "BRACKET")}
            for sym in WHITELIST:
                px = get_current_price(sym)
                if not px:
                    continue
                if sym in active_syms:
                    df = fetch_recent_klines(sym, limit=3)
                    if df is not None and len(df) > 0:
                        bar = df.iloc[-1]
                        sym_to_price[sym] = (max(px, float(bar["h"])), min(px, float(bar["l"])))
                    else:
                        sym_to_price[sym] = (px, px)
                else:
                    sym_to_price[sym] = (px, px)
            executor.run_cycle(sym_to_price)
            # Periodic stats
            if cycle % 20 == 0:
                stats = executor.mgr.get_stats()
                n_placed = stats["state_counts"].get("PLACED", 0)
                n_brk = stats["state_counts"].get("BRACKET", 0)
                log.info(f"� cycle={cycle} | placed={n_placed} bracket={n_brk} closed={stats['n_closed']} WR={stats['wr_pct']:.0f}% total_pnl={stats['total_pnl_pct']*100:+.2f}%")
        except Exception as e:
            log.error(f"cycle error: {e}")
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    main_loop()
