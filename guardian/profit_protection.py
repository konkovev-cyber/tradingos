"""
guardian/profit_protection.py
Profit Protection Layer v1 — Alert Only.
Monitors open positions and alerts when profit should be secured.
No autonomous closing. Human approval required.

P1: Added timeout check — alerts on positions held beyond max_hold_hours.
"""
import asyncio, json, logging, time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("profit_guardian")

ALERTS_PATH = Path("/root/tradingos/guardian/profit_alerts.jsonl")

# P1: Timeout configuration
MAX_HOLD_HOURS = 48  # Default: alert after 48 hours
TIMEOUT_LOG = Path("/root/tradingos/guardian/timeout_alerts.jsonl")


async def bingx_get(endpoint: str, params: dict = None) -> dict:
    import hashlib, hmac, time, httpx, urllib.parse
    key = '1GLz8DKdTi00cktb4GAFc7PXUdURwpUspmogSBtvTzBiqtLwnWNjURD35bc4BMAkDpjAHkmGT0xqywxQ'
    secret = 'h49fKOeGzqTL2A2Qn9gucLxnZSxgzMm6DK2RojtFNAKCohl2tcUWiUV2TQRMRnjjgjjRU0Ht5MLKbivHR5w'
    p = params or {}
    p["timestamp"] = str(int(time.time() * 1000))
    q = urllib.parse.urlencode(sorted(p.items()))
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"https://open-api.bingx.com{endpoint}?{q}&signature={sig}",
                                headers={"X-BX-APIKEY": key})
        return resp.json()


def profit_alert(symbol: str, side: str, entry: float, current: float,
                 pnl_pct: float, peak_pnl: float, reason: str,
                 sl_after: float = None, tp_distance: float = None):
    """Create profit protection alert."""
    giveback = None
    if peak_pnl > 0:
        giveback = round((peak_pnl - pnl_pct) / peak_pnl * 100, 1)

    alert = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "PROFIT_PROTECTION",
        "severity": "WARNING" if giveback and giveback > 30 else "INFO",
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "current": current,
        "pnl_pct": round(pnl_pct, 2),
        "peak_pnl": round(peak_pnl, 2) if peak_pnl else None,
        "giveback_pct": giveback,
        "reason": reason,
        "recommendation": None,
        "status": "PENDING",
    }

    # Recommendation logic
    if giveback and giveback > 50:
        alert["recommendation"] = f"Consider closing — {giveback}% profit lost from peak"
        alert["severity"] = "CRITICAL"
    elif giveback and giveback > 30:
        alert["recommendation"] = f"Move SL to protect remaining profit ({giveback}% giveback)"
    elif pnl_pct > 3 and tp_distance and tp_distance > pnl_pct * 2:
        alert["recommendation"] = f"TP at +{tp_distance:.1f}% too far. Consider partial close at +{pnl_pct:.1f}%"
    elif pnl_pct > 2:
        alert["recommendation"] = "Profit above 2% — move SL to breakeven" if sl_after is None else None
    if sl_after:
        alert["sl_after"] = sl_after

    ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ALERTS_PATH.open("a") as f:
        f.write(json.dumps(alert) + "\n")

    icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🟢"}.get(alert["severity"], "?")
    logger.info(f"{icon} PROFIT: {symbol} ({side}) pnl={pnl_pct:+.2f}% {'giveback=' + str(giveback) + '%' if giveback else ''}")
    if alert.get("recommendation"):
        logger.info(f"   → {alert['recommendation']}")

    return alert


def timeout_alert(symbol: str, side: str, entry: float, open_time: float,
                  hold_hours: float, max_hours: float = MAX_HOLD_HOURS):
    """
    P1: Create timeout alert for positions held beyond max_hold_hours.
    Alert only. No position modification. No execution.
    """
    alert = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "GUARDIAN_TIMEOUT",
        "severity": "WARNING",
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "hold_hours": round(hold_hours, 1),
        "max_hours": max_hours,
        "action": "Human review required",
        "note": "Timeout event does NOT modify position, SL, or TP",
    }

    TIMEOUT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with TIMEOUT_LOG.open("a") as f:
        f.write(json.dumps(alert) + "\n")

    logger.warning(
        f"⏰ GUARDIAN TIMEOUT: {symbol} ({side}) "
        f"held {hold_hours:.1f}h > {max_hours}h limit"
    )
    logger.warning(f"   → Action: Human review required")
    logger.warning(f"   → Position, SL, TP NOT modified")

    return alert


