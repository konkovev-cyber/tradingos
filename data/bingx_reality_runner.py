#!/usr/bin/env python3
"""
bingx_reality_runner.py — полный BingX торговый контур.
Poll свечи (BingX) → FeatureVector → SignalGenerator → фильтры → исполнение (trade_executor, exchange="bingx").

Запуск:
  python3 /root/tradingos/data/bingx_reality_runner.py
  systemctl start tradingos-bingx-reality.service
"""
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# NOTE: /opt/ubot_bingx NOT in path — it has its own `exchange` package that conflicts
for _p in ("/root/trading_brain_v4", "/root/tradingos", "/root"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tradingos.data.bingx_ohlcv_poller import BingXOHLCVPoller
from tradingos.signals.signal_generator import SignalGenerator

# Telemetry: capture SignalScore via monkey-patch (same as run_observation.py)
import tradingos.signals.signal_generator as _sg_mod
_score_holder = {"score": None}
_orig_calc = _sg_mod.SignalScoringEngine.calculate_with_vectors
def _patched_calc(self, **kw):
    result = _orig_calc(self, **kw)
    _score_holder["score"] = result
    return result
_sg_mod.SignalScoringEngine.calculate_with_vectors = _patched_calc

# Load BingX credentials from .env (not in PYTHONPATH to avoid exchange conflict)
_ENV_PATH = "/opt/ubot_bingx/.env"
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                if _k.strip() and not os.environ.get(_k.strip()):
                    os.environ[_k.strip()] = _v.strip()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("bingx_reality")

# Config
POLL_INTERVAL = 60  # seconds
MODE_PATH = "/root/tradingos/operations/bingx_trading_mode.json"
CONFIDENCE_THRESHOLD = 0.48  # overridden from config below
MAX_UNIVERSE_SYMBOLS = 50
LOG_PATH = "/root/tradingos/logs/bingx_signals.jsonl"
MAX_POSITIONS = 3

# Telemetry holder for SignalGenerator scoring (same pattern as run_observation.py)
_score_holder = {"score": None}


def load_config():
    try:
        with open(MODE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"risk_per_trade": 0.15, "max_positions": 3, "max_leverage": 3, "confidence_threshold": 0.48}


async def get_open_positions():
    """Get current open positions on BingX."""
    try:
        for _p in ("/root/trading_brain_v4",):
            if _p not in sys.path:
                sys.path.insert(0, _p)
        from exchange.bingx import client as _bx_client
        BingXClient = _bx_client.BingXClient
        client = BingXClient()
        pos = client.get_positions()
        client.close()
        return [p for p in pos if float(p.get("positionAmt", 0)) != 0]
    except Exception as e:
        logger.error(f"get_positions error: {e}")
        return []


async def execute_trade(symbol, direction, entry, sl, tp, conf, score):
    """Execute directly via BingXAdapter (own config, bypass Bybit executor filters).

    KILL SWITCH (added 2026-08-24): even though this path bypasses Bybit
    executor filters, it must still respect the global kill_switch in
    trading_mode.json. Fail-closed: unreadable config → block.
    """
    try:
        with open("/root/tradingos/operations/trading_mode.json") as _f:
            _cfg = json.load(_f)
        if _cfg.get("kill_switch", False):
            logger.warning(f"🛑 BLOCKED kill_switch: {symbol} — manual kill_switch ON")
            return {"status": "BLOCKED", "error": "kill_switch ON (manual halt)"}
    except Exception as e:
        logger.error(f"🛑 BLOCKED kill_switch: config read failed: {e} — FAIL-CLOSED")
        return {"status": "BLOCKED", "error": f"kill_switch config unreadable: {e}"}

    try:
        import sys as _sys
        for _p in ("/root/trading_brain_v4",):
            if _p not in _sys.path:
                _sys.path.insert(0, _p)
        from exchange.bingx import adapter as _bx_adapter

        # Load BingX config
        config = load_config()
        leverage = config.get("max_leverage", 3)

        a = _bx_adapter.BingXAdapter()
        await a.initialize()

        # Set leverage
        await a.set_leverage(symbol, leverage)

        # Compute quantity — conservative for small account: use max notional
        # $10 account, max 3x lev → max position value ~$20
        price = entry
        max_notional = 15.0  # $15 notional (safe for $10 with margin)
        qty = max_notional / price if price > 0 else 0
        # Round down to 4 decimals
        qty = round(qty, 4)
        if qty <= 0:
            a.close()
            return {"status": "ERROR", "error": f"qty={qty} too small"}

        logger.info(f"EXECUTING: {symbol} {direction} qty={qty} lev={leverage}x SL={sl} TP={tp}")

        order = await a.create_order(
            symbol=symbol,
            side=direction,
            quantity=qty,
            order_type="Market",
            leverage=leverage,
        )
        a.close()

        # Check fill — position may open even if data.order is empty (BingX quirk)
        order_data = order.get("data", {}).get("order", {})
        status = order_data.get("status", "UNKNOWN")
        logger.info(f"ORDER: {order.get('code')} status={status}")

        if order.get("code") == 0 or order_data:
            # FIX 2026-08-03: ALWAYS set SL/TP after open, even if data.order empty.
            # Previously BNB position opened but (empty data.order) → SL/TP never set → naked position.
            a2 = _bx_adapter.BingXAdapter()
            await a2.initialize()
            close_side = "Sell" if direction.upper() == "BUY" else "Buy"
            try:
                ok = a2.set_trading_stop(symbol, close_side, sl, tp)
                logger.info(f"SL/TP set: {ok}")
            except Exception as e:
                logger.error(f"SL/TP set failed: {e}")
                ok = False
            a2.close()
            return {"status": "FILLED", "order": order_data}
        else:
            return {"status": "ERROR", "error": f"Order failed: {order.get('msg','')}"}
    except Exception as e:
        logger.error(f"execute_trade error: {e}")
        return {"status": "ERROR", "error": str(e)}


def log_signal(symbol, direction, conf, score, entry, sl, tp, status):
    entry_log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "direction": direction,
        "confidence": round(conf, 4),
        "score": score,
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "status": status,
    }
    try:
        Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry_log) + "\n")
    except Exception:
        pass


