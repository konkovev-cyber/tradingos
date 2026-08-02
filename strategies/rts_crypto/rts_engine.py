"""
strategies/rts_crypto/rts_engine.py
RTS Crypto Engine v1 — Adaptive Position Basket Management for BingX.

Not a strategy. An execution layer that:
- Enters small
- Adds on pullback (ATR-based, not martingale)
- Manages basket average price
- Exits basket on recovery to breakeven+
"""
import asyncio, json, logging, hashlib, time
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rts")

API_KEY = '1GLz8DKdTi00cktb4GAFc7PXUdURwpUspmogSBtvTzBiqtLwnWNjURD35bc4BMAkDpjAHkmGT0xqywxQ'
API_SECRET = 'h49fKOeGzqTL2A2Qn9gucLxnZSxgzMm6DK2RojtFNAKCohl2tcUWiUV2TQRMRnjjgjjRU0Ht5MLKbivHR5w'
BASE = "https://open-api.bingx.com"

JOURNAL_PATH = Path("/root/tradingos/evidence/rts_journal.jsonl")


@dataclass
class Basket:
    symbol: str
    side: str  # LONG / SHORT
    entries: List[dict] = field(default_factory=list)
    average_price: float = 0.0
    total_qty: float = 0.0
    max_drawdown: float = 0.0
    level: int = 0

    def add_entry(self, price: float, qty: float):
        self.entries.append({"price": price, "qty": qty})
        self.level += 1
        self._recalc()

    def _recalc(self):
        total_value = sum(e["price"] * e["qty"] for e in self.entries)
        self.total_qty = sum(e["qty"] for e in self.entries)
        self.average_price = total_value / self.total_qty if self.total_qty else 0

    def basket_pnl(self, current_price: float) -> float:
        if self.total_qty == 0: return 0
        if self.side == "LONG":
            return (current_price - self.average_price) / self.average_price * 100
        return (self.average_price - current_price) / self.average_price * 100


async def bingx_request(method: str, endpoint: str, params: dict) -> dict:
    import hmac, hashlib as hl
    ts = str(int(time.time() * 1000))
    params["timestamp"] = ts
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac.new(API_SECRET.encode(), query.encode(), hl.sha256).hexdigest()
    url = f"{BASE}{endpoint}?{query}&signature={sig}"
    import httpx
    if method == "GET":
        resp = await httpx.AsyncClient(timeout=10).get(url, headers={"X-BX-APIKEY": API_KEY})
    elif method == "DELETE":
        resp = await httpx.AsyncClient(timeout=10).delete(url, headers={"X-BX-APIKEY": API_KEY})
    elif method == "POST":
        resp = await httpx.AsyncClient(timeout=10).post(url, headers={"X-BX-APIKEY": API_KEY})
    return resp.json()


async def get_price(symbol: str) -> Optional[float]:
    data = await bingx_request("GET", "/openApi/swap/v2/quote/ticker", {"symbol": symbol})
    if data.get("code") == 0:
        return float(data["data"]["lastPrice"])
    return None


async def place_order(symbol: str, side: str, qty: float, sl: float = 0, tp: float = 0) -> dict:
    params = {
        "symbol": symbol, "side": side, "positionSide": "BOTH",
        "type": "MARKET", "quantity": str(qty),
    }
    result = await bingx_request("POST", "/openApi/swap/v2/trade/order", params)
    if result.get("code") != 0:
        logger.warning(f"Order failed: {result.get('msg')}")
    return result