def check_timeout(symbol: str, side: str, entry: float, open_time: float,
                  max_hours: float = MAX_HOLD_HOURS) -> Optional[dict]:
    """
    P1: Check if position has exceeded max hold time.
    Returns alert dict if timeout, None otherwise.
    No position modification. No execution.
    """
    hold_hours = (time.time() - open_time) / 3600
    if hold_hours > max_hours:
        return timeout_alert(symbol, side, entry, open_time, hold_hours, max_hours)
    return None


async def scan_protection():
    """Scan all positions for profit protection needs."""
    positions_data = await bingx_get("/openApi/swap/v2/user/positions")
    if positions_data.get("code") != 0:
        logger.error("Cannot fetch positions")
        return

    alerts = []
    for p in positions_data.get("data", []):
        amt = float(p.get("positionAmt", 0) or 0)
        if abs(amt) < 0.0001:
            continue

        entry = float(p.get("avgPrice", 0))
        mark = float(p.get("markPrice", 0))
        pnl = float(p.get("unrealizedProfit", 0))
        pnl_pct = float(p.get("pnlRatio", 0)) * 100
        side = p.get("positionSide", "LONG")
        symbol = p.get("symbol", "")
        sl = float(p.get("stopLoss", 0) or 0)
        tp = float(p.get("takeProfit", 0) or 0)

        # Skip if no profit
        if pnl_pct <= 0.5:
            continue

        # Get TP distance from open orders
        orders_data = await bingx_get("/openApi/swap/v2/trade/openOrders", {"symbol": symbol})
        tp_orders = [o for o in orders_data.get("data", {}).get("orders", [])
                     if o.get("type") == "TAKE_PROFIT_MARKET"]
        tp_price = float(tp_orders[0].get("stopPrice", 0)) if tp_orders else tp if tp > 0 else 0
        tp_distance = round((tp_price - entry) / entry * 100, 2) if tp_price > 0 else None

        # Peak tracking from file
        peak_file = Path(f"/root/tradingos/guardian/peaks/{symbol.replace('-', '_')}_peak.json")
        peak_pnl = 0.0
        if peak_file.exists():
            try:
                peak_pnl = json.loads(peak_file.read_text()).get("peak_pnl", 0)
            except:
                pass
        if pnl_pct > peak_pnl:
            peak_pnl = pnl_pct
            peak_file.parent.mkdir(parents=True, exist_ok=True)
            peak_file.write_text(json.dumps({"peak_pnl": peak_pnl, "updated": datetime.now().isoformat()}))

        # Giveback check
        giveback_pct = 0
        if peak_pnl > 0 and pnl_pct < peak_pnl:
            giveback_pct = (peak_pnl - pnl_pct) / peak_pnl * 100

        # Generate alert if needed
        alert = None
        if giveback_pct > 30:
            alert = profit_alert(symbol, side, entry, mark, pnl_pct, peak_pnl,
                                 f"{giveback_pct:.0f}% profit lost from peak of +{peak_pnl:.1f}%")
        elif pnl_pct > 3 and tp_distance and tp_distance > pnl_pct * 1.5:
            alert = profit_alert(symbol, side, entry, mark, pnl_pct, peak_pnl,
                                 f"TP ({tp_distance:.1f}%) too far — recommend partial at +{pnl_pct:.1f}%")
        elif pnl_pct > 2 and sl < entry:
            alert = profit_alert(symbol, side, entry, mark, pnl_pct, peak_pnl,
                                 f"Profit +{pnl_pct:.1f}% with SL below entry — move to breakeven",
                                 sl_after=round(entry, 4))

        if alert:
            alerts.append(alert)

    return alerts


def show_alerts(alerts: list):
    """Display alerts in console."""
    if not alerts:
        print("✅ No profit protection alerts")
        return
    print(f"\n{'='*55}")
    print(f"  PROFIT PROTECTION — {len(alerts)} alert(s)")
    print(f"{'='*55}")
    for a in alerts:
        icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🟢"}.get(a["severity"], "?")
        print(f"  {icon} {a['symbol']} {a['side']} | PnL: {a['pnl_pct']:+.2f}%")
        if a.get("giveback_pct"):
            print(f"     Giveback: {a['giveback_pct']}% from peak")
        print(f"     → {a.get('recommendation', 'No action')}")


async def run():
    logger.info("Profit Protection Layer v1 — scanning")
    alerts = await scan_protection()
    show_alerts(alerts)


if __name__ == "__main__":
    asyncio.run(run())