async def run_loop():
    config = load_config()
    conf_threshold = config.get("confidence_threshold", CONFIDENCE_THRESHOLD)
    max_positions = config.get("max_positions", 3)
    logger.info(f"=== BingX Reality RUNNER starting ===")

    # Load dynamic universe (all tradeable BingX perps, top 50 by liquidity)
    from tradingos.data.bingx_universe import fetch_universe
    universe, counts = await asyncio.to_thread(fetch_universe, MAX_UNIVERSE_SYMBOLS)
    SYMBOLS = [u["symbol"] for u in universe]
    logger.info(f"Universe: {len(SYMBOLS)} symbols ({counts.get('total_instruments', 0)} total)")
    logger.info(f"Symbols: {', '.join(SYMBOLS[:15])}...")
    logger.info(f"Confidence threshold: {conf_threshold}")
    logger.info(f"Poll interval: {POLL_INTERVAL}s")

    poller = BingXOHLCVPoller(symbols=SYMBOLS)
    generators = {}

    cycle = 0
    while True:
        try:
            cycle += 1
            open_pos = await get_open_positions()
            open_syms = {p.get("symbol", "") for p in open_pos}
            logger.info(f"--- Cycle {cycle}: {len(open_pos)} open positions {open_syms} ---")

            for symbol in SYMBOLS:
                # Skip symbols with open positions (one position per symbol)
                bingx_sym = symbol.replace("USDT", "-USDT")
                if bingx_sym in open_syms:
                    continue

                # 1. Fetch candles
                candles = await poller._fetch_kline(symbol)
                if not candles or len(candles) < 50:
                    continue

                # 2. Build feature vector
                poller.feature_store.add_candles(symbol, "1m", candles)
                poller.feature_store.recalculate(symbol)
                fv = poller._candle_to_feature_vector(symbol)
                if fv is None:
                    continue

                # 3. Generate signal
                if symbol not in generators:
                    generators[symbol] = SignalGenerator()
                sg = generators[symbol]
                _score_holder["score"] = None
                direction = sg.decide(symbol, fv, bar_idx=0)

                score = _score_holder.get("score")
                conf = getattr(score, "final_probability", 0) if score else 0
                total_score = getattr(score, "total_score", 0) if score else 0

                if direction is None or conf < conf_threshold:
                    log_signal(symbol, direction, conf, total_score, fv.close, 0, 0, "rejected_low_conf")
                    continue

                # 4. Compute SL/TP from ATR
                atr = fv.atr if fv.atr and fv.atr > 0 else fv.close * 0.005
                sl = fv.close - 2 * atr if direction == "BUY" else fv.close + 2 * atr
                tp = fv.close + 3 * atr if direction == "BUY" else fv.close - 3 * atr

                # 5. Check max positions
                if len(open_pos) >= max_positions:
                    log_signal(symbol, direction, conf, total_score, fv.close, sl, tp, "max_positions")
                    continue

                logger.info(f"🎯 SIGNAL: {symbol} {direction} conf={conf:.3f} score={total_score} entry={fv.close:.6f} SL={sl:.6f} TP={tp:.6f}")

                # 6. Execute
                result = await execute_trade(symbol, direction, fv.close, sl, tp, conf, total_score)
                status = result.get("status", "UNKNOWN") if isinstance(result, dict) else "UNKNOWN"
                log_signal(symbol, direction, conf, total_score, fv.close, sl, tp, status)
                if status == "FILLED":
                    logger.info(f"✅ FILLED: {symbol} {direction}")
                else:
                    logger.error(f"❌ REJECTED: {symbol} {direction} → {result}")

                # Refresh open positions after execution
                open_pos = await get_open_positions()
                open_syms = {p.get("symbol", "") for p in open_pos}

        except Exception as e:
            logger.error(f"Cycle error: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_loop())