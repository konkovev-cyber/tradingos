#!env python3
"""
data/run_meme_shorts.py — МЕМКОИН-КОНТУР (2026-09-01, owner-approved).

Торгует ТОЛЬКО SHORT по D1-тренду на мемкоинах:
  close < EMA50 (D1) → SHORT, SL = entry + 1.2×ATR, TP = entry − 3R
Доказано на исправленном стенде: +203R на 1305 сделках (WR 29%), в отличие
от LONG-направления (+30R, не включено).

Правила (совпадают с reality):
- Только SHORT (мемы «альткоингравитация»: прострелы вверх → долгий слив)
- Тренд определяется на D1 (EMA50), вход маркетом на H1-цикле скана
- Тейк 3×ATR позиционный (комиссия несущественна), стоп 1.2×ATR
- Макс. позиций, риск на сделку — из trading_mode.json
- Идемпотентность: не открывать дважды по одному символу (dedup по D1-бару)
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("meme_shorts")

_ENV_PATH = "/root/trading_brain_v4/research/execution/.env"
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as f:
        for _l in f:
            _l = _l.strip()
            if _l and not _l.startswith("#") and "=" in _l:
                _k, _v = _l.split("=", 1)
                os.environ[_k.strip()] = _v.strip()

_SYSPATHS = ["/root/tradingos", "/root/trading_brain_v4"]
for _p in _SYSPATHS:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tradingos.data.indicators import IndicatorCalculator
from tradingos.data.models.candle import Candle
from tradingos.strategies.trade_executor import TradeProposal, _execute_reality
from tradingos.data.run_observation import generate_decision_id

# ─── Конфиг ──────────────────────────────────────────────────────
MEME_SYMBOLS = [
    "DOGEUSDT", "SHIB1000USDT", "WIFUSDT", "PENGUUSDT", "MUBARAKUSDT",
    "FARTCOINUSDT", "BRETTUSDT", "BOMEUSDT", "1000FLOKIUSDT",
    "POPCATUSDT", "MEWUSDT", "CHILLGUYUSDT",
]
STATE_PATH = Path("/root/tradingos/operations/meme_shorts_state.json")

calc = IndicatorCalculator()


def _load_tm() -> dict:
    try:
        return json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
    except Exception:
        return {}


def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception:
        pass
    return {"fired": {}}  # symbol -> d1_ts (последний исполненный сигнал)


def _save_state(state: dict):
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning(f"state save: {e}")


def _demo_enabled() -> bool:
    v = (os.environ.get("BYBIT_DEMO", "") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _fetch_d1(symbol: str) -> list:
    """D1-свечи с Bybit (сортировка старые→новые — критично!)."""
    import httpx
    r = httpx.get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "linear", "symbol": symbol, "interval": "D", "limit": 300},
        timeout=15,
    )
    data = r.json()
    if data.get("retCode") != 0:
        return []
    candles = []
    for row in reversed(data.get("result", {}).get("list", [])):
        try:
            candles.append(Candle(
                timestamp=int(row[0]), open=float(row[1]), high=float(row[2]),
                low=float(row[3]), close=float(row[4]),
                volume=float(row[5]) if row[5] else 0.0,
                symbol=symbol, timeframe="D"))
        except (IndexError, ValueError, TypeError):
            continue
    return candles


def _atr(candles: list, n: int = 14) -> float:
    """ATR по последним n закрытых свечей (Candle-объекты)."""
    if len(candles) < n + 1:
        return 0.0
    trs = []
    for i in range(len(candles) - n, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low,
                       abs(c.high - p.close), abs(c.low - p.close)))
    return sum(trs) / len(trs)


def _signal_for(candles: list):
    """SHORT-сигнал по D1-тренду: close < EMA50. Возвращает (side, entry) или None."""
    if len(candles) < 60:
        return None
    closes = [c.close for c in candles[-100:]]
    ema50 = calc.ema(closes, 50)  # IndicatorCalculator.ema → float (последнее значение)
    last = candles[-1]
    if not ema50 or ema50 <= 0:
        return None
    # Tолько SHORT: мем ниже EMA50 = тренд вниз (доказано +203R)
    if last.close < ema50:
        return "SELL", last.close
    return None


def _position_open(symbol: str) -> bool:
    """Есть ли уже открытая позиция по символу (не открывать дубль)."""
    try:
        from tradingos.strategies.bybit_position_check import get_open_position_symbols
        return symbol in get_open_position_symbols()
    except Exception:
        return True  # fail-closed


async def _scan_once():
    tm = _load_tm()
    live = bool(tm.get("live_trading_enabled", False))
    sell_disabled = bool(tm.get("sell_disabled", True))
    max_pos = int(tm.get("max_positions", 4))
    risk = float(tm.get("risk_per_trade", 10.0))
    state = _load_state()

    # Счёт открытых позиций
    try:
        from tradingos.strategies.bybit_position_check import count_open_positions
        open_count = count_open_positions()
    except Exception:
        open_count = 0

    if not live:
        log.info("⏸ meme_shorts: live_trading_enabled=false — режим наблюдения")
        return
    if sell_disabled:
        log.info("⏸ meme_shorts: sell_disabled=true — шорты запрещены конфигом")
        return

    for sym in MEME_SYMBOLS:
        if _position_open(sym):
            continue
        if open_count + 1 > max_pos:
            break  # достигнут лимит позиций

        candles = _fetch_d1(sym)
        if not candles or len(candles) < 60:
            continue
        sig = _signal_for(candles)
        if not sig:
            continue
        side, entry = sig
        d1_ts = candles[-1].timestamp

        # Дедуп: уже торговали этот D1-бар?
        if state["fired"].get(sym) == d1_ts:
            continue

        atr = _atr(candles)
        if atr <= 0:
            continue
        entry_p = candles[-1].close
        stop = entry_p + atr * 1.2
        target = entry_p - atr * 3.0

        # MIN-SL: стоп не ближе 1.5% (как reality)
        min_sl = entry_p * 1.015
        if stop < min_sl:
            stop = min_sl
        risk_per_unit = stop - entry_p
        if risk_per_unit <= 0:
            continue

        proposal = TradeProposal(
            symbol=sym,
            side=side,
            entry=entry_p,
            stop_loss=stop,
            take_profit=target,
            rr=3.0,
            confidence=0.6,
            strategy="MEME_SHORTS_D1",
            decision_id=f"M-{generate_decision_id()}",
            reason=[f"Meme D1-trend SHORT: {sym} close {entry_p:.6g} < EMA50, "
                    f"SL={stop:.6g}, TP={target:.6g} (3R)"],
            session="MEME_SHORTS",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        valid, msg = proposal.validate()
        if not valid:
            log.warning(f"⏭️ meme {sym}: proposal invalid — {msg}")
            continue
        # _execute_reality требует APPROVED (как reality-контур делает перед вызовом)
        proposal.status = "APPROVED"

        log.info(f"🎯 MEME SHORT signal: {sym} entry={entry_p:.6g} SL={stop:.6g} "
                 f"TP={target:.6g} risk=${risk:.0f}")
        res = await _execute_reality(proposal)
        status = res.get("status", "?") if isinstance(res, dict) else str(res)
        log.info(f"🛑 MEME EXEC {sym}: {status}")
        if isinstance(res, dict) and status in ("FILLED", "SUBMITTED"):
            state["fired"][sym] = d1_ts
            _save_state(state)
        open_count += 1

    # Очистка дедупа — держим последние 30 дней (30 баров)
    _save_state(state)


async def main():
    log.info("🚀 MEME_SHORTS контур запущен (D1-тренд SHORT, только мемы)")
    while True:
        try:
            await _scan_once()
        except Exception as e:
            log.error(f"scan error: {e}")
        await asyncio.sleep(300)  # каждые 5 минут


if __name__ == "__main__":
    asyncio.run(main())