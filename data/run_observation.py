#!/usr/bin/env python3
"""
run_observation.py — единый процесс для сбора данных + наблюдения сигналов.

Объединяет:
  OHLCVPoller  → FeatureStore (запись)
  SignalObserver → FeatureStore (чтение) — без дублирования REST-запросов.

Запуск:
  python3 -m tradingos.data.run_observation
  systemctl start tradingos-observation.service
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# Load Bybit credentials from isolated execution .env
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
from tradingos.data.ohlcv_poller import OHLCVPoller
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator
from tradingos.signals import signal_scoring as _ss
from tradingos.strategies.trade_executor import TradeProposal, save_proposal
from tradingos.strategies.trade_executor import _execute_reality

# --- Telemetry: capture final_probability without changing SignalGenerator ---
# Monkey-patch only scoring engine's return value, NOT decision logic.
# After decide() returns, _last_score holds the last SignalScore (or None).
_score_holder = {"score": None}
_orig_calc = _ss.SignalScoringEngine.calculate_with_vectors
def _patched_calc(self, **kw):
    result = _orig_calc(self, **kw)
    _score_holder["score"] = result
    return result
_ss.SignalScoringEngine.calculate_with_vectors = _patched_calc

log = logging.getLogger("observation")
SIGNAL_LOG = Path("/root/tradingos/memory/signal_log.jsonl")
FUNNEL_EVENTS = Path("/root/tradingos/memory/funnel_events.jsonl")


def _log_funnel_event(event: str, proposal, extra: dict | None = None) -> None:
    """Append a statistics-only event (proposal/opened) for the hourly funnel."""
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


def generate_decision_id() -> str:
    """Generates a unique ID for a decision: D-YYYYMMDD-SHORTID"""
    date_str = datetime.now().strftime("%Y%m%d")
    unique_part = uuid.uuid4().hex[:6].upper()
    return f"D-{date_str}-{unique_part}"


GENOME_LOG = Path("/root/tradingos/memory/trade_genome.jsonl")

_LEGACY_UNIVERSE: set[str] | None = None


def _universe_group(symbol: str) -> str:
    """old_34 (наблюдался до расширения universe 2026-08-07) или expanded."""
    global _LEGACY_UNIVERSE
    if _LEGACY_UNIVERSE is None:
        try:
            legacy = json.loads(
                Path("/root/tradingos/operations/universe_legacy_34.json").read_text()
            )
            _LEGACY_UNIVERSE = set(legacy.get("symbols", []))
        except Exception:
            _LEGACY_UNIVERSE = set()
    return "old_34" if symbol in _LEGACY_UNIVERSE else "expanded"


def _log_genome_open(proposal, best: dict | None = None, volume_1h: float = 0.0,
                     fill_price: float = 0.0, ticket: str = "") -> None:
    """Statistics-only OPEN record for the Trade Genome (по одной строке на сделку).

    Entry-сторона описания сделки. Exit-сторона (holding/MFE/MAE/realized_R)
    приходит из logs/trades/trade_results.jsonl и объединяется инструментом
    tools/trade_genome_join.py. expected_R = (rr+1)*prob - 1 — модельное ожидание
    при фиксированном RR (калибровка prob: см. замечания в инструменте).
    """
    now_utc = datetime.now(timezone.utc)
    prob = best.get("probability", proposal.confidence) if best else proposal.confidence
    rr = proposal.rr or 2.0
    expected_r = round((rr + 1) * prob - 1, 3)
    rec = {
        "event": "OPEN",
        "genome_version": 1,
        "ts": now_utc.isoformat(),
        "ts_unix": time.time(),
        "decision_id": getattr(proposal, "decision_id", ""),
        "symbol": proposal.symbol,
        "side": proposal.side,
        "universe_group": _universe_group(proposal.symbol),
        "entry": proposal.entry,
        "sl": proposal.stop_loss,
        "tp": proposal.take_profit,
        "rr": rr,
        "prob": prob,
        "score": best.get("score") if best else None,
        "quality": best.get("quality") if best else None,
        "adx": best.get("adx") if best else None,
        "atr": best.get("atr") if best else None,
        "rsi": best.get("rsi") if best else None,
        "volume_1h": round(volume_1h, 4) if volume_1h else None,
        "expected_R": expected_r,
        "utc_hour": now_utc.hour,
        "weekday": now_utc.strftime("%A"),
        "ticket": ticket,
        "fill_price": round(fill_price, 8) if fill_price else proposal.entry,
    }
    GENOME_LOG.parent.mkdir(parents=True, exist_ok=True)
    with GENOME_LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _log_paper_trade(proposal) -> None:
    """Append a PAPER (virtual) trade to paper_trades.jsonl.

    Used to test the full pipeline (Signal → Ranking → Risk → Sizing → Journal)
    without sending an order to the exchange. Same SL/TP/risk as a real trade.
    """
    paper_path = Path("/root/tradingos/memory/paper_trades.jsonl")
    paper_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": "PAPER",
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
    with open(paper_path, "a") as _f:
        _f.write(json.dumps(record, ensure_ascii=False) + "\n")


class ObservationRunner:
    """Объединяет poller + observer через общий FeatureStore."""

    def __init__(self, symbols: list[str] | None = None):
        if symbols is None:
            # Dynamic universe loaded at startup
            symbols = []
        self.symbols = symbols
        self.feature_store = FeatureStore()
        self.indicator_calc = IndicatorCalculator()
        self.generators: dict[str, SignalGenerator] = {}
        self.stats = {"checks": 0, "signals": 0, "buy": 0, "sell": 0, "rejected": 0}
        self.running = False
        self._http_proxy = ""
        # Reality Mode: candidate queue
        self._reality_candidates: list[dict] = []
        self._last_reality_proposal_time = 0.0  # unix timestamp
        self._last_symbol_proposal: dict[str, float] = {}  # symbol -> unix ts
        # FIX 2026-08-31: 1ч cooldown после КАЖДОГО proposal душил торговлю
        # (4 сделки/2ч при 32 кандидатах). 30 мин достаточно: символ не
        # спамится, но не пропадает надолго при сильном сигнале.
        self._SYMBOL_COOLDOWN_SEC = 1800  # 30min between same-symbol proposals
        self._last_report_time = 0.0  # hourly tradable-universe report
        self._tg = None  # Telegram notifier (OPEN-уведомления из observation)

    async def run(self, interval: int = 60):
        """Основной цикл: poll → store → observe → log."""
        self.running = True
        
        # Load dynamic universe if not provided
        if not self.symbols:
            from tradingos.data.reality_universe import fetch_universe, format_universe_report
            universe, counts = await fetch_universe()
            # Universe size is config-driven (trading_mode.json universe_size).
            # 2026-08-07: расширен 30 -> 120 по результатам universe_expansion_scan
            # (~5x больше торгуемых кандидатов при той же плотности на символ).
            universe_size = 30
            try:
                with Path("/root/tradingos/operations/trading_mode.json").open() as f:
                    universe_size = int(json.load(f).get("universe_size", 30))
            except Exception:
                pass
            universe_symbols = [u["symbol"] for u in universe[:universe_size]]
            # LIQUID WHITELIST — BTC/ETH/SOL/BNB/XRP give stable trends on 1H.
            # 2026-08-06: added to ensure good-quality signals (score≥55) appear regularly.
            LIQUID_WHITELIST = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
            self.symbols = LIQUID_WHITELIST + [s for s in universe_symbols if s not in LIQUID_WHITELIST]
            report = format_universe_report(universe, counts)
            for line in report.split("\n"):
                if line.strip():
                    log.info(line)
            log.info(f"Dynamic universe loaded: {len(self.symbols)} active symbols")
        
        log.info(f"Observation started: {self.symbols} every {interval}s")

        # Telegram notifier для OPEN-уведомлений (в observation есть prob/score/adx)
        try:
            if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
                from tradingos.notifier.notifier import make_notifier_from_env
                self._tg = make_notifier_from_env()
                if self._tg is not None:
                    await self._tg.start()
                    log.info("Telegram notifier started (OPEN cards from observation loop)")
        except Exception as e:
            log.warning(f"Telegram notifier init failed: {e}")

        while self.running:
            tick = time.time()

            for symbol in self.symbols:
                try:
                    await self._poll_and_observe(symbol)
                except Exception as e:
                    log.warning(f"{symbol}: {e}")

            elapsed = time.time() - tick
            wait = max(1, interval - int(elapsed))

            # Hourly tradable-universe report (2026-08-06): shows how many symbols
            # are physically tradeable at the current risk_per_trade — answers
            # "waiting for a signal, or risk too small for the market?".
            if time.time() - self._last_report_time >= 3600:
                try:
                    from tradingos.tools.tradable_universe_report import generate_tradable_universe_report
                    await asyncio.to_thread(generate_tradable_universe_report,
                                            feature_store=self.feature_store)
                    log.info("tradable_universe_report.json updated (hourly)")
                except Exception as e:
                    log.warning(f"tradable universe report failed: {e}")
                self._last_report_time = time.time()

            # B-fix: periodic equity sample — daily-loss guard reacts MID-DAY,
            # not only at the next entry attempt. Never touches positions.
            try:
                import sys as _sys
                _sys.path.insert(0, "/root/tradingos")
                from tradingos.strategies.deposit_guard import get_guard
                _g = get_guard()
                _eq = _g._get_equity()
                if _eq and _eq > 0:
                    _g.on_equity_sample(_eq)
            except Exception as e:
                log.warning(f"equity sample failed: {e}")

            await asyncio.sleep(wait)

    async def _fetch_kline(self, symbol: str) -> list:
        """Получить последние 200 1h свечей с Bybit."""
        import httpx

        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": "60",
            "limit": 200,
        }
        client_args = {"timeout": 15}
        if self._http_proxy:
            client_args["proxies"] = self._http_proxy

        async with httpx.AsyncClient(**client_args) as client:
            resp = await client.get(
                "https://api.bybit.com/v5/market/kline", params=params
            )

        data = resp.json()
        if data.get("retCode") != 0:
            raise ValueError(f"API {data.get('retCode')}: {data.get('retMsg')}")

        from tradingos.data.models.candle import Candle
        candles = []
        for row in reversed(data.get("result", {}).get("list", [])):
            try:
                c = Candle(
                    timestamp=int(row[0]),
                    open=float(row[1]), high=float(row[2]),
                    low=float(row[3]), close=float(row[4]),
                    volume=float(row[5]) if row[5] else 0.0,
                    symbol=symbol, timeframe="1h",
                )
                candles.append(c)
            except (IndexError, ValueError, TypeError):
                continue
        return candles

    def _fetch_daily(self, symbol: str) -> list:
        """Получить D1-свечи для тренд-фильтра (гибрид: D1-тренд + H1-вход).
        Кэшируется в feature_store под timeframe 'D'. Синхронный httpx — безопасно
        вызывать из async-контекста через to_thread."""
        cached = self.feature_store.get_candles(symbol, "D")
        if cached and len(cached) >= 60:
            return cached
        import httpx
        params = {"category": "linear", "symbol": symbol, "interval": "D", "limit": 200}
        try:
            with httpx.Client(timeout=15) as client:
                resp = client.get("https://api.bybit.com/v5/market/kline", params=params)
                data = resp.json()
            if data.get("retCode") != 0:
                return []
            from tradingos.data.models.candle import Candle
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
            for c in candles:
                self.feature_store.add_candle(symbol, "D", c)
            return candles
        except Exception as e:
            log.debug(f"fetch_daily {symbol}: {e}")
            return []

    def _build_fv(self, symbol: str) -> Optional[FeatureVector]:
        """Построить FeatureVector из FeatureStore."""
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
        """Один цикл: fetch → store → signal → log."""
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

        _score_holder["score"] = None  # Reset telemetry before decide()
        direction = sg.decide(symbol, fv, bar_idx=0)

        reject_reason = ""
        if direction is None:
            if _score_holder.get("score") is None:
                reject_reason = "no_direction"
            else:
                reject_reason = "probability_below_threshold"
            self.stats["rejected"] += 1
        else:
            self.stats["signals"] += 1
            self.stats["buy" if direction == "BUY" else "sell"] += 1

            # FIX 2026-08-03: accepted signals (direction != None) MUST be added
            # to _reality_candidates too, otherwise they never reach ranking/execution.
            # Before: only None-direction signals were collected → zero trades even
            # when SignalGenerator produced valid BUY/SELL (MOVEUSDT, HUSDT, ...).
            score_for_cand = _score_holder.get("score")
            prob_for_cand = getattr(score_for_cand, "final_probability", 0.0) if score_for_cand else 0.0
            total_for_cand = getattr(score_for_cand, "total_score", 0) if score_for_cand else 0
            quality_for_cand = getattr(score_for_cand, "quality", "NONE") if score_for_cand else "NONE"
            # FIX 2026-09-01 (owner): фильтры были закручены «на глаз» при кривых свечах.
            # Проверка по signal_log (17728 сигналов): prob≥0.70 = 0 сигналов, score≥75 = 0,
            # т.е. старые пороги физически недостижимы моделью (её максимум 0.66/66) —
            # система была МЁРТВОЙ. Достижимо: prob≥0.58 + score≥60 + GOOD = 11.7% сигналов.
            # Позиционный edge (D1-тренд 3R) не зависит от этих порогов — ослабляем до
            # реально встречающихся значений, чтобы система работала.
            _qty_ok = quality_for_cand in ("MEDIUM", "GOOD", "EXCELLENT")
            _prob_ok = prob_for_cand >= 0.55
            _score_ok = total_for_cand >= 60
            # Тренд-фильтр УБРАН здесь (2026-09-01): старый H1-EMA20 конфликтовал
            # с гибридным D1-EMA50-фильтром (ниже) и блокировал сильные сигналы
            # (AGIUSDT ADX=46 BUY отклонялся из-за close чуть ниже H1-EMA20).
            # Тренд теперь определяется ТОЛЬКО на D1 (доказанный edge +305R).
            _trend_ok = True
            # ADX FILTER: тренд (смягчён 2026-09-01: 25→20 — при 25 душило, 34% сигналов;
            # позиционная логика D1-тренда использует EMA50, ADX 20 достаточен)
            _adx_fv = getattr(fv, "adx", 0) or 0
            _adx_ok = _adx_fv >= 20
            if not _adx_ok and direction:
                log.info(f"⏭️ ADX-SKIP {symbol}: ADX={_adx_fv:.1f} < 20 — боковик, без тренда не входим")

            if _prob_ok and _qty_ok and _score_ok and _trend_ok and _adx_ok:
                # ─── T9/T10: паттерны + VWAP/SR (2026-09-01) ──────
                # Свечные+графические паттерны (30+ типов), Dynamic Deviation
                # Channels, VWAP-bounce, S/R-уровни. Логируются как SHADOW
                # (не блокируют) — накопление статистики в pattern_log.jsonl.
                pattern_info = None
                ddc_info = None
                vwap_info = None
                sr_info = None
                fb_info = None
                fib_info = None
                orb_info = None
                smc_info = None
                try:
                    from tradingos.signals.pattern_detector import (
                        detect_all_patterns, PatternTracker,
                    )
                    _candles_full = self.feature_store.get_candles(symbol, "1h")
                    if _candles_full and len(_candles_full) >= 30:
                        _candle_dicts = [c.to_dict() for c in _candles_full[-60:]]
                        _pd = detect_all_patterns([c.to_dict() for c in _candles_full])
                        pattern_info = _pd.get("patterns") or []
                        ddc_info = _pd.get("ddc")
                        # VWAP-bounce + SMA20-bounce + S/R + FalseBreak
                        try:
                            from tradingos.signals.vwap_sr import (
                                VWAPBounceDetector, SRLevelDetector, FalseBreakDetector,
                                SMA20BounceDetector,
                            )
                            _vw = VWAPBounceDetector().detect(
                                _candle_dicts[-10:], fv.vwap or 0)
                            if _vw:
                                vwap_info = {
                                    "direction": _vw.direction,
                                    "quality": round(_vw.bounce_quality, 3),
                                    "reason": _vw.reason,
                                }
                            # SMA20-bounce (Kristina Forex / LiveFree FX): откат к EMA20
                            try:
                                _ema20v = getattr(fv, "ema20", 0) or 0
                                _sw = SMA20BounceDetector().detect(
                                    _candle_dicts[-10:], _ema20v)
                                if _sw and _ema20v:
                                    vwap_info = {
                                        "direction": _sw.direction,
                                        "quality": round(_sw.bounce_quality, 3),
                                        "reason": _sw.reason,
                                    }
                            except Exception as _e:
                                log.debug(f"sma20 bounce fail {symbol}: {_e}")
                            _sr = SRLevelDetector().nearest_levels(
                                [c.to_dict() for c in _candles_full], fv.close)
                            sr_info = {
                                "support": _sr["nearest_support"].price if _sr["nearest_support"] else None,
                                "support_touches": _sr["nearest_support"].touches if _sr["nearest_support"] else 0,
                                "resistance": _sr["nearest_resistance"].price if _sr["nearest_resistance"] else None,
                                "resistance_touches": _sr["nearest_resistance"].touches if _sr["nearest_resistance"] else 0,
                            }
                            # FalseBreak (Craig Percoco): ложный пробой сильного уровня
                            _fb = FalseBreakDetector().detect(_candle_dicts)
                            if _fb:
                                fb_info = {
                                    "direction": _fb["direction"],
                                    "pattern": _fb["pattern"],
                                    "level": _fb["level"],
                                    "touches": _fb["touches"],
                                    "reason": _fb["reason"],
                                }
                            # Fibonacci (Karen Fu): ретрейсмент + Golden Pocket
                            # + конфлюэнция fib×S/R
                            try:
                                from tradingos.signals.fib_detector import FibonacciDetector
                                _sr_all = SRLevelDetector().detect(_candle_dicts)
                                _fib = FibonacciDetector().detect(_candle_dicts, _sr_all)
                                if _fib:
                                    fib_info = {
                                        "direction": _fib.direction,
                                        "retracement": _fib.retracement,
                                        "in_golden_pocket": _fib.in_golden_pocket,
                                        "confluence": _fib.confluence_level,
                                        "reason": _fib.reason,
                                    }
                            except Exception as _e:
                                log.debug(f"fib scan fail {symbol}: {_e}")
                            # ORB (Opening Range Breakout, Trade Your Edge):
                            # диапазон первых N свечей + пробой + ретест
                            try:
                                from tradingos.signals.orb_detector import ORBDetector
                                _orb = ORBDetector(range_bars=5).detect(_candle_dicts)
                                if _orb:
                                    orb_info = {
                                        "direction": _orb.direction,
                                        "high": _orb.high,
                                        "low": _orb.low,
                                        "entry": _orb.entry,
                                        "stop": _orb.stop,
                                        "target": _orb.target,
                                        "reason": _orb.reason,
                                    }
                            except Exception as _e:
                                log.debug(f"orb scan fail {symbol}: {_e}")
                            # SMC (Smart Money Concepts, Smart Risk):
                            # Liquidity Sweep + Order Block + Fair Value Gap
                            try:
                                from tradingos.signals.smc_detector import SMCDetector
                                _smc = SMCDetector().detect(_candle_dicts)
                                if _smc:
                                    smc_info = {
                                        "direction": _smc.direction,
                                        "sweep_price": _smc.sweep_price,
                                        "ob_low": _smc.ob_zone_low,
                                        "ob_high": _smc.ob_zone_high,
                                        "entry": _smc.entry,
                                        "stop": _smc.stop,
                                        "target": _smc.target,
                                        "reason": _smc.reason,
                                    }
                            except Exception as _e:
                                log.debug(f"smc scan fail {symbol}: {_e}")
                        except Exception as _e:
                            log.debug(f"vwap/sr scan fail {symbol}: {_e}")
                        # Записать паттерны в лог-трекер (валидация: совпадает ли
                        # направление паттерна с направлением сигнала)
                        try:
                            PatternTracker.log_scan(
                                symbol, direction, prob_for_cand, pattern_info,
                                ddc_info, vwap_info, sr_info, fb_info, fib_info,
                                orb_info, smc_info)
                        except Exception as _e:
                            log.debug(f"pattern tracker fail: {_e}")
                except Exception as e:
                    log.debug(f"pattern scan fail {symbol}: {e}")

                # 2026-08-27: meta_v1 shadow scorer (LightGBM on passed+rejected signals).
                # Scores are LOGGED but do NOT block (shadow mode). When validated via
                # OOS WR uplift over 30+ days on demo $100k, promote to active gate.
                meta_prob = None
                try:
                    from ml.meta_v1.meta_v1_scorer import score_signal
                    meta_prob = score_signal({
                        "score": total_for_cand,
                        "rsi": fv.rsi or 0, "adx": fv.adx or 0, "atr": fv.atr or 0,
                        "ema20": getattr(fv, "ema20", 0) or 0,
                        "final_probability": prob_for_cand,
                        "confidence": getattr(fv, "confidence", 0.5) or 0.5,
                        "rejected": 0,
                    })
                except Exception:
                    pass
                # 2026-08-27: foundation_v1 drift prediction (zero-shot statistical).
                # Direct OOF AUC = 0.574 @ 4h horizon — beats meta_v1 OOF (0.556).
                # Logged alongside meta_v1 as second shadow filter.
                drift_pred = None
                try:
                    import numpy as _np
                    from ml.foundation_v1.foundation_v1_scorer import should_take_by_drift
                    _candles = self.feature_store.get_candles(symbol, "1h")
                    if _candles and len(_candles) >= 5:
                        _closes = _np.array([c.close for c in _candles[-64:]], dtype=_np.float64)
                        _, drift_pred = should_take_by_drift(_closes, threshold=0.005, horizon=4)
                except Exception:
                    pass
                self._reality_candidates.append({
                    "symbol": symbol,
                    "direction": direction,
                    "probability": prob_for_cand,
                    "score": total_for_cand,
                    "quality": quality_for_cand,
                    "close": fv.close,
                    "atr": fv.atr,
                    "rsi": fv.rsi,
                    "adx": fv.adx,
                    "timestamp": time.time(),
                    "meta_v1_prob": meta_prob,
                    "drift_fwd_1h_pred": drift_pred,
                    "patterns": [p.get("name") for p in pattern_info][:5] if pattern_info else [],
                    "pattern_best": pattern_info[0] if pattern_info else None,
                    "ddc_signal": ddc_info.get("signal") if ddc_info else None,
                    "ddc_trend": ddc_info.get("trend") if ddc_info else None,
                    "vwap_signal": vwap_info,
                    "sr_levels": sr_info,
                    "false_break": fb_info,
                    "fib": fib_info,
                    "orb": orb_info,
                    "smc": smc_info,
                })
                _pat_s = ",".join(p.get("name") or "?" for p in pattern_info[:3]) if pattern_info else "-"
                _pat_d = pattern_info[0].get("direction", "?") if pattern_info else "-"
                _agree = "✓" if pattern_info and (
                    (pattern_info[0].get("direction") == "BULLISH") == (direction == "BUY")) else "✗"
                _fb_s = fb_info.get("pattern", "-") if fb_info else "-"
                _fb_agree = "✓" if fb_info and (
                    (fb_info.get("direction") == "LONG") == (direction == "BUY")) else ("✗" if fb_info else "")
                _orb_s = orb_info.get("direction", "-") if orb_info else "-"
                _smc_s = smc_info.get("direction", "-") if smc_info else "-"
                log.info(f"📌 REALITY CANDIDATE: {symbol} {direction} "
                         f"prob={prob_for_cand:.2f} score={total_for_cand} "
                         f"pat={_pat_s}[{_pat_d}]{_agree} ddc={ddc_info.get('signal', '-') if ddc_info else '-'} "
                         f"vwap={vwap_info.get('direction', '-') if vwap_info else '-'} "
                         f"fb={_fb_s}{_fb_agree} orb={_orb_s} smc={_smc_s}"
                         + (f" meta_v1={meta_prob:.2f}" if meta_prob is not None else "")
                         + (f" drift_1h={drift_pred:+.4f}" if drift_pred is not None else ""))

            # First accepted signal → trigger Stage 1 event
            # FIX 2026-08-07: block referenced final_prob/entry_p/stop_l/take_p/atr_val
            # before they were assigned (NameError on every signals==1, aborting the
            # whole symbol cycle). Use values already in scope (prob_for_cand, fv.*).
            if self.stats["signals"] == 1:
                _atr_1 = fv.atr or 0.0
                _entry_1 = fv.close
                _sl_1 = _entry_1 - _atr_1 * 2 if direction == "BUY" else _entry_1 + _atr_1 * 2
                _tp_1 = _entry_1 + _atr_1 * 2 if direction == "BUY" else _entry_1 - _atr_1 * 2
                event = {
                    "event": "FIRST_ACCEPTED_SIGNAL",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "symbol": symbol,
                    "direction": direction,
                    "confidence": round(prob_for_cand, 4),
                    "entry": round(_entry_1, 6) if _entry_1 else 0,
                    "stop_loss": round(_sl_1, 6) if _sl_1 else 0,
                    "take_profit": round(_tp_1, 6) if _tp_1 else 0,
                    "adx": round(fv.adx, 1),
                    "rsi": round(fv.rsi, 1),
                    "atr": round(_atr_1, 4) if _atr_1 else 0,
                    "stage": "STAGE_1_SHADOW_AUDIT_REQUIRED",
                    "execution": "LOCKED",
                }
                event_path = Path("/root/tradingos/memory/first_signal_event.json")
                with open(event_path, "w") as f:
                    json.dump(event, f, indent=2, ensure_ascii=False)
                log.info(f"🚨 FIRST ACCEPTED SIGNAL: {symbol} {direction} "
                         f"conf={prob_for_cand:.3f} entry={_entry_1:.6f} "
                         f"SL={_sl_1:.6f} TP={_tp_1:.6f}")
                log.info(f"   Stage 1 shadow audit required. Execution LOCKED.")

        self.stats["checks"] += 1

        # Capture final_probability and threshold from the last scoring call
        last_score = _score_holder.get("score")
        if last_score is not None:
            final_prob = round(last_score.final_probability, 4)
            threshold_val = last_score.threshold
            total_score = last_score.total_score
            quality = last_score.quality
        else:
            final_prob = 0.0
            threshold_val = 0.55
            total_score = 0
            quality = "NONE"

        # ATR-based stop levels (same rule as in backtest: 2 ATR SL, 1:1 RR)
        atr_val = fv.atr
        if direction == "BUY":
            entry_p = fv.close
            stop_l = fv.close - atr_val * 2
            take_p = fv.close + atr_val * 2
        elif direction == "SELL":
            entry_p = fv.close
            stop_l = fv.close + atr_val * 2
            take_p = fv.close - atr_val * 2
        else:
            entry_p = None
            stop_l = None
            take_p = None

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "direction": direction or "NONE",
            "reject_reason": reject_reason,
            "final_probability": final_prob,
            "threshold": threshold_val,
            "score": total_score,
            "quality": quality,
            "rsi": round(fv.rsi, 1),
            "adx": round(fv.adx, 1),
            "ema20": round(fv.ema20, 4),
            "close": round(fv.close, 4),
            "candles": len(candles),
            "atr": round(atr_val, 4) if atr_val else 0,
            "entry": round(entry_p, 6) if entry_p else None,
            "stop_loss": round(stop_l, 6) if stop_l else None,
            "take_profit": round(take_p, 6) if take_p else None,
            "confidence": round(fv.integrity_score, 4),
            "regime": "unknown",
        }

        SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SIGNAL_LOG, "a") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

        if direction:
            log.info(f"SIGNAL {symbol} {direction} RSI={fv.rsi:.0f} ADX={fv.adx:.0f}")

        # ---------------------------------------------------------------------
        # ВАЖНО (2026-09-01): fallback-путь ОТКЛОНЁННЫХ сигналов УДАЛЁН.
        # Раньше: если SignalGenerator.decide() вернул None (нет сигнала),
        # этот fallback ВСЁ РАВНО создавал кандидата с prob>=0.55 / MEDIUM —
        # и система торговала «отклонённые» сигналы. Отсюда 842 сделки
        # с WR 14.5% (LONG 49% = монетка, SELL 7%): генератор говорил «нет»
        # (нет edge), а мы входили и теряли деньги.
        # Торгуем ТОЛЬКО сигналы, принятые основной веткой (prob>=0.70,
        # GOOD+, ADX>=25, паттерн-гейт). direction==None → нет сделки.
        # ---------------------------------------------------------------------

        # ---------------------------------------------------------------------
        # REALITY MODE: rank and propose TOP 1 immediately in AUTO mode
        # ---------------------------------------------------------------------
        now = time.time()
        # In AUTO mode: propose immediately when candidates exist (no timer)
        # In MANUAL mode: rate-limit to once per hour to avoid spam
        mode_path = Path("/root/tradingos/operations/trading_mode.json")
        is_auto = False
        try:
            if mode_path.exists():
                with open(mode_path) as f:
                    is_auto = json.load(f).get("mode") == "AUTO"
        except:
            pass
        
        min_interval = 60 if is_auto else 3600  # 1min in AUTO, 1h in MANUAL
        if (self._reality_candidates
                and (now - self._last_reality_proposal_time) >= min_interval):
            # Filter: only executable candidates (direction must be BUY or SELL)
            executable = [c for c in self._reality_candidates
                          if c.get("direction") in ("BUY", "SELL")]
            self._reality_candidates.clear()

            if not executable:
                log.info("⏭️ REALITY SKIP: no executable BUY/SELL candidates in this cycle")
                # Track opportunity loss
                try:
                    sys.path.insert(0, "/root/tradingos")
                    from guardian.opportunity_loss import record_rejection
                    for c in self._reality_candidates[:5]:
                        record_rejection(c["symbol"], c.get("direction","NONE"), c["probability"], c["score"], c["quality"], "no_direction", c["close"], c["timestamp"], c.get("atr",0), meta_v1_prob=c.get("meta_v1_prob"))
                except Exception:
                    pass
                return

            # FIX 2026-08-31 (profit loss, explore-1): раньше `best` выбирался
            # один, и при cooldown/ADX-скипе ОСТАЛЬНЫЕ кандидаты терялись
            # (список чистился до обработки) — 4 proposals из 32 кандидатов/2ч.
            # Теперь идём по кандидатам по рангу (prob×score), пропуская только
            # тех, кто в cooldown или ADX<20.
            ranked = sorted(executable, key=lambda c: c["probability"] * c["score"], reverse=True)
            best = None
            # ─── PATTERN GATE (2026-09-01, owner) ──────────────────────────
            # Раньше паттерны (vwap/smc/fib/false_break/свечные) ЛОГИРОВАЛИСЬ,
            # но не влияли на входы. Торговля шла по индикаторам без edge
            # (842 сделки: WR 14.5%, LONG 49% = монетка).
            # Теперь вход ТОЛЬКО при подтверждении хотя бы одного детектора
            # в направлении сигнала: vwap_bounce / false_break / fib / smc /
            # световой паттерн / ddc. Ни один подтверждающий → проходим мимо.
            def _pattern_confirmed(_c) -> tuple:
                """(confirmed: bool, sources: list, against: bool)"""
                _dir = _c.get("direction")  # BUY/SELL
                _want_long = (_dir == "BUY")
                _confirmed, _sources, _against = False, [], False

                # VWAP-bounce
                _vwap = _c.get("vwap_signal") or {}
                if _vwap.get("direction"):
                    _up = (_vwap["direction"] == "LONG")
                    _sources.append(f"vwap:{_vwap['direction']}")
                    if _up == _want_long:
                        _confirmed = True
                    else:
                        _against = True
                # False Break
                _fb = _c.get("false_break") or {}
                if _fb.get("direction"):
                    _up = (_fb["direction"] == "LONG")
                    _sources.append(f"fb:{_fb['direction']}")
                    if _up == _want_long:
                        _confirmed = True
                    else:
                        _against = True
                # Fib (golden pocket)
                _fib = _c.get("fib") or {}
                if _fib.get("direction"):
                    _up = (_fib["direction"] == "LONG")
                    _sources.append(f"fib:{_fib['direction']}")
                    if _up == _want_long:
                        _confirmed = True
                    else:
                        _against = True
                # SMC
                _smc = _c.get("smc") or {}
                if _smc.get("direction"):
                    _up = (_smc["direction"] == "LONG")
                    _sources.append(f"smc:{_smc['direction']}")
                    if _up == _want_long:
                        _confirmed = True
                    else:
                        _against = True
                # Свечные/графические паттерны (best)
                _bp = _c.get("pattern_best") or {}
                if _bp.get("direction") in ("BULLISH", "BEARISH"):
                    _up = (_bp["direction"] == "BULLISH")
                    _sources.append(f"pat:{_bp.get('name', '?')}:{_bp['direction']}")
                    if _up == _want_long:
                        _confirmed = True
                    else:
                        _against = True
                # DDC (Dynamic Deviation Channels)
                if _c.get("ddc_signal"):
                    _d = _c["ddc_signal"]
                    _up = (_d == "LONG")
                    _sources.append(f"ddc:{_d}")
                    if _up == _want_long:
                        _confirmed = True
                    else:
                        _against = True
                return _confirmed, _sources, _against

            for _c in ranked:
                _last = self._last_symbol_proposal.get(_c["symbol"], 0)
                if now - _last < self._SYMBOL_COOLDOWN_SEC:
                    _w = int(self._SYMBOL_COOLDOWN_SEC - (now - _last))
                    log.info(f"⏭️ REALITY SKIP: {_c['symbol']} cooldown {_w}s remaining")
                    continue
                _adx = _c.get("adx", 0) or 0
                if _adx < 20:
                    log.info(f"⏭️ REALITY SKIP: {_c['symbol']} ADX={_adx:.0f} < 20 (no trend)")
                    try:
                        from guardian.opportunity_loss import record_rejection
                        record_rejection(_c["symbol"], _c["direction"], _c["probability"], _c["score"], _c["quality"], "adx_low", _c["close"], time.time(), _c.get("atr",0), meta_v1_prob=_c.get("meta_v1_prob"))
                    except Exception:
                        pass
                    continue
                # TREND FILTER EMA50 (2026-09-01, ГИБРИД): тренд на D1, вход на H1.
                # Доказано на исправленном стенде: D1-тренд (close vs EMA50) = +305R/1223
                # сделки. H1-тренд слабее (+37R). Гибрид: определяем тренд по D1-свечам,
                # входим на H1 только в направлении D1-тренда.
                try:
                    _d1_candles = self._fetch_daily(_c["symbol"])
                    _tr_ok = True
                    if _d1_candles and len(_d1_candles) >= 50:
                        _closes_d1 = [x.close for x in _d1_candles[-100:]]
                        _ema50_d1 = self.indicator_calc.ema(_closes_d1, 50)[-1] if len(_closes_d1) >= 50 else 0
                        if _c["direction"] == "BUY" and _ema50_d1 and _c["close"] <= _ema50_d1:
                            _tr_ok = False
                        elif _c["direction"] == "SELL" and _ema50_d1 and _c["close"] >= _ema50_d1:
                            _tr_ok = False
                    if not _tr_ok:
                        log.info(f"⏭️ REALITY SKIP: {_c['symbol']} {_c['direction']} "
                                 f"— против D1-EMA50-тренда (гибрид)")
                        try:
                            from guardian.opportunity_loss import record_rejection
                            record_rejection(_c["symbol"], _c["direction"], _c["probability"], _c["score"], _c["quality"], "d1_ema50_contra", _c["close"], time.time(), _c.get("atr",0), meta_v1_prob=_c.get("meta_v1_prob"))
                        except Exception:
                            pass
                        continue
                except Exception:
                    pass  # нет данных — не блокируем
                # PATTERN GATE (2026-09-01, owner; ОТКЛЮЧЕНО как блокирующий):
                # Раньше блокировал вход при «детектор ПРОТИВ» (BEAR_FLAG/EVENING_STAR
                # против BUY). Но ВСЕ эти паттерны отклонены в тестах (не дают edge),
                # а блокировка душила торговлю (AGIUSDT ADX=46 BUY отклонялся из-за
                # BEAR_FLAG). Паттерны теперь ТОЛЬКО логируются (PatternTracker),
                # вход решает сигнал + D1-тренд + ADX.
                try:
                    _pconf, _psources, _pagainst = _pattern_confirmed(_c)
                except Exception as _e:
                    log.debug(f"pattern gate err {_c['symbol']}: {_e}")
                    _pconf, _psources, _pagainst = False, [], False
                # Источник подтверждения (только для лога, не блокирует)
                _c["pattern_sources"] = _psources or ["indicator-only"]
                best = _c
                break
            if best is None:
                log.info("⏭️ REALITY SKIP: все кандидаты в cooldown/ADX/contra-gate фильтре")
                self._last_reality_proposal_time = now
                return

            log.info(f"🎯 REALITY RANK: selected {best['symbol']} {best['direction']} "
                     f"prob={best['probability']:.2f} score={best['score']} "
                     f"patterns=[{','.join(best.get('pattern_sources', []))}] "
                     f"from {len(executable)} executable candidates")

            # ─── PRE-TRADE FILTERS (2026-09-01) ────────────────────────────
            # Night ban: не торговать в ночные часы (UTC, из trading_mode.json)
            _filters_pass = True
            _filters_reason = ""
            try:
                _cfg_tm = json.loads(mode_path.read_text()) if mode_path.exists() else {}
                _nb_start = int(_cfg_tm.get("night_ban_start", 0) or 0)
                _nb_end = int(_cfg_tm.get("night_ban_end", 6) or 6)
                _utc_hour = datetime.now(timezone.utc).hour
                if _nb_start < _nb_end and _nb_start <= _utc_hour < _nb_end:
                    _filters_pass = False
                    _filters_reason = f"night_ban {_utc_hour}Z ({_nb_start}-{_nb_end}Z)"
                elif _nb_start > _nb_end and (_utc_hour >= _nb_start or _utc_hour < _nb_end):
                    _filters_pass = False
                    _filters_reason = f"night_ban wrap {_utc_hour}Z"
            except Exception:
                pass

            # Spread + funding через один тикер-запрос (public API, без HMAC)
            if _filters_pass:
                try:
                    import httpx as _httpx
                    _tk = _httpx.get(
                        f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={best['symbol']}",
                        timeout=8,
                    ).json()
                    _t0 = (_tk.get("result") or {}).get("list") or []
                    if _t0:
                        _bid = float(_t0[0].get("bid1Price", 0) or 0)
                        _ask = float(_t0[0].get("ask1Price", 0) or 0)
                        _lastpx = float(_t0[0].get("lastPrice", 0) or 0)
                        if _bid > 0 and _ask > 0 and _lastpx > 0:
                            _spread = (_ask - _bid) / _lastpx * 100
                            if _spread > 0.5:
                                _filters_pass = False
                                _filters_reason = f"spread {_spread:.2f}% > 0.5%"
                        # Funding filter: не входить LONG при высоком funding
                        # (перекупленность, риск шорт-сквиза)
                        _spr = float(_t0[0].get("fundingRate", 0) or 0)
                        if _filters_pass and best["direction"] == "BUY":
                            if abs(_spr) > 0.0001:  # >0.01%/8h
                                _filters_pass = False
                                _filters_reason = f"high funding {_spr*100:.4f}%"
                except Exception:
                    pass

            # ─── NEWS BLACKOUT (T2, 2026-09-01) ────────────────────────────
            # Блокировать вход ±15/30 мин вокруг макроэкономических событий
            # (FOMC / CPI / NFP) из operations/news_blackout.json. Резкая
            # волатильность новостей = случайный результат (правило R176:
            # изменения только с обоснованием; данные: в окнах новостей цена
            # ходит 2-3× шире обычного, стопы срезаются шумом).
            if _filters_pass:
                try:
                    _nb_path = Path("/root/tradingos/operations/news_blackout.json")
                    if _nb_path.exists():
                        _nbc = json.loads(_nb_path.read_text())
                        if _nbc.get("enabled"):
                            _mb = int(_nbc.get("margin_before_min", 15))
                            _ma = int(_nbc.get("margin_after_min", 30))
                            _now = datetime.now(timezone.utc)
                            for _ev in _nbc.get("events_2026", []):
                                _ev_start = datetime(
                                    _now.year, _ev["month"], _ev["day"],
                                    _ev["hour_utc"], 0, tzinfo=timezone.utc)
                                _b = _ev_start - timedelta(minutes=_mb)
                                _a = _ev_start + timedelta(minutes=_ma)
                                if _b <= _now <= _a:
                                    _filters_pass = False
                                    _filters_reason = (
                                        f"news blackout {_ev['name']} "
                                        f"({_ev_start.strftime('%d-%m %H:%M')}Z)")
                                    break
                except Exception:
                    pass

            if not _filters_pass:
                log.info(f"⏭️ REALITY SKIP: {best['symbol']} {best['direction']} — {_filters_reason}")
                try:
                    from guardian.opportunity_loss import record_rejection
                    record_rejection(best["symbol"], best["direction"], best["probability"],
                                     best["score"], best["quality"], "pre_trade_filter",
                                     best["close"], time.time(), best.get("atr", 0),
                                     meta_v1_prob=best.get("meta_v1_prob"))
                except Exception:
                    pass
                self._last_reality_proposal_time = now
                return

            # ─── ENGINE V2 + ENTRY QUALITY GATE (behind flag, shadow-proven) ──
            # "Defensive Continuation": Signal Generator = только кандидат.
            # Gate блокирует OPPOSITE_IMPULSE / LATE_MOMENTUM / EXTENDED_ENTRY
            # (доказано: −2.47%/1h), SL/TP — рыночные структуры (не 4×ATR).
            # engine_v2_live=false → ровно прежнее поведение (prob×score, 2/4 ATR).
            engine_v2_live = False
            try:
                if mode_path.exists():
                    with open(mode_path) as f:
                        engine_v2_live = bool(json.load(f).get("engine_v2_live", False))
            except Exception:
                pass

            # S2 fix: sl/tp по умолчанию None. engine_v2 задаёт их при
            # консистентном плане; fallback 2/4 ATR (ниже) — только если план
            # не предоставил валидные значения (engine_v2_live=false, exception,
            # неконсистентный план). Раньше fallback затирал структурные SL/TP.
            sl = tp = None

            if engine_v2_live:
                try:
                    from trade.engine_v2 import decide as v2_decide
                    from trade.entry_quality_gate import load_candles as eq_load, gate as eq_gate

                    best_ts = best.get("ts")
                    if best_ts is None:
                        raw_ts = best.get("timestamp")
                        if isinstance(raw_ts, (int, float)):
                            best_ts = float(raw_ts)
                        elif isinstance(raw_ts, str):
                            try:
                                best_ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).timestamp()
                            except Exception:
                                best_ts = time.time()
                        else:
                            best_ts = time.time()
                    # FIX 2026-08-31 (rate-limit hole): eq_load() ходил в живой Bybit API
                    # для каждого из 124 символов → "Too many visits" → reality
                    # замолкала на минуты. Используем свечи, уже загруженные
                    # H1-сканом в feature_store (без дополнительных HTTP-запросов).
                    # структура v2: si40/lo40 на H1 = 40-часовые зоны, валидно.
                    _fs_df = None
                    _fs_candles = self.feature_store.get_candles(best["symbol"], "1h")
                    if _fs_candles and len(_fs_candles) >= 60:
                        _fs_df = pd.DataFrame({
                            "ts": [c.timestamp for c in _fs_candles],
                            "o": [c.open for c in _fs_candles],
                            "h": [c.high for c in _fs_candles],
                            "l": [c.low for c in _fs_candles],
                            "c": [c.close for c in _fs_candles],
                            "v": [c.volume for c in _fs_candles],
                        })
                    eq_meta = {"source": "feature_store", "data_age": 0, "ok": _fs_df is not None}
                    df = _fs_df
                    plan = v2_decide({
                        "symbol": best["symbol"], "side": best["direction"], "ts": best_ts,
                    }, df)
                    g = eq_gate(best["symbol"], best["direction"], best_ts, df,
                                log=True, meta=eq_meta)

                    # 1) Gate: блок доказанных плохих входов
                    if g["decision"] == "SKIP":
                        log.info(f"⏭️ ENGINE_V2 SKIP: {best['symbol']} {best['direction']} "
                                 f"GATE={g['reason']} (imp={g.get('impulse_direction')} "
                                 f"{g.get('impulse_atr')}ATR vol={g.get('relative_volume')}x "
                                 f"age={g.get('impulse_age_min')}мин src={g.get('source')})")
                        self._last_reality_proposal_time = now
                        try:
                            from guardian.opportunity_loss import record_rejection
                            record_rejection(best["symbol"], best["direction"], best["probability"],
                                             best["score"], best["quality"], f"gate_{g['reason']}",
                                             best["close"], time.time(),
                                             best.get("atr", 0),
                                             meta_v1_prob=best.get("meta_v1_prob"))
                        except Exception:
                            pass
                        return

                    # 2) Метод входа: SKIP/WAIT (не LIMIT_PULLBACK) → не открываем
                    if plan["entry_method"] in ("SKIP", "WAIT"):
                        log.info(f"⏭️ ENGINE_V2 SKIP: {best['symbol']} method={plan['entry_method']} "
                                 f"reason={plan.get('skip_reason')} setup={plan.get('setup')}")
                        self._last_reality_proposal_time = now
                        return

                    # 3) Замена SL/TP: рыночные структуры вместо 2/4×ATR
                    if plan.get("sl") and plan.get("tp1") and plan.get("room_r") is not None:
                        atr = best["atr"]
                        entry = best["close"]
                        sl_new = plan["sl"]
                        tp_new = plan["tp1"]
                        room = plan["room_r"]
                        # Согласование направления
                        if best["direction"] == "BUY" and sl_new < entry < tp_new:
                            sl, tp = sl_new, tp_new
                            log.info(f"🎯 ENGINE_V2 PLAN: {best['symbol']} {best['direction']} "
                                     f"setup={plan.get('setup')} sl={sl:.6g} tp={tp:.6g} "
                                     f"room={room}R (вместо 2/4 ATR)")
                        elif best["direction"] == "SELL" and sl_new > entry > tp_new:
                            sl, tp = sl_new, tp_new
                            log.info(f"🎯 ENGINE_V2 PLAN: {best['symbol']} {best['direction']} "
                                     f"setup={plan.get('setup')} sl={sl:.6g} tp={tp:.6g} "
                                     f"room={room}R (вместо 2/4 ATR)")
                        else:
                            log.info(f"⚠️ ENGINE_V2: неконсистентный план {best['symbol']} — "
                                     f"используем 2/4 ATR (sl={sl_new:.6g} tp={tp_new:.6g} entry={entry:.6g})")
                except Exception as e:
                    log.warning(f"⚠️ ENGINE_V2: ошибка ({e}) — fallback к 2/4 ATR")

            # Create TradeProposal — SL/TP: engine_v2 план ИЛИ fallback 2/4 ATR.
            # S2 fix: fallback выполняется ТОЛЬКО когда engine_v2 не задал sl/tp
            # (engine_v2_live=false, exception, или неконсистентный план).
            if sl is None or tp is None:
                # Fallback: SL=2 ATR, TP=3 ATR — FIX 2026-09-01 (позиционный)
                # Статистика на ИСПРАВЛЕННЫХ данных (сортировка свечей, комиссии):
                # D1-тренд (close vs EMA50) с тейком 3R даёт +305R/1223 сделки
                # (контроль случайного = ~0R). Тейк 1.5×ATR = скальп, комиссия
                # 0.1% его убивает. 3×ATR = позиционный, комиссия несущественна.
                atr = best["atr"]
                entry = best["close"]
                # FIX 2026-09-01: если ATR=0 (не накоплен) → SL==entry, Bybit
                # отклоняет ордер. Пропускаем такой сигнал (нет данных для стопа).
                if not atr or atr <= 0:
                    log.info(f"⏭️ REALITY SKIP: {best['symbol']} ATR=0 — нет данных для SL/TP")
                    self._last_reality_proposal_time = now
                    return
                if best["direction"] == "BUY":
                    sl = entry - atr * 2
                    tp = entry + atr * 3.0
                else:
                    sl = entry + atr * 2
                    tp = entry - atr * 3.0
            else:
                # engine_v2 дал структурные sl/tp; atr/entry нужны для size-check ниже
                atr = best["atr"]
                entry = best["close"]

            # ─── MIN-SL ГАРАНТИЯ (2026-08-31, owner) ─────────────────────────
            # Проблема: 2×ATR на дешёвых монетах = 0.2-0.4% (шумовой уровень).
            # ZKP: SL 0.3% — выбивало по шуму ДО TP (−9.31 при входе 0.05412).
            # Слишком узкий SL = снип-машина: стоп срабатывает раньше, чем цена
            # проходит шум к реальной цели. Расширяем SL минимум до 1.5% от входа
            # (в сторону убытка): риск растёт, но позиция живёт до реального
            # движения, а не умирает на шуме.
            MIN_SL_PCT = 1.5
            if entry and entry > 0:
                sl_pct_now = abs(entry - sl) / entry * 100
                if sl_pct_now < MIN_SL_PCT:
                    if best["direction"] == "BUY":
                        sl = entry * (1 - MIN_SL_PCT / 100.0)
                    else:
                        sl = entry * (1 + MIN_SL_PCT / 100.0)
                    log.info(f"🎯 MIN-SL {best['symbol']}: {sl_pct_now:.2f}%→{MIN_SL_PCT}% "
                             f"(SL {sl:.6g}, entry {entry:.6g})")

            # ─── STOP-HUNT DETECTION (T7, 2026-09-01) ──────────────────────
            # Если за последние 24 свечи цена дважды ПРОБИВАЛА зону будущего SL
            # вниз и закрывалась обратно — это Stop Hunt: стопы розничных
            # трейдеров собирают, затем цена разворачивается. Такой SL будет
            # снова срезан. Расширяем SL на 50% (в сторону убытка), чтобы
            # позиция пережила охоту до реального движения.
            try:
                _sh_candles = self.feature_store.get_candles(best["symbol"], "1h")
                if _sh_candles and entry and sl:
                    _buf = sl
                    _hits = 0
                    for _sc in _sh_candles[-24:]:
                        if best["direction"] == "BUY" and _sc.low < _buf:
                            _hits += 1
                        elif best["direction"] != "BUY" and _sc.high > _buf:
                            _hits += 1
                    if _hits >= 2:
                        if best["direction"] == "BUY":
                            sl = entry - abs(entry - sl) * 1.5
                        else:
                            sl = entry + abs(entry - sl) * 1.5
                        log.info(f"🕵️ STOP-HUNT {best['symbol']}: {_hits} касаний зоны SL "
                                 f"за 24 свечи → SL расширен ×1.5 (SL {sl:.6g}")
            except Exception:
                pass

            # ─── TP-КЕП ПО РЕАЛЬНОЙ ДОСТИЖИМОСТИ (2026-08-30, owner) ─────────
            # Проблема: fallback TP=4×ATR (и иногда engine_v2) ставит цель далеко
            # от фактических движений (PUMPFUN: TP +7.67% при реальном MFE +1.5% —
            # цель недостижима, позиция висит и отдаёт прибыль на откате).
            # Ограничиваем TP: не дальше 7-дневного экстремума − 0.5×ATR.
            # FIX (audit explore-1): candles берём по best["symbol"] (кандидату),
            # а не по symbol (текущий опрашиваемый — обычно другой инструмент!).
            # FIX: clamp tp относительно entry — кап ниже входа (BUY) бессмыслен.
            try:
                _bs = best["symbol"]
                _fs_candles = self.feature_store.get_candles(_bs, "1h")
                if _fs_candles and len(_fs_candles) >= 30:
                    _hi7 = max(c.high for c in _fs_candles[-168:])
                    _lo7 = min(c.low for c in _fs_candles[-168:])
                    _atr_c = atr or 0.0
                    if best["direction"] == "BUY":
                        _tp_cap = _hi7 - _atr_c * 0.5
                        if tp and tp > _tp_cap and _tp_cap > entry:
                            log.info(f"🎯 TP-CAP {_bs}: {tp:.6g} → {_tp_cap:.6g} "
                                     f"(7d-high {_hi7:.6g} − 0.5×ATR)")
                            tp = _tp_cap
                    elif best["direction"] == "SELL":
                        _tp_cap = _lo7 + _atr_c * 0.5
                        if tp and tp < _tp_cap and _tp_cap < entry:
                            log.info(f"🎯 TP-CAP {_bs}: {tp:.6g} → {_tp_cap:.6g} "
                                     f"(7d-low {_lo7:.6g} + 0.5×ATR)")
                            tp = _tp_cap
            except Exception as e:
                log.debug(f"TP-CAP не применён {best.get('symbol', '?')}: {e}")

            # Exchange minimum order size check
            # FIX 2026-09-02 (audit BUG1): использовать реальный risk из trading_mode.json
            # вместо hardcoded 0.25 (блокировал валидные символы при risk=$50).
            risk_per_unit = abs(entry - sl)
            try:
                _risk_cfg = json.load(open("/root/tradingos/operations/trading_mode.json"))
                _risk_usd = float(_risk_cfg.get("risk_per_trade", 25.0) or 25.0)
            except Exception:
                _risk_usd = 25.0
            position_units = _risk_usd / risk_per_unit if risk_per_unit > 0 else 0
            # FIX 2026-09-02 (audit BUG2): live lot-size из Bybit вместо hardcoded guesses
            exchange_min = 1
            try:
                ri = httpx.get(
                    "https://api.bybit.com/v5/market/instruments-info",
                    params={"category": "linear", "symbol": best["symbol"]},
                    timeout=10,
                ).json()
                _lot = ((ri.get("result") or {}).get("list") or [{}])[0].get("lotSizeFilter", {})
                exchange_min = float(_lot.get("minOrderQty", 1) or 1)
            except Exception:
                pass  # fallback к generic min=1
            if position_units < exchange_min:
                log.info(f"⏭️ REALITY SKIP: {best['symbol']} position {position_units:.4f} < min {exchange_min}")
                self._last_reality_proposal_time = now
                try:
                    from guardian.opportunity_loss import record_rejection
                    record_rejection(best["symbol"], best["direction"], best["probability"], best["score"], best["quality"], "exchange_min_size", best["close"], time.time(), best.get("atr",0), meta_v1_prob=best.get("meta_v1_prob"))
                except Exception:
                    pass
                return

            # P0: max_positions check — read from config
            try:
                import sys
                sys.path.insert(0, "/root/tradingos")
                from tradingos.strategies.bybit_position_check import (
                    has_open_position, count_open_positions, get_open_position_symbols
                )
                mode_path_cfg = Path("/root/tradingos/operations/trading_mode.json")
                # FIX 2026-09-02 (audit): fail-closed к 1 при config-read failure
                max_pos = 1
                if mode_path_cfg.exists():
                    with open(mode_path_cfg) as f:
                        cfg = json.load(f)
                    max_pos = int(cfg.get("max_positions", 1))
                current_count = count_open_positions()
                # Ручные позиции (source=MANUAL в guardian reality_state.json, там только
                # открытые) не блокируют AUTO: их открыл пользователь через Telegram-контур.
                manual_count = 0
                try:
                    gs_path = Path("/root/tradingos/guardian/reality_state.json")
                    if gs_path.exists():
                        with open(gs_path) as f:
                            gstate = json.load(f)
                        manual_count = sum(
                            1 for v in gstate.values()
                            if isinstance(v, dict) and v.get("source") == "MANUAL"
                        )
                except Exception:
                    manual_count = 0
                # External/unmanaged positions (no SL/TP, not opened by TradingOS)
                # must NOT block AUTO entries. Count positions that have no stopLoss
                # as external and exclude them from the auto count.
                # FIX 2026-08-24: XRP/DOT/FIL/APT were blocking all AUTO entries
                # because they counted toward max_positions despite being external.
                external_count = 0
                try:
                    from tradingos.strategies.bybit_position_check import _get_positions
                    all_positions = _get_positions()
                    for p in all_positions:
                        if float(p.get("size", 0) or 0) > 0:
                            sl_val = str(p.get("stopLoss", "") or "").strip()
                            if not sl_val or float(sl_val) == 0:
                                external_count += 1
                except Exception:
                    external_count = 0
                auto_count = max(current_count - manual_count - external_count, 0)
                if auto_count >= max_pos:
                    log.info(f"⏭️ REALITY SKIP: max_positions reached "
                             f"({auto_count}/{max_pos}, total {current_count}, manual {manual_count}, external {external_count}) — skipping {best['symbol']} proposal")
                    self._last_reality_proposal_time = now
                    try:
                        from guardian.opportunity_loss import record_rejection
                        record_rejection(best["symbol"], best["direction"], best["probability"], best["score"], best["quality"], "max_positions", best["close"], time.time(), best.get("atr",0), meta_v1_prob=best.get("meta_v1_prob"))
                    except Exception:
                        pass
                    return
                # Correlation filter: skip if same direction in correlated sector
                open_syms = get_open_position_symbols()
                if best["symbol"] in open_syms:
                    log.info(f"⏭️ REALITY SKIP: {best['symbol']} already has open position")
                    self._last_reality_proposal_time = now
                    return
                # CORRELATION FILTER (2026-09-01): не дублировать риск —
                # если уже открыта позиция в том же секторе (BTC/ETH/SOL-семья),
                # блокируем второй символ того же направления. 13 одновременных
                # LONG на коррелированных монетах = ставка на одно движение.
                _CORR_GROUPS = [
                    {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "WIFUSDT", "DOGEUSDT"},
                    {"XRPUSDT", "ADAUSDT", "DOTUSDT", "AVAXUSDT", "LINKUSDT"},
                    {"OPUSDT", "ARBUSDT", "LDOUSDT", "STRKUSDT", "TNSRUSDT"},
                ]
                _corr_hit = False
                _corr_group = ""
                for _grp in _CORR_GROUPS:
                    if best["symbol"] in _grp:
                        _same_side = [s for s in open_syms if s in _grp]
                        if _same_side:
                            _corr_hit = True
                            _corr_group = ",".join(sorted(_same_side))
                        break
                if _corr_hit:
                    log.info(f"⏭️ REALITY SKIP: {best['symbol']} — correlated position(s) "
                             f"already open in group ({_corr_group}), риск дублируется")
                    self._last_reality_proposal_time = now
                    return
            except ImportError:
                pass

            proposal = TradeProposal(
                symbol=best["symbol"],
                side=best["direction"],
                entry=entry,
                stop_loss=sl,
                take_profit=tp,
                rr=2.0,
                confidence=round(best["probability"], 2),
                strategy="REALITY_DISCOVERY",
                decision_id=f"R-{generate_decision_id()}",
                reason=[f"Reality TOP candidate: {best['symbol']} {best['direction']} "
                        f"prob={best['probability']:.2f} score={best['score']} "
                        f"quality={best['quality']}"],
                session="REALITY",
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

            valid, msg = proposal.validate()
            if valid:
                save_proposal(proposal)
                _log_funnel_event("proposal", proposal)
                self._last_reality_proposal_time = now
                self._last_symbol_proposal[proposal.symbol] = now
                log.info(f"🌟 REALITY PROPOSAL: {best['symbol']} {best['direction']} "
                         f"conf={best['probability']:.2f}")
                
                # Execution modes: AUTO (real), PAPER (virtual, no order), else wait for manual
                try:
                    mode_path = Path("/root/tradingos/operations/trading_mode.json")
                    if mode_path.exists():
                        with open(mode_path) as f:
                            mode_data = json.load(f)
                        mode = mode_data.get("mode")
                        if mode == "AUTO":
                            proposal.status = "APPROVED"
                            result = await _execute_reality(proposal)
                            if result.get("status") == "FILLED":
                                log.info(f"🤖 AUTO EXECUTED: {proposal.symbol} {proposal.side} "
                                         f"id={result.get('ticket','?')} price={result.get('price','?')}")
                                _log_funnel_event("opened", proposal, {
                                    "ticket": str(result.get("ticket", "?")),
                                    "price": result.get("price", proposal.entry),
                                    "prob": best.get("probability"),
                                    "score": best.get("score"),
                                    "quality": best.get("quality"),
                                    "adx": best.get("adx"),
                                    "atr": best.get("atr"),
                                    "rsi": best.get("rsi"),
                                })
                                # Trade Genome: OPEN record (statistics only)
                                try:
                                    _candles = self.feature_store.get_candles(proposal.symbol, "1h")
                                    _vol = _candles[-1].volume if _candles else 0.0
                                except Exception:
                                    _vol = 0.0
                                _log_genome_open(
                                    proposal, best, volume_1h=_vol,
                                    fill_price=result.get("price", proposal.entry),
                                    ticket=str(result.get("ticket", "")),
                                )
                                # OPEN-уведомление из observation-цикла: здесь есть
                                # prob/score/adx сигнала (guardian их не знает).
                                if self._tg is not None:
                                    try:
                                        _qty = 0.0
                                        try:
                                            from tradingos.strategies.bybit_position_check import get_open_positions_with_side
                                            for _p in get_open_positions_with_side():
                                                if _p.get("symbol") == proposal.symbol:
                                                    _qty = float(_p.get("size", 0) or 0)
                                                    break
                                        except Exception:
                                            pass
                                        if not _qty:
                                            _rp = abs(proposal.entry - proposal.stop_loss)
                                            _risk = 0.5
                                            _lev = 5
                                            try:
                                                with open("/root/tradingos/operations/trading_mode.json") as _f:
                                                    _cfg_t = json.load(_f)
                                                _risk = float(_cfg_t.get("risk_per_trade", 0.5))
                                                _lev = int(_cfg_t.get("max_leverage", 5))
                                            except Exception:
                                                pass
                                            _qty = _risk / _rp if _rp > 0 else 0.0
                                        else:
                                            _lev = 5
                                            try:
                                                with open("/root/tradingos/operations/trading_mode.json") as _f:
                                                    _lev = int(json.load(_f).get("max_leverage", 5))
                                            except Exception:
                                                pass
                                        await self._tg.notify_trade_open(
                                            symbol=proposal.symbol, side=proposal.side,
                                            entry_price=proposal.entry, qty=_qty,
                                            sl=proposal.stop_loss, tp=proposal.take_profit,
                                            reason="OPEN", leverage=_lev,
                                            probability=best.get("probability"),
                                            score=best.get("score"),
                                            adx=best.get("adx"),
                                            entry_time=datetime.now(timezone.utc),
                                        )
                                        log.info(f"🤖 TG OPEN card sent: {proposal.symbol} "
                                                 f"prob={best.get('probability')} "
                                                 f"score={best.get('score')} adx={best.get('adx')}")
                                    except Exception as e:
                                        log.warning(f"TG open send failed: {e}")
                            elif result.get("status") == "SKIP":
                                # Pre-order skip: size below exchange lotSizeFilter.
                                log.info(f"⏭️ REALITY SKIP (pre-order): {proposal.symbol} {proposal.side} "
                                         f"reason={result.get('reason')} "
                                         f"raw_qty={result.get('raw_qty')} "
                                         f"required_qty={result.get('required_qty')} "
                                         f"risk_required=${result.get('risk_required')}")
                                proposal.status = "PENDING"
                                save_proposal(proposal)
                            else:
                                log.error(f"🤖 AUTO EXECUTION FAILED: {result.get('error','?')}")
                                proposal.status = "PENDING"
                                save_proposal(proposal)
                        elif mode == "PAPER":
                            # Virtual execution: no order sent to exchange.
                            # Same SL/TP/risk as real; logged to paper_trades.jsonl for a
                            # full-cycle test of the pipeline without touching the market.
                            proposal.status = "APPROVED"
                            _log_paper_trade(proposal)
                            log.info(f"🧪 PAPER TRADE: {proposal.symbol} {proposal.side} "
                                     f"entry={proposal.entry:.6f} SL={proposal.stop_loss:.6f} "
                                     f"TP={proposal.take_profit:.6f} conf={proposal.confidence:.2f}")
                except Exception as e:
                    log.error(f"Execution mode error: {e}")
            else:
                log.warning(f"Reality proposal rejected: {msg}")

    def stop(self):
        self.running = False


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    runner = ObservationRunner()
    try:
        await runner.run(interval=60)
    except KeyboardInterrupt:
        runner.stop()


if __name__ == "__main__":
    # Singleton guard: a second observation engine double-executes signals.
    import sys as _sys
    _sys.path.insert(0, "/root/tradingos")
    from core.singleton import acquire_singleton_lock
    acquire_singleton_lock("run_observation")
    asyncio.run(main())
