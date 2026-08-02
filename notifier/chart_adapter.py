"""
Exchange adapter for chart generation.
Simple interface matching what generate_trade_chart expects:
- get_klines(symbol, limit, interval) -> list[dict]
"""

from __future__ import annotations
import asyncio
import hmac
import hashlib
import time
import logging
from typing import Optional

import httpx

log = logging.getLogger("TradingOS.ChartAdapter")


class BybitChartAdapter:
    """Minimal Bybit adapter for chart generation."""

    BASE_URL = "https://api.bybit.com"

    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret

    def _auth_headers(self, params: dict) -> dict:
        if not self.api_key:
            return {}
        ts = str(int(time.time() * 1000))
        q = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign = hmac.new(
            self.api_secret.encode(),
            f"{ts}{self.api_key}5000{q}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-SIGN": sign,
            "X-BAPI-RECV-WINDOW": "5000",
        }

    async def get_klines(self, symbol: str, limit: int = 48, interval: str = "15") -> list[dict]:
        """Fetch klines from Bybit (public endpoint, no auth needed)."""
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": str(min(limit, 1000)),
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.BASE_URL}/v5/market/kline", params=params)
                data = resp.json()
            if data.get("retCode") != 0:
                log.warning(f"Bybit kline error: {data.get('retMsg')}")
                return []
            klines = data.get("result", {}).get("list", [])
            # Bybit: newest first → reverse to oldest first
            klines.reverse()
            result = []
            for k in klines:
                result.append({
                    "timestamp": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                })
            return result
        except Exception as e:
            log.warning(f"Bybit kline fetch error: {e}")
            return []

    async def get_ticker(self, symbol: str) -> Optional[dict]:
        """Fetch current ticker."""
        params = {"category": "linear", "symbol": symbol}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.BASE_URL}/v5/market/tickers", params=params)
                data = resp.json()
            if data.get("retCode") != 0:
                return None
            items = data.get("result", {}).get("list", [])
            if items:
                t = items[0]
                return {
                    "lastPrice": float(t.get("lastPrice", 0)),
                    "highPrice24h": float(t.get("highPrice24h", 0)),
                    "lowPrice24h": float(t.get("lowPrice24h", 0)),
                }
        except Exception:
            pass
        return None

    async def get_positions(self, symbol: str = None) -> list[dict]:
        """Fetch open positions (requires auth)."""
        if not self.api_key:
            return []
        params = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/v5/position/list",
                    params=params,
                    headers=self._auth_headers(params),
                )
                data = resp.json()
            if data.get("retCode") != 0:
                return []
            return data.get("result", {}).get("list", [])
        except Exception as e:
            log.warning(f"Bybit positions error: {e}")
            return []
