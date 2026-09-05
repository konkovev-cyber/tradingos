"""
tradingos/data/reality_universe.py
Dynamic Reality Universe v1.1 — Market Discovery Engine.
Scans all Bybit USDT perpetual instruments, filters by Reality Pilot constraints.
No hardcoded whitelists. No manual pair selection. Exchange is the source of truth.
"""
import httpx
import re
from typing import List, Dict, Tuple

RISK_BUDGET = 0.25
MIN_VOLUME_24H = 5_000   # lowered from 50k — enables more tradeable altcoins
MIN_NOTIONAL = 5

# Symbols always excluded
BLOCKED_PATTERNS = [
    r"^USD[0-9]*USDT$",         # USD1USDT, USD2USDT — stablecoins
    r"^USDCUSDT$",              # USDCUSDT — stablecoin
    r"^USDEUSDT$",              # USDEUSDT — stablecoin
    r"^RLUSDUSDT$",             # RLUSDUSDT — stablecoin
    r"^FDUSDUSDT$",             # FDUSDUSDT — stablecoin
    r"^DAIUSDT$",               # DAIUSDT — stablecoin
    r"^TUSDUSDT$",              # TUSDUSDT — stablecoin
    r"^AMZNUSDT$",              # tokenized stock
    r"^AAPLUSDT$",              # tokenized stock
    r"^GOOGLUSDT$",             # tokenized stock
    r"^TSLAUSDT$",              # tokenized stock
    r"^NFLXUSDT$",              # tokenized stock
    r"^MSFTUSDT$",              # tokenized stock
    r"^COINUSDT$",              # tokenized stock
    r"^ETHBTCUSDT$",            # cross pair (ETH/BTC ratio)
    r"^BNBBTCUSDT$",            # cross pair
    r"^SOLBTCUSDT$",            # cross pair
]

# Fallback symbols if API is unavailable
FALLBACK_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "DOGEUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "BNBUSDT",
]


def is_blocked(symbol: str) -> bool:
    """Check if symbol is blocked (stablecoin, stock, non-standard)."""
    for pattern in BLOCKED_PATTERNS:
        if re.match(pattern, symbol):
            return True
    return False


def classify_instrument(symbol: str, symbol_type: str = "") -> str:
    """Classify a Bybit linear instrument into experiment buckets.

    symbol_type is the exchange's OWN taxonomy from /v5/market/instruments-info:
      ""           -> regular crypto perpetual (BTCUSDT, ...)
      "innovation" -> innovation-zone crypto perpetual
      "stock"      -> tokenized US equity (AAPLUSDT, NVDAUSDT, AMDSTOCKUSDT, ...)
      "commodity"  -> commodity token (XAUUSDT, XAGUSDT, BZUSDT, CLUSDT)
    Using symbol_type is future-proof: any new tokenized stock automatically
    carries symbolType=stock, no manual ticker list needed.

    Name-based BLOCKED_PATTERNS (stablecoins, cross pairs, stock names) remain
    as a fallback for when symbol_type is unavailable.

    Returns one of: "crypto_perp" | "tokenized_stock" | "commodity_token" | "blocked_type".
    LIVE MICRO experiment = crypto_perp only.
    """
    if symbol_type == "stock":
        return "tokenized_stock"
    if symbol_type == "commodity":
        return "commodity_token"
    if is_blocked(symbol):
        return "blocked_type"
    return "crypto_perp"


