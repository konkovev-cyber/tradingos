"""
control_plane/bingx_read/client.py
BingX Read-Only Adapter — ЕДИНСТВЕННЫЙ источник истины по BingX аккаунту.

READ-ONLY. HMAC signed. No POST/PUT/DELETE. Изолирован от LIVE бота.
Источник ключей: /opt/ubot_bingx/.env (ТОЛЬКО этот файл, не MT5, не другой).

Проверка: userId = 1586188652750692355 (BingX)
"""
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
import logging
logger = logging.getLogger("bingx_read")

BINGX_API_BASE = "https://open-api.bingx.com"
ENV_PATH = Path("/opt/ubot_bingx/.env")  # ЕДИНСТВЕННЫЙ источник ключей
KNOWN_USER_ID = "1586188652750692355"  # BingX userId для верификации


def _load_credentials() -> tuple:
    """Загружает ключи ТОЛЬКО из /opt/ubot_bingx/.env."""
    api_key = os.getenv("BINGX_API_KEY", "")
    api_secret = os.getenv("BINGX_API_SECRET", "")
    if not api_key or not api_secret:
        if ENV_PATH.exists():
            for line in ENV_PATH.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k == "BINGX_API_KEY":
                    api_key = v
                elif k == "BINGX_API_SECRET":
                    api_secret = v
    if not api_key or not api_secret:
        raise ValueError("BingX API keys not found in /opt/ubot_bingx/.env")
    return api_key, api_secret


def _sign(secret: str, params: dict) -> str:
    """BingX HMAC-SHA256 signature."""
    query = urllib.parse.urlencode(sorted(params.items()))
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _signed_url(path: str, params: dict = None) -> tuple:
    """Build fully signed URL + headers for BingX private endpoint."""
    key, secret = _load_credentials()
    params = dict(params or {})
    params["timestamp"] = str(int(time.time() * 1000))
    params["signature"] = _sign(secret, params)
    query = urllib.parse.urlencode(params)
    return f"{BINGX_API_BASE}{path}?{query}", {"X-BX-APIKEY": key}


class BingXReadClient:
    """
    ЕДИНСТВЕННЫЙ клиент для чтения реального BingX аккаунта.
    Загружает ключи из /opt/ubot_bingx/.env — всегда.
    """

    def __init__(self):
        self._api_key, self._api_secret = _load_credentials()
        self._verified = False

    def verify(self, data: dict) -> bool:
        """Проверяет identity аккаунта после первого запроса."""
        if self._verified:
            return True
        uid = data.get("userId", "") if data else ""
        if uid:
            if uid != KNOWN_USER_ID:
                logger.warning(
                    f"BingX userId={uid} не совпадает с известным {KNOWN_USER_ID}. "
                    f"Возможно, ключи от другого аккаунта."
                )
            else:
                self._verified = True
        return self._verified

    async def _get(self, path: str, params: dict = None) -> Optional[dict]:
        """Signed GET запрос к BingX private endpoint."""
        url, headers = _signed_url(path, params or {})
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        logger.error(f"BingX GET {path} status={resp.status}")
                        return None
                    data = await resp.json()
                    if data.get("code") != 0:
                        logger.error(f"BingX GET {path} error={data}")
                        return None
                    return data.get("data")
            except Exception as e:
                logger.error(f"BingX GET {path} failed: {e}")
                return None

    async def get_positions(self) -> List[dict]:
        """Читает открытые позиции с BingX."""
        data = await self._get("/openApi/swap/v2/user/positions")
        if not data:
            return []
        positions = []
        for p in data if isinstance(data, list) else [data]:
            amt = float(p.get("positionAmt", 0) or 0)
            if abs(amt) < 0.0001:
                continue
            positions.append({
                "symbol": p.get("symbol", ""),
                "position_id": p.get("positionId", ""),
                "side": "LONG" if p.get("positionSide") == "LONG" else "SHORT",
                "entry_price": float(p.get("avgPrice", 0) or 0),
                "mark_price": float(p.get("markPrice", 0) or 0),
                "qty": abs(amt),
                "unrealized_pnl": float(p.get("unrealizedProfit", 0) or 0),
                "pnl_ratio": float(p.get("pnlRatio", 0) or 0),
                "leverage": int(p.get("leverage", 1) or 1),
                "stop_loss": float(p.get("stopLoss", 0) or 0) or None,
                "take_profit": float(p.get("takeProfit", 0) or 0) or None,
            })
        return positions

    async def get_balance(self) -> Optional[dict]:
        """Читает баланс аккаунта с BingX."""
        data = await self._get("/openApi/swap/v2/user/balance")
        if not data:
            return None
        d = data[0] if isinstance(data, list) and data else data
        bal = d.get("balance", {}) if isinstance(d.get("balance"), dict) else d
        return {
            "equity": float(bal.get("equity", 0) or 0),
            "wallet": float(bal.get("balance", 0) or 0),
            "available": float(bal.get("availableMargin", 0) or 0),
            "used_margin": float(bal.get("usedMargin", 0) or 0),
            "unrealized_pnl": float(bal.get("unrealizedProfit", 0) or 0),
            "realized_pnl": float(bal.get("realisedProfit", 0) or 0),
            "userId": bal.get("userId", ""),
        }

    async def close(self):
        pass
