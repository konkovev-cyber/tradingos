#!/usr/bin/env python3
"""bybit_client.py — thin Bybit v5 REST client for the H2 shadow engine.

Auth pattern copied from /root/tradingos/strategies/bybit_position_check.py
(HMAC-SHA256 over ts+key+recv_window+query, X-BAPI-* headers).
Credentials are read from the SAME isolated .env the live contour uses
(/root/trading_brain_v4/research/execution/.env). NEVER hardcode keys.
"""
import os, time, hmac, hashlib, json
from urllib.parse import urlencode
import httpx

ENV_PATH = "/root/trading_brain_v4/research/execution/.env"
BASE = "https://api.bybit.com"
RECV_WINDOW = "5000"
CATEGORY = "linear"


class BybitError(Exception):
    def __init__(self, ret_code, ret_msg, path=""):
        self.ret_code = ret_code
        self.ret_msg = ret_msg
        self.path = path
        super().__init__(f"{path} retCode={ret_code} retMsg={ret_msg}")


class BybitClient:
    def __init__(self, env_path=ENV_PATH, base=BASE, timeout=12):
        self._key, self._secret = self._load_credentials(env_path)
        self.base = base
        self.timeout = timeout

    @staticmethod
    def _load_credentials(env_path):
        api_key = api_secret = ""
        try:
            with open(env_path) as f:
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

    @property
    def authed(self):
        return bool(self._key and self._secret)

    # ---------- public ----------

    def _get(self, path, params=None):
        params = params or {}
        q = urlencode(sorted(params.items()))
        r = httpx.get(f"{self.base}/v5/{path}?{q}", timeout=self.timeout)
        return self._check(r.json(), path)

    def kline(self, symbol, interval="15", limit=200, start=None, end=None):
        p = {"category": CATEGORY, "symbol": symbol, "interval": interval, "limit": limit}
        if start:
            p["start"] = int(start)
        if end:
            p["end"] = int(end)
        d = self._get("market/kline", p)
        out = []
        for it in d.get("list", []) or []:
            out.append({
                "ts": int(it[0]),              # Bybit v5 returns start in MS (string)
                "o": float(it[1]), "h": float(it[2]),
                "l": float(it[3]), "c": float(it[4]),
                "v": float(it[5]),
            })
        return sorted(out, key=lambda x: x["ts"])

    def ticker(self, symbol):
        d = self._get("market/tickers", {"category": CATEGORY, "symbol": symbol})
        lst = d.get("list", []) or []
        if not lst:
            return None
        it = lst[0]
        try:
            bid = float(it.get("bid1Price") or 0)
            ask = float(it.get("ask1Price") or 0)
            last = float(it.get("lastPrice") or 0)
        except (TypeError, ValueError):
            bid = ask = last = 0.0
        mid = (bid + ask) / 2 if (bid > 0 and ask > 0) else (last if last > 0 else None)
        return {"bid": bid, "ask": ask, "last": last, "mid": mid, "mark": it.get("markPrice")}

    def instrument(self, symbol):
        d = self._get("market/instruments-info", {"category": CATEGORY, "symbol": symbol})
        lst = d.get("list", []) or []
        if not lst:
            return None
        it = lst[0]
        lot = it.get("lotSizeFilter", {})
        prc = it.get("priceFilter", {})
        return {
            "symbol": it.get("symbol"),
            "status": it.get("status"),
            "contractType": it.get("contractType"),
            "min_qty": float(lot.get("minOrderQty", 0)),
            "qty_step": float(lot.get("qtyStep", 0)),
            "min_notional": float(lot.get("minNotionalValue", 0)),
            "tick_size": float(prc.get("tickSize", 0)),
        }

    # ---------- authed ----------

    def _call(self, method, path, params=None):
        params = params or {}
        if not self.authed:
            raise BybitError(-1, "no credentials", path)
        ts = str(int(time.time() * 1000))
        q = urlencode(sorted(params.items()))
        sign = hmac.new(self._secret.encode(), f"{ts}{self._key}{RECV_WINDOW}{q}".encode(),
                        hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": self._key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-SIGN": sign,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if method == "GET":
            r = httpx.get(f"{self.base}/v5/{path}?{q}", headers=headers, timeout=self.timeout)
        else:
            r = httpx.post(f"{self.base}/v5/{path}", headers=headers, content=q,
                           timeout=self.timeout)
        return self._check(r.json(), path)

    def _check(self, data, path):
        if data.get("retCode") != 0:
            raise BybitError(data.get("retCode"), data.get("retMsg"), path)
        return data.get("result") or {}

    def wallet_usdt(self):
        d = self._call("GET", "account/wallet-balance",
                       {"accountType": "UNIFIED", "coin": "USDT"})
        for acc in d.get("list", []) or []:
            for c in acc.get("coin", []) or []:
                if c.get("coin") == "USDT":
                    return {
                        "equity": self._f(c.get("equity")),
                        "wallet": self._f(c.get("walletBalance")),
                        "available": self._f(c.get("availableToWithdraw")),
                    }
        return None

    @staticmethod
    def _f(v, default=0.0):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def positions(self, symbol=None):
        p = {"category": CATEGORY, "settleCoin": "USDT"}
        if symbol:
            p["symbol"] = symbol
        d = self._call("GET", "position/list", p)
        out = []
        for it in d.get("list", []) or []:
            try:
                size = float(it.get("size", 0))
            except (TypeError, ValueError):
                size = 0.0
            if size <= 0:
                continue
            out.append({
                "symbol": it.get("symbol"),
                "side": it.get("side"),
                "size": size,
                "avg_price": float(it.get("avgPrice", 0)),
                "unrealised_pnl": float(it.get("unrealisedPnl", 0)),
                "position_value": float(it.get("positionValue", 0)),
            })
        return out

    def open_orders(self, symbol=None, order_id=None):
        p = {"category": CATEGORY, "settleCoin": "USDT"}
        if symbol:
            p["symbol"] = symbol
        if order_id:
            p["orderId"] = order_id
        d = self._call("GET", "order/realtime", p)
        return d.get("list", []) or []

    def get_order(self, symbol, order_id):
        d = self._call("GET", "order/realtime",
                       {"category": CATEGORY, "symbol": symbol, "settleCoin": "USDT",
                        "orderId": order_id})
        lst = d.get("list", []) or []
        return lst[0] if lst else None

    def set_leverage(self, symbol, leverage):
        try:
            self._call("POST", "position/set-leverage", {
                "category": CATEGORY, "symbol": symbol,
                "buyLeverage": str(leverage), "sellLeverage": str(leverage),
                "positionIdx": "0",
            })
            return True
        except BybitError as e:
            # 110004 = already at target / no change; treat as success
            if e.ret_code in (110004, 110005, 110006, 110032):
                return True
            return False

    def create_order(self, symbol, side, order_type, qty, price=None, post_only=False,
                     reduce_only=False, client_order_id=None):
        p = {
            "category": CATEGORY, "symbol": symbol, "side": side,
            "orderType": order_type, "qty": qty, "positionIdx": "0",
            "reduceOnly": "true" if reduce_only else "false",
            "timeInForce": "PostOnly" if post_only else "GTC",
        }
        if price is not None:
            p["price"] = price
        if client_order_id:
            p["clientOrderId"] = client_order_id[:36]
        if order_type == "Market":
            p.pop("price", None)
        d = self._call("POST", "order/create", p)
        return d

    def cancel_order(self, symbol, order_id):
        return self._call("POST", "order/cancel",
                          {"category": CATEGORY, "symbol": symbol, "orderId": order_id})

    def cancel_all_symbol(self, symbol):
        try:
            return self._call("POST", "order/cancel-all",
                              {"category": CATEGORY, "symbol": symbol})
        except BybitError:
            return {}
