"""
strategies/bybit_position_check.py
Minimal helper to check open Bybit positions.
Used by Reality Mode to prevent duplicate proposals.
Read-only. No orders. No state changes.
Source of truth: Bybit exchange only.
"""
import os, json, time, hmac, hashlib, httpx
from typing import Optional, List, Dict

ENV_PATH = "/root/trading_brain_v4/research/execution/.env"


def _load_credentials() -> tuple[str, str]:
    """Load Bybit API credentials from isolated execution .env."""
    api_key = ""
    api_secret = ""
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "BYBIT_API_KEY":
                        api_key = v.strip()
                    elif k.strip() == "BYBIT_API_SECRET":
                        api_secret = v.strip()
    except FileNotFoundError:
        pass
    return api_key, api_secret


def _get_positions() -> List[Dict]:
    """Fetch all open positions from Bybit. Returns list of position dicts."""
    api_key, api_secret = _load_credentials()
    if not api_key or not api_secret:
        return []
    params = {"category": "linear", "settleCoin": "USDT"}
    ts = str(int(time.time() * 1000))
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign = hmac.new(api_secret.encode(), f"{ts}{api_key}5000{query}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign,
        "X-BAPI-RECV-WINDOW": "5000",
    }
    try:
        resp = httpx.get(f"https://api.bybit.com/v5/position/list?{query}", headers=headers, timeout=10)
        data = resp.json()
        if data.get("retCode") != 0:
            return []
        return [item for item in data["result"].get("list", []) if float(item.get("size", 0)) > 0]
    except Exception:
        return []


def has_open_position(symbol: Optional[str] = None) -> bool:
    """Check if there are any open positions. If symbol provided, check only that symbol."""
    positions = _get_positions()
    if symbol:
        return any(p["symbol"] == symbol for p in positions)
    return len(positions) > 0


def count_open_positions() -> int:
    """Return count of open positions."""
    return len(_get_positions())


def get_open_position_symbols() -> List[str]:
    """Return list of symbols with open positions."""
    return [p["symbol"] for p in _get_positions()]
