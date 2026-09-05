#!/usr/bin/env python3
"""
Universe Expansion Scan — сколько качественных кандидатов остаётся
ЗА ПРЕДЕЛАМИ текущего наблюдаемого юниверса (оконный замер, 72ч).

Вопрос: если расширить universe с ~34 до ~120 ликвидных криптомонет,
насколько вырастет поток кандидатов? Решение по данным, не по ожиданиям.

Метод (тот же пайплайн, что в run_observation):
- текущий наблюдаемый набор: символы с записями в signal_log за последние 30 мин;
- полный крипто-юниверс по объёму 24h (symbolType==""|"innovation");
- для каждого символа: 1h-свечи (~400) → для каждого бара последних 72ч:
  FeatureVector → SignalGenerator.decide → пороги кандидата (prob>=0.55,
  quality MEDIUM+, score>=55) → ADX>=20 → tradeable_qty при risk=$0.25
  (реальный ATR + lotSizeFilter + notional cap 20%);
- агрегация по группам: current vs additional — кандидат-бары, пробеги,
  уникальные символы, торгуемость.

НЕ меняет контур. Выход: печать + reports/universe_expansion_scan.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.WARNING)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))

from tradingos.data.models.candle import Candle
from tradingos.data.indicators import IndicatorCalculator
from tradingos.data.reality_universe import classify_instrument
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator
import tradingos.signals.signal_scoring as _ss

_score_holder = {"score": None}
_orig_calc = _ss.SignalScoringEngine.calculate_with_vectors
def _patched(self, **kw):
    r = _orig_calc(self, **kw)
    _score_holder["score"] = r
    return r
_ss.SignalScoringEngine.calculate_with_vectors = _patched

SIGNAL_LOG = Path("/root/tradingos/memory/signal_log.jsonl")
REPORT = Path("/root/tradingos/reports/universe_expansion_scan.json")

PROB_MIN, SCORE_MIN, ADX_MIN = 0.55, 55, 20
QUALITY_OK = ("MEDIUM", "GOOD", "EXCELLENT")
EXPAND_TO = 120
WINDOW_HOURS = 72
RISK = 0.25
NEED_HISTORY = 200   # баров истории для полного индикаторного набора


def current_observed_symbols() -> set[str]:
    """Символы с записями в signal_log за последние 30 минут."""
    now = datetime.now(timezone.utc).timestamp()
    syms = set()
    if not SIGNAL_LOG.exists():
        return syms
    with SIGNAL_LOG.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            try:
                ts = datetime.fromisoformat(r["timestamp"]).timestamp()
            except Exception:
                continue
            if now - ts <= 1800:
                syms.add(r.get("symbol", ""))
    return syms


async def fetch_klines(symbol: str) -> list[Candle]:
    """Fetch NEED_HISTORY + WINDOW_HOURS 1h candles (paginated)."""
    import httpx

    need = NEED_HISTORY + WINDOW_HOURS
    all_c: list[Candle] = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    async with httpx.AsyncClient(timeout=15) as client:
        while len(all_c) < need:
            params = {"category": "linear", "symbol": symbol,
                      "interval": "60", "limit": 200}
            if end_ms:
                params["end"] = str(end_ms)
            resp = await client.get("https://api.bybit.com/v5/market/kline", params=params)
            data = resp.json()
            rows = data.get("result", {}).get("list", [])
            if not rows:
                break
            for row in rows:
                all_c.append(Candle(
                    timestamp=int(row[0]), open=float(row[1]), high=float(row[2]),
                    low=float(row[3]), close=float(row[4]),
                    volume=float(row[5]) if row[5] else 0.0,
                    symbol=symbol, timeframe="1h",
                ))
            end_ms = int(rows[-1][0]) - 1
            await asyncio.sleep(0.05)
    all_c.sort(key=lambda x: x.timestamp)
    return all_c


def build_fv(symbol: str, candles: list[Candle], calc: IndicatorCalculator) -> FeatureVector:
    last = candles[-1]
    prices = [c.close for c in candles if c.close > 0]
    n = len(prices)
    ema20 = calc.ema(prices, 20) if n >= 20 else 0.0
    ema50 = calc.ema(prices, 50) if n >= 50 else 0.0
    ema200 = calc.ema(prices, 200) if n >= 200 else 0.0
    rsi = calc.rsi(prices, 14) if n >= 14 else 50.0
    atr = calc.atr(candles, 14) if len(candles) >= 14 else 0.0
    adx = calc.adx(candles, 14) if len(candles) >= 14 else 0.0
    macd = calc.macd(prices) if n >= 26 else {}
    bb = calc.bollinger(prices) if n >= 20 else {}
    vwap = calc.vwap(candles) if candles else 0.0
    vol_ratio = calc.volume_ratio([c.volume for c in candles], 20) if len(candles) >= 20 else 1.0
    return FeatureVector(
        timestamp_ms=last.timestamp, symbol=symbol,
        open=last.open, high=last.high, low=last.low, close=last.close,
        volume=last.volume,
        ema20=ema20, ema50=ema50, ema200=ema200 or 0.0,
        rsi=rsi, macd_line=macd.get("macd", 0.0), macd_signal=macd.get("signal", 0.0),
        atr=atr, bb_upper=bb.get("upper", 0.0), bb_lower=bb.get("lower", 0.0),
        bb_middle=bb.get("middle", 0.0), adx=adx,
        volume_ma=0.0, volume_ratio=vol_ratio, obv=0.0, vwap=vwap,
        ema_bullish=ema20 > ema50 if ema20 > 0 and ema50 > 0 else False,
        price_above_ema50=last.close > ema50 if ema50 > 0 else False,
        rsi_overbought=rsi > 70, rsi_oversold=rsi < 30,
        htf_ema50=None, htf_ema200=None, htf_trend=None,
        integrity_score=1.0,
    )


async def check_tradeable(symbol: str, entry: float, atr: float, equity: float) -> tuple[bool, dict]:
    if entry <= 0 or atr <= 0:
        return False, {}
    from tradingos.strategies.trade_executor import _get_lot_size
    lot = await _get_lot_size(symbol)
    qty_step = lot["qty_step"] if lot["qty_step"] > 0 else 1.0
    raw_qty = RISK / (2 * atr)
    quantity = math.floor(raw_qty / qty_step) * qty_step
    notional_qty = 0.0
    if lot["min_notional"] > 0:
        notional_qty = math.ceil((lot["min_notional"] / entry) / qty_step) * qty_step
    required = max(lot["min_order_qty"], notional_qty)
    if equity > 0:
        max_qty = math.floor(equity * 0.20 / entry / qty_step) * qty_step
        quantity = min(quantity, max_qty)
    ok = quantity >= required and quantity >= qty_step
    return ok, {"required_qty": required, "qty": quantity,
                "risk_required": round(required * 2 * atr, 2)}


async def scan_symbol(symbol: str, calc: IndicatorCalculator,
                      sg: SignalGenerator, equity: float) -> dict:
    candles = await fetch_klines(symbol)
    n = len(candles)
    if n < NEED_HISTORY + 5:
        return {"symbol": symbol, "status": "no_data"}

    cand_bars = 0
    runs = 0
    prev_cand = False
    best = None
    start = max(NEED_HISTORY, n - WINDOW_HOURS)
    for i in range(start, n):
        fv = build_fv(symbol, candles[:i + 1], calc)
        _score_holder["score"] = None
        direction = sg.decide(symbol, fv, bar_idx=0)
        sc = _score_holder.get("score")
        prob = getattr(sc, "final_probability", 0.0) if sc else 0.0
        total = getattr(sc, "total_score", 0) if sc else 0
        quality = getattr(sc, "quality", "NONE") if sc else "NONE"
        if direction in ("BUY", "SELL") and prob >= PROB_MIN and quality in QUALITY_OK and total >= SCORE_MIN:
            cand_bars += 1
            if not prev_cand:
                runs += 1
                prev_cand = True
            if best is None or prob > best["prob"]:
                best = {"prob": prob, "score": total, "quality": quality,
                        "direction": direction, "adx": fv.adx, "atr": fv.atr,
                        "close": fv.close}
        else:
            prev_cand = False

    rec = {"symbol": symbol, "candidate_bars": cand_bars, "runs": runs}
    if best:
        t_ok, t_info = await check_tradeable(symbol, best["close"], best["atr"], equity)
        rec.update({
            "candidate": True,
            "prob_max": round(best["prob"], 3),
            "score": best["score"],
            "quality": best["quality"],
            "direction": best["direction"],
            "adx_ok": best["adx"] >= ADX_MIN,
            "adx": round(best["adx"], 1),
            "tradeable_025": t_ok,
            "lot": t_info,
        })
    return rec


async def main():
    import httpx

    current = current_observed_symbols()
    print(f"текущий наблюдаемый набор: {len(current)} символов")

    async with httpx.AsyncClient(timeout=30) as client:
        inst = (await client.get("https://api.bybit.com/v5/market/instruments-info",
                                 params={"category": "linear", "limit": 1000})).json()
        ticks = (await client.get("https://api.bybit.com/v5/market/tickers",
                                  params={"category": "linear"})).json()
    tick_map = {t["symbol"]: t for t in ticks["result"]["list"]}

    ranked = []
    for it in inst["result"]["list"]:
        sym = it.get("symbol", "")
        cls = classify_instrument(sym, it.get("symbolType", ""))
        if cls != "crypto_perp":
            continue
        tk = tick_map.get(sym)
        if not tk:
            continue
        vol = float(tk.get("volume24h", 0) or 0)
        if vol <= 0:
            continue
        ranked.append((sym, vol))
    ranked.sort(key=lambda x: -x[1])

    expanded = [s for s, _ in ranked[:EXPAND_TO]]
    additional = [s for s in expanded if s not in current]
    print(f"полный крипто-юниверс: {len(ranked)}; top-{EXPAND_TO}: {len(expanded)}; "
          f"дополнительно: {len(additional)}")

    from tradingos.strategies.trade_executor import _get_balance_or_zero
    equity = _get_balance_or_zero()

    calc = IndicatorCalculator()
    sg = SignalGenerator()
    sem = asyncio.Semaphore(6)

    async def guarded(sym):
        async with sem:
            try:
                return await scan_symbol(sym, calc, sg, equity)
            except Exception as e:
                return {"symbol": sym, "status": f"error: {e}"}

    scan_set = sorted(set(additional) | current)
    results = await asyncio.gather(*[guarded(s) for s in scan_set])

    groups = {"current": [], "additional": []}
    for r in results:
        if r["symbol"] in additional:
            groups["additional"].append(r)
        else:
            groups["current"].append(r)

    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "risk": RISK, "window_hours": WINDOW_HOURS,
              "expanded_size": EXPAND_TO, "summary": {}}
    print("\n=== UNIVERSE EXPANSION SCAN (окно 72ч, тот же Signal Engine) ===")
    print(f"{'группа':<12} {'симв':>5} {'с кандид':>9} {'канд-бары':>10} {'пробеги':>8} {'ADX ок':>7} {'торг $0.25':>10}")
    for grp in ("current", "additional"):
        res = groups[grp]
        scanned = len([r for r in res if "candidate" in r])
        with_cand = [r for r in res if r.get("candidate")]
        cand_bars = sum(r.get("candidate_bars", 0) for r in res)
        runs = sum(r.get("runs", 0) for r in res)
        adx_ok = sum(1 for r in with_cand if r.get("adx_ok"))
        tradeable = sum(1 for r in with_cand if r.get("tradeable_025"))
        print(f"{grp:<12} {len(res):>5} {len(with_cand):>9} {cand_bars:>10} {runs:>8} "
              f"{adx_ok:>7} {tradeable:>10}")
        report["summary"][grp] = {
            "scanned": scanned, "with_candidates": len(with_cand),
            "candidate_bars": cand_bars, "runs": runs,
            "adx_ok": adx_ok, "tradeable_025": tradeable,
            "symbols": {r["symbol"]: {k: r[k] for k in
                        ("direction", "prob_max", "score", "quality", "adx", "candidate_bars",
                         "runs", "adx_ok", "tradeable_025") if k in r}
                        for r in with_cand},
        }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport: {REPORT}")


if __name__ == "__main__":
    asyncio.run(main())
