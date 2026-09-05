#!/usr/bin/env python3
"""
bingx_universe.py — динамический список символов для BingX контура.
Сканирует все USDT перпетуалы BingX, фильтрует по ликвидности.

BingX: GET /openApi/swap/v2/quote/contracts → полный список инструментов
с tradeMinQuantity, quantityPrecision, status, apiStateOpen.
"""
import logging
import os
import sys
from typing import List, Dict, Tuple

for _p in ("/root/trading_brain_v4",):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load BingX credentials
_ENV_PATH = "/opt/ubot_bingx/.env"
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                if _k.strip() and not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()

log = logging.getLogger("bingx_universe")

# Базовые символы всегда в списке (BTC/ETH мажоры)
FALLBACK_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
                    "BNBUSDT", "LINKUSDT", "ADAUSDT", "AVAXUSDT", "LTCUSDT"]

# Минимальный 24h объём в USDT для включения (ликвидность)
MIN_QUOTE_VOLUME = 500000  # $500K


def _quote_volume_usdt(sym: dict) -> float:
    """Оценка ликвидности по полям контракта (если есть)."""
    # contracts endpoint может не давать volume — используем tradeMinUSDT как прокси
    try:
        return float(sym.get("tradeMinUSDT", 0) or 0) * 100000  # proxy
    except Exception:
        return 0


def fetch_universe(max_symbols: int = 50) -> Tuple[List[Dict], Dict[str, int]]:
    """Получить список торгуемых BingX перпетуалов, отсортированный по ликвидности.

    Returns: (symbols, counts)
      symbols: [{"symbol": "BTCUSDT", ...}]
    """
    counts = {"total_instruments": 0, "non_usdt": 0, "closed": 0, "selected": 0}

    try:
        from exchange.bingx import client as _bx_client
        c = _bx_client.BingXClient()
        contracts = c.get_symbols()
        c.close()
    except Exception as e:
        log.error(f"fetch_universe error: {e}")
        return [{"symbol": s} for s in FALLBACK_SYMBOLS], counts

    all_symbols = contracts if isinstance(contracts, list) else []
    counts["total_instruments"] = len(all_symbols)

    candidates = []
    for sym in all_symbols:
        if not isinstance(sym, dict):
            continue
        name = sym.get("symbol", "")
        if not name.endswith("USDT"):
            counts["non_usdt"] += 1
            continue
        # apiStateOpen = можно торговать через API
        if not sym.get("apiStateOpen", True):
            counts["closed"] += 1
            continue
        candidates.append({
            "symbol": name.replace("-", ""),  # BTC-USDT → BTCUSDT
            "display": name,
            "status": sym.get("status", 1),
            "min_qty": sym.get("tradeMinQuantity", 0),
            "min_notional": sym.get("tradeMinUSDT", 0),
            "precision": sym.get("quantityPrecision", 0),
        })

    # Sort: BTC/ETH first, then by min_notional (proxy for liquidity)
    def sort_key(c):
        if c["symbol"] in ("BTCUSDT", "ETHUSDT"):
            return (0, 0)
        if c["symbol"] in ("SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT"):
            return (1, 0)
        return (2, -float(c.get("min_notional", 0) or 0))

    candidates.sort(key=sort_key)
    selected = candidates[:max_symbols]
    counts["selected"] = len(selected)

    log.info(f"BingX universe: {counts['total_instruments']} total → "
             f"{counts['selected']} selected (non_usdt={counts['non_usdt']}, closed={counts['closed']})")
    return selected, counts


def format_universe_report(symbols: List[Dict], counts: Dict) -> str:
    lines = [
        "=== BingX Universe ===",
        f"Total instruments: {counts.get('total_instruments', 0)}",
        f"Selected: {counts.get('selected', 0)}",
        f"Symbols: {', '.join(s['symbol'] for s in symbols[:20])}...",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    syms, counts = fetch_universe()
    print(format_universe_report(syms, counts))
    print(f"Top 10: {[s['symbol'] for s in syms[:10]]}")