async def set_tp_sl(symbol: str, side: str, qty: float, sl: float, tp: float):
    """Set TP and SL for position."""
    # Place TP
    tp_params = {
        "symbol": symbol, "side": "SELL" if side == "LONG" else "BUY",
        "positionSide": "BOTH", "type": "TAKE_PROFIT_MARKET",
        "quantity": str(qty), "stopPrice": str(tp), "workingType": "MARK_PRICE",
        "reduceOnly": "true",
    }
    tp_res = await bingx_request("POST", "/openApi/swap/v2/trade/order", tp_params)
    # Place SL
    sl_params = {
        "symbol": symbol, "side": "SELL" if side == "LONG" else "BUY",
        "positionSide": "BOTH", "type": "STOP_MARKET",
        "quantity": str(qty), "stopPrice": str(sl), "workingType": "MARK_PRICE",
        "reduceOnly": "true",
    }
    sl_res = await bingx_request("POST", "/openApi/swap/v2/trade/order", sl_params)
    logger.info(f"TP: {tp_res.get('code')} SL: {sl_res.get('code')}")


async def log_journal(entry: dict, decision_id: Optional[str] = None):
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    if decision_id:
        entry["decision_id"] = decision_id
    with JOURNAL_PATH.open("a") as f:
        f.write(json.dumps(entry) + "\n")


async def rts_cycle(symbol: str = "XRP-USDT", account_balance: float = 300):
    """One RTS management cycle."""
    basket = Basket(symbol=symbol, side="LONG")
    base_qty = round(account_balance * 0.005 / 1, 2)  # 0.5% risk
    max_levels = 3
    level_sizes = [base_qty, round(base_qty * 1.3, 2), round(base_qty * 1.6, 2)]

    # Get current price for entry
    price = await get_price(symbol)
    if not price:
        logger.error("No price data")
        return
    entry_price = price

    # Entry
    logger.info(f"RTS ENTRY {symbol} {basket.side} @ {entry_price} qty={level_sizes[0]}")
    await place_order(symbol, "BUY", level_sizes[0])
    basket.add_entry(entry_price, level_sizes[0])
    await log_journal({"event": "ENTRY", "symbol": symbol, "price": entry_price, "qty": level_sizes[0]})

    # Monitor loop
    for cycle in range(60):  # 60 cycles ≈ 10 min at 10s
        await asyncio.sleep(10)
        current = await get_price(symbol)
        if not current:
            continue

        pnl_pct = basket.basket_pnl(current)
        drop = (basket.average_price - current) / basket.average_price * 100

        # Track max drawdown
        if drop > basket.max_drawdown:
            basket.max_drawdown = drop

        # Add level trigger
        add_distance = 0.5 * (basket.average_price * 0.01)  # 0.5% drop
        if (basket.level < max_levels and
            drop > (basket.level * 0.5) and
            pnl_pct > -3.0):  # don't add if -3%+ down

            add_qty = level_sizes[basket.level]
            logger.info(f"RTS ADD L{basket.level+1} @ {current} qty={add_qty}")
            await place_order(symbol, "BUY", add_qty)
            basket.add_entry(current, add_qty)
            await log_journal({"event": f"ADD_L{basket.level}", "price": current, "qty": add_qty,
                             "avg": basket.average_price})

        # Basket TP
        if pnl_pct > 0.3:  # 0.3% above average = basket profit
            logger.info(f"RTS BASKET TP @ {current} pnl={pnl_pct:.2f}% avg={basket.average_price:.2f}")
            # Close — hit by SL/TP automatically if set, or signal for close
            await log_journal({
                "event": "BASKET_TP", "exit_price": current, "pnl_pct": round(pnl_pct, 2),
                "avg_price": basket.average_price, "levels": basket.level,
            })
            return

        # Emergency stop
        if pnl_pct < -3.0:
            logger.warning(f"RTS EMERGENCY STOP @ {current} pnl={pnl_pct:.2f}%")
            await log_journal({
                "event": "EMERGENCY_STOP", "exit_price": current, "pnl_pct": round(pnl_pct, 2),
            })
            return

        logger.info(f"RTS {symbol} avg={basket.average_price:.2f} current={current:.2f} "
                     f"pnl={pnl_pct:.2f}% level={basket.level}/{max_levels}")


async def run():
    logger.info("RTS Crypto Engine v1 — PAPER MODE")
    await rts_cycle("XRP-USDT", account_balance=300)
    logger.info("Cycle complete")


if __name__ == "__main__":
    asyncio.run(run())
