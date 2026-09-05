"""
BingX API Client — получение реальных позиций и данных рынка.
HMAC подпись для всех приватных эндпоинтов.
"""

import hashlib
import hmac
import time
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("BingXClient")


class BingXClient:
    """BingX API Client."""

    BASE_URL = "https://open-api.bingx.com"

    def __init__(self, api_key: str, api_secret: str):
        self._api_key = api_key
        self._api_secret = api_secret
        self._session = None

    async def _request(
        self, method: str, path: str, params: Optional[Dict] = None, signed: bool = False
    ) -> Dict[str, Any]:
        """Выполнить запрос к API. Подписанные параметры — в URL query string в правильном порядке."""
        import aiohttp

        if params is None:
            params = {}

        # Build query string with sorted params + signature
        if signed:
            params["timestamp"] = str(int(time.time() * 1000))
            # Convert all values to string for URL query string
            str_params = {k: str(v).lower() if isinstance(v, bool) else str(v) 
                         for k, v in params.items()}
            qs = "&".join(f"{k}={v}" for k, v in sorted(str_params.items()))
            signature = hmac.new(
                self._api_secret.encode("utf-8"),
                qs.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            url = f"{self.BASE_URL}{path}?{qs}&signature={signature}"
        else:
            url = f"{self.BASE_URL}{path}"

        headers = {"X-BX-APIKEY": self._api_key}

        if self._session is None:
            self._session = aiohttp.ClientSession()

        try:
            async with self._session.request(
                method, url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                return await resp.json()
        except Exception as e:
            logger.error(f"BingX API error: {e}")
            return {"code": -1, "msg": str(e)}

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Получить все открытые позиции."""
        result = await self._request("GET", "/openApi/swap/v2/user/positions", {"symbol": ""}, signed=True)
        if result.get("code") != 0:
            logger.error(f"Failed to get positions: {result}")
            return []
        data = result.get("data", [])
        if isinstance(data, list):
            positions = data
        elif isinstance(data, dict):
            positions = data.get("positions", [])
        else:
            positions = []
        return [p for p in positions if float(p.get("positionAmt", 0)) != 0]

    async def cancel_existing_stops(self, symbol: str) -> None:
        """Отменить существующие SL/TP ордера по символу (BingX: DELETE /trade/order)."""
        try:
            existing = await self._request("GET", "/openApi/swap/v2/trade/openOrders",
                                           {"symbol": symbol}, signed=True)
            orders = existing.get("data", {}).get("orders", [])
            for o in orders:
                # КРИТИЧЕСКИЙ ФИКС: отменяем ТОЛЬКО STOP_MARKET, НЕ TAKE_PROFIT_MARKET
                if o.get("type") == "STOP_MARKET":
                    try:
                        await self._request("DELETE", "/openApi/swap/v2/trade/order",
                                            {"symbol": symbol, "orderId": o["orderId"]},
                                            signed=True)
                    except Exception:
                        pass
        except Exception:
            pass

    async def place_stop_loss(self, symbol: str, side: str, quantity: str,
                               stop_price: str) -> Dict[str, Any]:
        """
        Place a STOP_MARKET order for stop loss (one-way mode = positionSide BOTH).
        """
        # Cancel existing stops first to avoid duplicate orders
        await self.cancel_existing_stops(symbol)
        return await self._request("POST", "/openApi/swap/v2/trade/order", {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "quantity": quantity,
            "stopPrice": stop_price,
            "positionSide": "BOTH",
        }, signed=True)


    async def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Получить текущую цену для конкретного символа."""
        result = await self._request("GET", "/openApi/swap/v2/quote/ticker", {"symbol": symbol})
        if result.get("code") != 0:
            return {}
        data = result.get("data", {})
        # BingX returns dict for single symbol, not list
        if isinstance(data, dict):
            return data
        # Fallback: find matching symbol in list
        if isinstance(data, list):
            for t in data:
                if t.get("symbol") == symbol:
                    return t
            return data[0] if data else {}
        return {}

    async def get_klines(self, symbol: str, interval: str = "1m", limit: int = 100) -> List[Dict[str, Any]]:
        """Получить свечи."""
        result = await self._request(
            "GET", "/openApi/swap/v2/quote/klines",
            {"symbol": symbol, "interval": interval, "limit": str(limit)},
        )
        if result.get("code") != 0:
            return []
        raw = result.get("data", [])
        candles = []
        for c in raw:
            if isinstance(c, dict):
                candles.append({
                    "time": int(c.get("time", 0)) / 1000,
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                    "volume": float(c.get("volume", 0)),
                })
            elif isinstance(c, list) and len(c) >= 6:
                candles.append({
                    "time": int(c[0]) / 1000 if len(str(c[0])) > 10 else int(c[0]),
                    "open": float(c[1]), "high": float(c[2]),
                    "low": float(c[3]), "close": float(c[4]), "volume": float(c[5]),
                })
        return candles

    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