async def fetch_universe() -> Tuple[List[Dict], Dict[str, int]]:
    """
    Fetch and filter Bybit USDT perpetual symbols.
    Returns (universe_list, filter_counts).
    
    Filter counts keys:
      total_instruments, non_usdt, blocked_type, no_ticker,
      low_volume, zero_volatility, risk_incompatible, final
    """
    counts = {"total_instruments": 0, "non_usdt": 0, "blocked_type": 0,
              "tokenized_stocks": 0, "commodity_tokens": 0,
              "no_ticker": 0, "low_volume": 0, "zero_volatility": 0,
              "risk_incompatible": 0, "final": 0}

    try:
        resp = httpx.get("https://api.bybit.com/v5/market/instruments-info",
                         params={"category": "linear", "limit": 1000}, timeout=30)
        data = resp.json()
        if data.get("retCode") != 0:
            return fallback_universe(), counts

        all_symbols = data["result"].get("list", [])
        counts["total_instruments"] = len(all_symbols)
        inst_map = {s["symbol"]: s for s in all_symbols}

        tick_resp = httpx.get("https://api.bybit.com/v5/market/tickers",
                              params={"category": "linear"}, timeout=30)
        tick_data = tick_resp.json()
        ticker_map = {}
        if tick_data.get("retCode") == 0:
            for t in tick_data["result"].get("list", []):
                ticker_map[t["symbol"]] = t

        result = []
        seen = set()

        for s in all_symbols:
            sym = s["symbol"]
            if not sym.endswith("USDT"):
                counts["non_usdt"] += 1
                continue
            if sym in seen:
                continue
            seen.add(sym)

            # Instrument classification (exchange taxonomy): experiment = crypto only
            cls = classify_instrument(sym, s.get("symbolType", ""))
            if cls == "tokenized_stock":
                counts["tokenized_stocks"] += 1
                continue
            if cls == "commodity_token":
                counts["commodity_tokens"] += 1
                continue
            if cls == "blocked_type":
                counts["blocked_type"] += 1
                continue

            t = ticker_map.get(sym)
            inst = inst_map.get(sym)
            if not t or not inst:
                counts["no_ticker"] += 1
                continue

            lot = inst.get("lotSizeFilter", {})
            min_qty = float(lot.get("minOrderQty", 1))
            price = float(t.get("lastPrice", 0))
            high = float(t.get("highPrice24h", 0))
            low = float(t.get("lowPrice24h", 0))
            vol = float(t.get("volume24h", 0))

            if price <= 0 or vol < MIN_VOLUME_24H:
                counts["low_volume"] += 1
                continue

            # General risk estimate: use 2% of price as ATR proxy
            atr = price * 0.02
            position = RISK_BUDGET / (2 * max(atr, 0.0001))
            notional = position * price

            # Only filter clear untradeables: min_qty * price must be reachable
            if notional < MIN_NOTIONAL and sym not in FALLBACK_SYMBOLS:
                counts["risk_incompatible"] += 1
                continue

            result.append({
                "symbol": sym, "price": round(price, 8),
                "atr": round(atr, 8), "position": round(position, 2),
                "notional": round(notional, 2), "volume_24h": round(vol, 0),
                "min_qty": min_qty,
            })

        result.sort(key=lambda x: x["volume_24h"], reverse=True)
        counts["final"] = len(result)
        return result, counts

    except Exception:
        return fallback_universe(), counts


def fallback_universe() -> List[Dict]:
    return [{"symbol": s, "price": 0, "atr": 0, "position": 0,
             "notional": 0, "volume_24h": 0, "min_qty": 1}
            for s in FALLBACK_SYMBOLS]


def format_universe_report(universe: List[Dict], counts: Dict[str, int]) -> str:
    lines = [
        "\n=== REALITY MARKET DISCOVERY REPORT ===",
        f"\nExchange universe:      {counts.get('total_instruments', 0)}",
        f"Non-USDT rejected:     {counts.get('non_usdt', 0)}",
        f"Tokenized stocks:      {counts.get('tokenized_stocks', 0)}",
        f"Commodity tokens:      {counts.get('commodity_tokens', 0)}",
        f"Blocked type:          {counts.get('blocked_type', 0)}",
        f"No ticker data:        {counts.get('no_ticker', 0)}",
        f"Low volume:            {counts.get('low_volume', 0)}",
        f"Zero volatility:       {counts.get('zero_volatility', 0)}",
        f"Risk incompatible:     {counts.get('risk_incompatible', 0)}",
        f"---",
        f"Final universe:        {counts.get('final', 0)}",
    ]
    if universe:
        lines.append(f"\nTop candidates:")
        lines.append(f"{'Symbol':<12} {'Price':<12} {'Pos':<10} {'Notional':<10} {'Vol 24h':<12}")
        lines.append("-" * 56)
        for u in universe[:20]:
            lines.append(f"{u['symbol']:<12} {u['price']:<12} {u['position']:<10.1f} ${u['notional']:<8.2f} {u['volume_24h']:<12.0f}")
    return "\n".join(lines)
