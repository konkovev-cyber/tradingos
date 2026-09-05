"""
control_plane/bingx_executor/client.py
BingX Trade Executor — для исполнения TP/SL ордеров через актуальный API.

Использует актуальные эндпоинты BingX v2 API.
Endpoint: /openApi/swap/v2/trade/order
"""
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Optional

import aiohttp
import logging
logger = logging.getLogger("bingx_executor")

ENV_PATH = "/opt/ubot_bingx/.env"
BASE_URL = "https://open-api.bingx.com"


def _load_credentials():
    api_key = ""
    api_secret = ""
    try:
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "BINGX_API_KEY":
                    api_key = v
                elif k == "BINGX_API_SECRET":
                    api_secret = v
    except Exception as e:
        logger.error(f"Failed to load credentials: {e}")
    return api_key, api_secret


def _sign(secret, params: dict) -> str:
    query = urllib.parse.urlencode(sorted(params.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


class BingXExecutor:
    """Executor для TP/SL ордеров. READ-ONLY для orders history, WRITE для placement."""

    def __init__(self):
        self.api_key, self.api_secret = _load_credentials()
        if not self.api_key or not self.api_secret:
            raise ValueError("BingX API credentials not found")

    def _signed_url(self, method: str, path: str, params: dict) -> tuple:
        """Build signed URL for BingX API."""
        params = dict(params or {})
        params["timestamp"] = str(int(time.time() * 1000))
        sig = _sign(self.api_secret, params)
        params["signature"] = sig
        query = urllib.parse.urlencode(params)
        url = f"{BASE_URL}{path}?{query}"
        return url, {"X-BX-APIKEY": self.api_key, "Content-Type": "application/json"}

    async def _get(self, path: str, params: dict = None) -> Optional[dict]:
        url, headers = self._signed_url("GET", path, params or {})
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            try:
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    return data
            except Exception as e:
                logger.error(f"GET {path} failed: {e}")
                return None

    async def _post(self, path: str, params: dict) -> Optional[dict]:
        url, headers = self._signed_url("POST", path, params)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            try:
                async with session.post(url, headers=headers, data="") as resp:
                    data = await resp.json()
                    return data
            except Exception as e:
                logger.error(f"POST {path} failed: {e}")
                return None

    async def get_positions(self) -> list:
        """Read current positions."""
        data = await self._get("/openApi/swap/v2/user/positions")
        if not data or data.get("code") != 0:
            return []
        return data.get("data", [])

    async def get_open_orders(self, symbol: str = None) -> list:
        """Read open conditional orders."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = await self._get("/openApi/swap/v2/trade/openOrders", params)
        if not data or data.get("code") != 0:
            return []
        orders = data.get("data", {})
        if isinstance(orders, dict):
            return orders.get("orders", [])
        return orders if isinstance(orders, list) else []

    async def get_ticker(self, symbol: str) -> Optional[dict]:
        """Get current price for a symbol."""
        params = {"symbol": symbol}
        data = await self._get("/openApi/swap/v2/quote/ticker", params)
        if data and data.get("code") == 0:
            return data.get("data", {})
        return None

    async def place_take_profit(
        self,
        symbol: str,
        side: str,
        position_side: str,
        quantity: float,
        stop_price: float,
        working_type: str = "MARK_PRICE",
    ) -> dict:
        """
        Place a TAKE_PROFIT_MARKET order.
        For LONG positions: side='SELL' to close
        For SHORT positions: side='BUY' to close
        """
        client_id = f"TOS_TP_{int(time.time())}"

        # Try multiple parameter formats
        formats_to_try = [
            # Format 1: minimal
            {
                "clientOrderId": client_id,
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "type": "TAKE_PROFIT_MARKET",
                "quantity": str(quantity),
                "stopPrice": str(stop_price),
                "workingType": working_type,
                "reduceOnly": "true",
            },
            # Format 2: with price field
            {
                "clientOrderId": client_id,
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "type": "TAKE_PROFIT_MARKET",
                "quantity": str(quantity),
                "price": "0",
                "stopPrice": str(stop_price),
                "workingType": working_type,
                "reduceOnly": "true",
                "timeInForce": "GTC",
            },
            # Format 3: without reduceOnly
            {
                "clientOrderId": client_id,
                "symbol": symbol,
                "side": side,
                "positionSide": position_side,
                "type": "TAKE_PROFIT_MARKET",
                "quantity": str(quantity),
                "stopPrice": str(stop_price),
                "workingType": working_type,
            },
        ]

        last_error = None
        for i, params in enumerate(formats_to_try):
            result = await self._post("/openApi/swap/v2/trade/order", params)
            if result:
                if result.get("code") == 0:
                    logger.info(f"TP order placed (format {i+1}): {result.get('data')}")
                    return {"success": True, "data": result.get("data"), "format": i+1}
                last_error = f"code={result.get('code')} msg={result.get('msg')}"
                logger.debug(f"Format {i+1} failed: {last_error}")

        return {"success": False, "error": last_error, "tried_formats": len(formats_to_try)}

    async def verify_tp_exists(
        self,
        symbol: str,
        expected_stop_price: float,
        max_age_seconds: int = 60,
    ) -> bool:
        """Verify TP order exists on exchange."""
        orders = await self.get_open_orders(symbol)
        for o in orders:
            if o.get("type") == "TAKE_PROFIT_MARKET":
                if float(o.get("stopPrice", 0)) == expected_stop_price:
                    return True
        return False

    async def execute_with_verification(
        self,
        symbol: str,
        side: str,
        position_side: str,
        quantity: float,
        stop_price: float,
    ) -> dict:
        """
        Place TP order, then VERIFY on exchange.
        Returns full execution result.
        """
        result = await self.place_take_profit(
            symbol, side, position_side, quantity, stop_price
        )
        if not result.get("success"):
            return result

        # Verify on exchange
        order_id = result.get("data", {}).get("orderId", "")
        if isinstance(order_id, dict):
            order_id = order_id.get("orderId", "")
        verified = await self.verify_tp_exists(symbol, stop_price)
        result["verified"] = verified
        result["orderId"] = order_id
        return result

    async def close(self):
        pass
