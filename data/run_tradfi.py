#!/usr/bin/env python3
"""
data/run_tradfi.py — ОТДЕЛЬНЫЙ PAPER-контур tradFi (токенизированные акции + товары Bybit).

Параллельный крипто-контуру эксперимент: тот же Signal Engine и те же пороги
(prob>=0.55, quality, score>=55, ADX>=20), но на классе stock/commodity.

Только PAPER: ордера на биржу НЕ уходят. Свой конфиг (operations/tradfi_mode.json),
свой сигнальный лог, свои funnel-события, свой почасовой отчёт. Crypto-контур
(run_observation.py) не трогается.

Запуск:
  python3 -m tradingos.data.run_tradfi
  systemctl start tradingos-tradfi.service
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Load Bybit credentials from isolated execution .env (public endpoints only, but consistent)
_ENV_PATH = "/root/trading_brain_v4/research/execution/.env"
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ[_k.strip()] = _v.strip()

from tradingos.data.feature_store import FeatureStore
from tradingos.data.indicators import IndicatorCalculator
from tradingos.data.models.candle import Candle
from tradingos.data.reality_universe import classify_instrument
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator
from tradingos.signals import signal_scoring as _ss
from tradingos.strategies.trade_executor import TradeProposal, save_proposal

# --- Telemetry: capture final_probability without changing SignalGenerator ---
_score_holder = {"score": None}
_orig_calc = _ss.SignalScoringEngine.calculate_with_vectors
def _patched_calc(self, **kw):
    result = _orig_calc(self, **kw)
    _score_holder["score"] = result
    return result
_ss.SignalScoringEngine.calculate_with_vectors = _patched_calc

log = logging.getLogger("tradfi")

CONFIG = Path("/root/tradingos/operations/tradfi_mode.json")
SIGNAL_LOG = Path("/root/tradingos/memory/signal_log_tradfi.jsonl")
FUNNEL_EVENTS = Path("/root/tradingos/memory/funnel_tradfi_events.jsonl")
PAPER_TRADES = Path("/root/tradingos/memory/paper_trades_tradfi.jsonl")
REPORT_PATH = Path("/root/tradingos/tradfi_universe_report.json")

UNIVERSE_SIZE = 30
SYMBOL_COOLDOWN_SEC = 3600
MIN_PROPOSAL_INTERVAL = 60
PROB_THRESHOLD = 0.55
QUALITY_OK = ("MEDIUM", "GOOD", "EXCELLENT")
SCORE_THRESHOLD = 55
ADX_THRESHOLD = 20


def _log_funnel_event(event: str, proposal, extra: dict | None = None) -> None:
    rec = {
        "event": event,
        "ts": time.time(),
        "symbol": proposal.symbol,
        "side": proposal.side,
        "decision_id": getattr(proposal, "decision_id", ""),
    }
    if extra:
        rec.update(extra)
    FUNNEL_EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with FUNNEL_EVENTS.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _log_paper_trade(proposal) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "PAPER",
        "circuit": "tradfi",
        "decision_id": proposal.decision_id,
        "symbol": proposal.symbol,
        "side": proposal.side,
        "entry": proposal.entry,
        "stop_loss": proposal.stop_loss,
        "take_profit": proposal.take_profit,
        "rr": proposal.rr,
        "confidence": proposal.confidence,
        "strategy": proposal.strategy,
        "reason": proposal.reason,
    }
    PAPER_TRADES.parent.mkdir(parents=True, exist_ok=True)
    with PAPER_TRADES.open("a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {"mode": "PAPER", "risk_per_trade": 0.25, "max_positions": 2}


def generate_decision_id() -> str:
    return f"T-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


async def fetch_tradfi_universe(size: int = UNIVERSE_SIZE) -> list[dict]:
    """Top-N tokenized stocks + commodities by 24h volume (exchange taxonomy)."""
    import httpx

    inst_resp = httpx.get("https://api.bybit.com/v5/market/instruments-info",
                          params={"category": "linear", "limit": 1000}, timeout=30)
    inst_data = inst_resp.json()
    if inst_data.get("retCode") != 0:
        return []
    tick_resp = httpx.get("https://api.bybit.com/v5/market/tickers",
                          params={"category": "linear"}, timeout=30)
    tick_map = {}
    tick_data = tick_resp.json()
    if tick_data.get("retCode") == 0:
        tick_map = {t["symbol"]: t for t in tick_data["result"].get("list", [])}

    rows = []
    for it in inst_data["result"].get("list", []):
        sym = it.get("symbol", "")
        cls = classify_instrument(sym, it.get("symbolType", ""))
        if cls not in ("tokenized_stock", "commodity_token"):
            continue
        tick = tick_map.get(sym)
        if not tick:
            continue
        price = float(tick.get("lastPrice", 0) or 0)
        vol = float(tick.get("volume24h", 0) or 0)
        if price <= 0 or vol <= 0:
            continue
        rows.append({"symbol": sym, "price": price, "volume_24h": vol})
    rows.sort(key=lambda x: x["volume_24h"], reverse=True)
    return rows[:size]


class TradFiRunner:
    """PAPER-only observation loop for the tradFi circuit."""

    def __init__(self):
        self.symbols: list[str] = []
        self.feature_store = FeatureStore()
        self.indicator_calc = IndicatorCalculator()
        self.generators: dict[str, SignalGenerator] = {}
        self.stats = {"checks": 0, "signals": 0, "rejected": 0}
        self._candidates: list[dict] = []
        self._last_proposal_time = 0.0
        self._last_symbol_proposal: dict[str, float] = {}
        self._last_report_time = 0.0
        self._config = _load_config()

    async def _fetch_kline(self, symbol: str) -> list:
        import httpx

        resp = await httpx.AsyncClient(timeout=15).get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "linear", "symbol": symbol, "interval": "60", "limit": 200},
        )
        data = resp.json()
        if data.get("retCode") != 0:
            raise ValueError(f"API {data.get('retCode')}: {data.get('retMsg')}")

        candles = []
        for row in reversed(data.get("result", {}).get("list", [])):
            try:
                candles.append(Candle(
                    timestamp=int(row[0]),
                    open=float(row[1]), high=float(row[2]),
                    low=float(row[3]), close=float(row[4]),
                    volume=float(row[5]) if row[5] else 0.0,
                    symbol=symbol, timeframe="1h",
                ))
            except (IndexError, ValueError, TypeError):
                continue
        return candles

    def _build_fv(self, symbol: str) -> Optional[FeatureVector]:
        candles = self.feature_store.get_candles(symbol, "1h")
        if not candles or len(candles) < 20:
            return None

        last = candles[-1]
        prices = [c.close for c in candles if c.close > 0]

        ema20 = self.indicator_calc.ema(prices, 20) if len(prices) >= 20 else 0.0
        ema50 = self.indicator_calc.ema(prices, 50) if len(prices) >= 50 else 0.0
        ema200 = self.indicator_calc.ema(prices, 200) if len(prices) >= 200 else 0.0
        rsi = self.indicator_calc.rsi(prices, 14) if len(prices) >= 14 else 50.0
        atr = self.indicator_calc.atr(candles, 14) if len(candles) >= 14 else 0.0
        adx_val = self.indicator_calc.adx(candles, 14) if len(candles) >= 14 else 0.0
        macd = self.indicator_calc.macd(prices) if len(prices) >= 26 else {}
        bb = self.indicator_calc.bollinger(prices) if len(prices) >= 20 else {}
        vwap = self.indicator_calc.vwap(candles) if candles else 0.0
        vol_ratio = self.indicator_calc.volume_ratio(
            [c.volume for c in candles], 20) if len(candles) >= 20 else 1.0

        return FeatureVector(
            timestamp_ms=last.timestamp, symbol=symbol,
            open=last.open, high=last.high, low=last.low, close=last.close,
            volume=last.volume,
            ema20=ema20, ema50=ema50, ema200=ema200 or 0.0,
            rsi=rsi,
            macd_line=macd.get("macd", 0.0),
            macd_signal=macd.get("signal", 0.0),
            atr=atr,
            bb_upper=bb.get("upper", 0.0),
            bb_lower=bb.get("lower", 0.0),
            bb_middle=bb.get("middle", 0.0),
            adx=adx_val,
            volume_ma=0.0, volume_ratio=vol_ratio, obv=0.0, vwap=vwap,
            ema_bullish=ema20 > ema50 if ema20 > 0 and ema50 > 0 else False,
            price_above_ema50=last.close > ema50 if ema50 > 0 else False,
            rsi_overbought=rsi > 70, rsi_oversold=rsi < 30,
            htf_ema50=None, htf_ema200=None, htf_trend=None,
            integrity_score=1.0,
        )

    async def _poll_and_observe(self, symbol: str):
        candles = await self._fetch_kline(symbol)
        if not candles:
            return

        self.feature_store.add_candles(symbol, "1h", candles)
        self.feature_store.recalculate(symbol)

        fv = self._build_fv(symbol)
        if fv is None:
            return

        if symbol not in self.generators:
            self.generators[symbol] = SignalGenerator()
        sg = self.generators[symbol]

        _score_holder["score"] = None
        direction = sg.decide(symbol, fv, bar_idx=0)

        # ─── AUX INDICATOR LAYER (Stochastic + RSI-squeeze + MTF) ───
        aux = {"stoch_k": None, "stoch_d": None, "stoch_cross_up": False,
               "stoch_cross_down": False, "squeeze_on": False, "squeeze_fired": False,
               "squeeze_momentum": 0.0, "h4_trend": None, "d1_trend": None,
               "mtf_conflict": False, "stoch_zone_conflict": False}
        try:
            stoch = self.indicator_calc.stochastic(candles)
            sq = self.indicator_calc.rsi_squeeze(candles)
            aux.update({"stoch_k": stoch.get("k"), "stoch_d": stoch.get("d"),
                        "stoch_cross_up": stoch.get("cross_up", False),
                        "stoch_cross_down": stoch.get("cross_down", False),
                        "squeeze_on": sq.get("squeeze_on", False),
                        "squeeze_fired": sq.get("fired", False),
                        "squeeze_momentum": sq.get("momentum", 0.0)})
            # MTF: H4/D1 тренды через EMA20/50 (последний закрытый бар)
            for iv, key in (("240", "h4_trend"), ("D", "d1_trend")):
                r = httpx.get(
                    "https://api.bybit.com/v5/market/kline",
                    params={"category": "linear", "symbol": symbol,
                            "interval": iv, "limit": 120}, timeout=15,
                ).json().get("result", {}).get("list") or []
                if len(r) >= 60:
                    closes = [float(x[4]) for x in reversed(r)]
                    e20 = self.indicator_calc.ema(closes, 20)
                    e50 = self.indicator_calc.ema(closes, 50)
                    cl = closes[-2]
                    if cl > e20 > e50:
                        aux[key] = "UP"
                    elif cl < e20 < e50:
                        aux[key] = "DOWN"
                    else:
                        aux[key] = "MIXED"
            if direction == "BUY":
                aux["mtf_conflict"] = (aux["h4_trend"] == "DOWN" or aux["d1_trend"] == "DOWN")
                aux["stoch_zone_conflict"] = (aux["stoch_k"] is not None and aux["stoch_k"] > 80)
            elif direction == "SELL":
                aux["mtf_conflict"] = (aux["h4_trend"] == "UP" or aux["d1_trend"] == "UP")
                aux["stoch_zone_conflict"] = (aux["stoch_k"] is not None and aux["stoch_k"] < 20)
        except Exception:
            pass

        score_obj = _score_holder.get("score")
        prob = getattr(score_obj, "final_probability", 0.0) if score_obj else 0.0
        total = getattr(score_obj, "total_score", 0) if score_obj else 0
        quality = getattr(score_obj, "quality", "NONE") if score_obj else "NONE"
        reject_reason = ""
        if direction is None:
            reject_reason = ("probability_below_threshold" if score_obj is not None
                             else "no_direction")
            self.stats["rejected"] += 1
        else:
            self.stats["signals"] += 1
            if (prob >= PROB_THRESHOLD
                    and quality in QUALITY_OK
                    and total >= SCORE_THRESHOLD
                    and not aux["mtf_conflict"]
                    and not aux["stoch_zone_conflict"]):
                self._candidates.append({
                    "symbol": symbol,
                    "direction": direction,
                    "probability": prob,
                    "score": total,
                    "quality": quality,
                    "close": fv.close,
                    "atr": fv.atr,
                    "rsi": fv.rsi,
                    "adx": fv.adx,
                    "aux": aux,
                    "timestamp": time.time(),
                })
                log.info(f"📌 TRADFI CANDIDATE: {symbol} {direction} "
                         f"prob={prob:.2f} score={total} adx={fv.adx:.0f} "
                         f"mtf={aux['h4_trend']}/{aux['d1_trend']} "
                         f"stoch_K={aux['stoch_k']} squeeze={'ON' if aux['squeeze_on'] else 'OFF'}")
            elif direction is not None:
                self.stats["rejected"] += 1
                log.info(f"⏭️ TRADFI AUX-GATE: {symbol} {direction} "
                         f"mtf_conflict={aux['mtf_conflict']} "
                         f"stoch_zone={aux['stoch_zone_conflict']}")

        self.stats["checks"] += 1
        atr_val = fv.atr
        if direction == "BUY":
            entry_p, stop_l, take_p = fv.close, fv.close - atr_val * 2, fv.close + atr_val * 2
        elif direction == "SELL":
            entry_p, stop_l, take_p = fv.close, fv.close + atr_val * 2, fv.close - atr_val * 2
        else:
            entry_p = stop_l = take_p = None

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "direction": direction or "NONE",
            "reject_reason": reject_reason,
            "final_probability": round(prob, 4),
            "threshold": PROB_THRESHOLD,
            "score": total,
            "quality": quality,
            "rsi": round(fv.rsi, 1),
            "adx": round(fv.adx, 1),
            "close": round(fv.close, 4),
            "candles": len(candles),
            "atr": round(atr_val, 4) if atr_val else 0,
            "entry": round(entry_p, 6) if entry_p else None,
            "stop_loss": round(stop_l, 6) if stop_l else None,
            "take_profit": round(take_p, 6) if take_p else None,
            "aux": aux,
        }
        SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SIGNAL_LOG.open("a") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

        # ─── PAPER proposal: rank TOP-1 from collected candidates ─────
        now = time.time()
        if (self._candidates
                and (now - self._last_proposal_time) >= MIN_PROPOSAL_INTERVAL):
            executable = [c for c in self._candidates
                          if c.get("direction") in ("BUY", "SELL")]
            self._candidates.clear()

            if not executable:
                log.info("⏭️ TRADFI SKIP: no executable BUY/SELL candidates this cycle")
                return

            best = max(executable, key=lambda c: c["probability"] * c["score"])

            last_for_sym = self._last_symbol_proposal.get(best["symbol"], 0)
            if now - last_for_sym < SYMBOL_COOLDOWN_SEC:
                wait = int(SYMBOL_COOLDOWN_SEC - (now - last_for_sym))
                log.info(f"⏭️ TRADFI SKIP: {best['symbol']} cooldown {wait}s")
                self._last_proposal_time = now
                return

            adx_val = best.get("adx", 0) or 0
            if adx_val < ADX_THRESHOLD:
                log.info(f"⏭️ TRADFI SKIP: {best['symbol']} ADX={adx_val:.0f} < {ADX_THRESHOLD}")
                self._last_proposal_time = now
                return

            atr = best["atr"]
            entry = best["close"]
            # FIX 2026-09-01: 4×ATR недостижим на скальпе; позиционный 3R
            # (данные на исправленном стенде: D1/тренд 3R = +edge; комиссия
            # несущественна при тейке 3×ATR).
            if best["direction"] == "BUY":
                sl, tp = entry - atr * 2, entry + atr * 3
            else:
                sl, tp = entry + atr * 2, entry - atr * 3

            risk = float(self._config.get("risk_per_trade", 0.25))
            risk_per_unit = abs(entry - sl)
            if risk_per_unit <= 0:
                return
            raw_qty = risk / risk_per_unit
            qty_step = 1.0
            notional_qty = 0.0
            min_qty = 1.0
            try:
                from tradingos.strategies.trade_executor import _get_lot_size
                lot = await _get_lot_size(best["symbol"])
                min_qty = lot["min_order_qty"]
                qty_step = lot["qty_step"] if lot["qty_step"] > 0 else 1.0
                if lot["min_notional"] > 0:
                    notional_qty = math.ceil((lot["min_notional"] / entry) / qty_step) * qty_step
            except Exception as e:
                log.warning(f"lot size check failed ({best['symbol']}): {e}")
            required_qty = max(min_qty, notional_qty)
            quantity = math.floor(raw_qty / qty_step) * qty_step
            if quantity < required_qty:
                risk_required = required_qty * risk_per_unit
                log.info(f"⏭️ TRADFI SKIP (pre-order): {best['symbol']} raw_qty={raw_qty:.2f} "
                         f"required={required_qty} risk_required=${risk_required:.2f} "
                         f"(minOrderQty={min_qty} minNotional check) risk=${risk:.2f}")
                self._last_proposal_time = now
                return

            proposal = TradeProposal(
                symbol=best["symbol"],
                side=best["direction"],
                entry=entry,
                stop_loss=sl,
                take_profit=tp,
                rr=2.0,
                confidence=round(best["probability"], 2),
                strategy="TRADFI_DISCOVERY",
                decision_id=generate_decision_id(),
                reason=[f"TradFi TOP candidate: {best['symbol']} {best['direction']} "
                        f"prob={best['probability']:.2f} score={best['score']} "
                        f"quality={best['quality']}"],
                session="TRADFI",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            valid, msg = proposal.validate()
            if valid:
                save_proposal(proposal)
                _log_funnel_event("proposal", proposal)
                self._last_proposal_time = now
                self._last_symbol_proposal[proposal.symbol] = now
                mode = self._config.get("mode", "PAPER")
                if mode == "PAPER":
                    _log_paper_trade(proposal)
                    _log_funnel_event("opened", proposal, {"mode": "PAPER"})
                    log.info(f"🧪 TRADFI PAPER TRADE: {proposal.symbol} {proposal.side} "
                             f"entry={entry:.6f} SL={sl:.6f} TP={tp:.6f} conf={proposal.confidence:.2f}")
                else:
                    # LIVE: реальный ордер через trade_executor (2026-09-01 FIX —
                    # раньше контур молча сохранял proposal без исполнения)
                    try:
                        sys.path.insert(0, "/root/tradingos")
                        sys.path.insert(0, "/root/trading_brain_v4")
                        from tradingos.strategies.trade_executor import _execute_reality
                        proposal.status = "APPROVED"
                        res = await _execute_reality(proposal)
                        status = res.get("status", "?") if isinstance(res, dict) else str(res)
                        log.info(f"🚀 TRADFI LIVE EXEC: {proposal.symbol} {proposal.side} "
                                 f"→ {status}")
                    except Exception as _e:
                        log.error(f"❌ TRADFI LIVE EXEC FAILED {proposal.symbol}: {_e}")
            else:
                log.warning(f"TradFi proposal rejected: {msg}")

    async def run(self, interval: int = 60):
        self.symbols = [u["symbol"] for u in await fetch_tradfi_universe(UNIVERSE_SIZE)]
        if not self.symbols:
            log.error("TradFi universe empty — exiting")
            return
        log.info(f"TradFi universe ({len(self.symbols)} symbols): {self.symbols}")

        mode = self._config.get("mode", "PAPER")
        if mode not in ("PAPER", "LIVE"):
            log.error(f"⛔ tradfi_mode.json mode={mode} — недопустимо (PAPER|LIVE). Выход.")
            return

        log.info(f"TradFi {mode} observation started: {len(self.symbols)} symbols every {interval}s")

        while True:
            tick = time.time()
            for symbol in self.symbols:
                try:
                    await self._poll_and_observe(symbol)
                except Exception as e:
                    log.warning(f"{symbol}: {e}")

            elapsed = time.time() - tick
            wait = max(1, interval - int(elapsed))

            if time.time() - self._last_report_time >= 3600:
                try:
                    from tradingos.tools.tradable_universe_report import generate_tradable_universe_report
                    await asyncio.to_thread(
                        generate_tradable_universe_report,
                        feature_store=self.feature_store,
                        report_path=REPORT_PATH,
                        allowed=("tokenized_stock", "commodity_token"),
                        signal_log=SIGNAL_LOG,
                        funnel_events=FUNNEL_EVENTS,
                        config_path=CONFIG,
                    )
                    log.info("tradfi_universe_report.json updated (hourly)")
                except Exception as e:
                    log.warning(f"tradfi report failed: {e}")
                self._last_report_time = time.time()

            await asyncio.sleep(wait)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    await TradFiRunner().run(interval=60)


if __name__ == "__main__":
    asyncio.run(main())
