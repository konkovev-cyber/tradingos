"""
guardian/capital_utilization.py
Capital Utilization Monitor — non-trading KPI measurement.
Tracks how efficiently the risk budget is used across open positions.
Does NOT modify any positions, orders, or strategy.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("capital_util")

CAPITAL_LOG = Path("/root/tradingos/guardian/capital_utilization.jsonl")


def snapshot(
    balance: float = 0,
    equity: float = 0,
    free_margin: float = 0,
    used_margin: float = 0,
    positions: int = 0,
    max_positions: int = 4,
    risk_budget: float = 1.00,
    risk_used: float = 0.0,
):
    """Record one capital utilization snapshot. No trading actions."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "free_margin": round(free_margin, 2),
        "used_margin": round(used_margin, 2),
        "margin_util_pct": round(used_margin / max(equity, 0.01) * 100, 1),
        "positions": positions,
        "max_positions": max_positions,
        "position_util_pct": round(positions / max(max_positions, 1) * 100, 1),
        "risk_budget": risk_budget,
        "risk_used": round(risk_used, 2),
        "risk_util_pct": round(risk_used / max(risk_budget, 0.01) * 100, 1),
    }
    CAPITAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(CAPITAL_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def take_snapshot_from_exchange(max_pos: int = 4, risk_budget: float = 1.0) -> Optional[Dict]:
    """Fetch live data from Bybit and take a capital utilization snapshot."""
    import hmac, hashlib, httpx
    ak, as_ = "", ""
    env_path = "/root/trading_brain_v4/research/execution/.env"
    try:
        with open(env_path) as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, v = l.split("=", 1)
                    if k.strip() == "BYBIT_API_KEY":
                        ak = v.strip()
                    elif k.strip() == "BYBIT_API_SECRET":
                        as_ = v.strip()
    except FileNotFoundError:
        return None

    if not ak or not as_:
        return None

    # Get account balance
    ts = str(int(time.time() * 1000))
    q = "accountType=UNIFIED&coin=USDT"
    sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
    h = {"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000"}
    try:
        r = httpx.get(f"https://api.bybit.com/v5/account/wallet-balance?{q}", headers=h, timeout=10)
        d = r.json()
        equity = 0.0
        wallet = 0.0
        available = 0.0
        if d.get("retCode") == 0:
            for item in d["result"]["list"]:
                for c in item.get("coin", []):
                    if c["coin"] == "USDT":
                        equity = float(c.get("equity", 0) or 0)
                        wallet = float(c.get("walletBalance", 0) or 0)
                        avail_raw = c.get("availableToWithdraw", "0")
                        available = float(avail_raw) if avail_raw and avail_raw != "" else 0.0
    except Exception as e:
        logger.error(f"Balance fetch failed: {e}")
        return None

    # Get positions
    ts2 = str(int(time.time() * 1000))
    q2 = "category=linear&settleCoin=USDT"
    sign2 = hmac.new(as_.encode(), f"{ts2}{ak}5000{q2}".encode(), hashlib.sha256).hexdigest()
    h2 = {"X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts2, "X-BAPI-SIGN": sign2, "X-BAPI-RECV-WINDOW": "5000"}
    positions = []
    total_risk = 0.0
    try:
        r2 = httpx.get(f"https://api.bybit.com/v5/position/list?{q2}", headers=h2, timeout=10)
        d2 = r2.json()
        if d2.get("retCode") == 0:
            for i in d2["result"]["list"]:
                pos_size = float(i.get("size", 0) or 0)
                if pos_size > 0:
                    positions.append(i)
                    entry = float(i.get("avgPrice", 0) or 0)
                    sl_raw = i.get("stopLoss", "0")
                    sl = float(sl_raw) if sl_raw and sl_raw != "" else 0.0
                    if sl > 0:
                        total_risk += abs(entry - sl) * pos_size
    except Exception as e:
        logger.error(f"Positions fetch failed: {e}")
        return None

    used_margin = max(0, wallet - available) if wallet > 0 else 0
    free_margin = available if available > 0 else 0

    return snapshot(
        balance=wallet,
        equity=equity,
        free_margin=free_margin,
        used_margin=used_margin,
        positions=len(positions),
        max_positions=max_pos,
        risk_budget=risk_budget,
        risk_used=total_risk,
    )


def daily_summary() -> Optional[Dict]:
    """Generate daily capital utilization summary from recent snapshots."""
    if not CAPITAL_LOG.exists():
        return None

    snapshots = []
    cutoff = time.time() - 86400
    with open(CAPITAL_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rec = json.loads(line)
                    ts = datetime.fromisoformat(rec["timestamp"]).timestamp()
                    if ts >= cutoff:
                        snapshots.append(rec)
                except:
                    pass

    if not snapshots:
        return None

    # Compute stats
    avg_margin_util = sum(s["margin_util_pct"] for s in snapshots) / len(snapshots)
    avg_position_util = sum(s["position_util_pct"] for s in snapshots) / len(snapshots)
    avg_risk_util = sum(s["risk_util_pct"] for s in snapshots) / len(snapshots)
    latest = snapshots[-1]

    return {
        "period": "24h",
        "snapshots": len(snapshots),
        "latest_balance": latest["balance"],
        "latest_equity": latest["equity"],
        "avg_margin_util_pct": round(avg_margin_util, 1),
        "avg_position_util_pct": round(avg_position_util, 1),
        "avg_risk_util_pct": round(avg_risk_util, 1),
        "peak_positions": max(s["positions"] for s in snapshots),
        "min_positions": min(s["positions"] for s in snapshots),
    }
