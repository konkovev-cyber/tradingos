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
from datetime import datetime, timezone
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
        self._SYMBOL_COOLDOWN_SEC = 3600  # 1h between same-symbol proposals
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
            if (prob_for_cand >= 0.55
                    and quality_for_cand in ("MEDIUM", "GOOD", "EXCELLENT")
                    and total_for_cand >= 55):
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
                })
                log.info(f"📌 REALITY CANDIDATE: {symbol} {direction} "
                         f"prob={prob_for_cand:.2f} score={total_for_cand}")

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
        # REALITY MODE: collect candidates when decide() returns None
        # but SignalScore has prob >= 0.48 and quality >= MEDIUM
        # (threshold lowered 2026-08-03 from 0.55 — market rarely reaches 0.55
        #  on 1m candles → zero trades for 9h. 0.48 matches BingX config.)
        # ---------------------------------------------------------------------
        if direction is None and last_score is not None:
            if (last_score.final_probability >= 0.55
                    and last_score.quality in ("MEDIUM", "GOOD", "EXCELLENT")
                    and last_score.total_score >= 55):
                self._reality_candidates.append({
                    "symbol": symbol,
                    "direction": last_score.direction,
                    "probability": last_score.final_probability,
                    "score": last_score.total_score,
                    "quality": last_score.quality,
                    "close": fv.close,
                    "atr": fv.atr,
                    "rsi": fv.rsi,
                    "adx": fv.adx,
                    "timestamp": time.time(),
                })
                log.info(f"📌 REALITY CANDIDATE: {symbol} {last_score.direction} "
                         f"prob={last_score.final_probability:.2f} score={last_score.total_score}")

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
                        record_rejection(c["symbol"], c.get("direction","NONE"), c["probability"], c["score"], c["quality"], "no_direction", c["close"], c["timestamp"], c.get("atr",0))
                except Exception:
                    pass
                return

            # Rank by probability * score
            best = max(executable, key=lambda c: c["probability"] * c["score"])

            # ─── SYMBOL COOLDOWN: skip if same symbol proposed recently ────
            last_for_sym = self._last_symbol_proposal.get(best["symbol"], 0)
            if now - last_for_sym < self._SYMBOL_COOLDOWN_SEC:
                wait = int(self._SYMBOL_COOLDOWN_SEC - (now - last_for_sym))
                log.info(f"⏭️ REALITY SKIP: {best['symbol']} cooldown {wait}s remaining")
                self._last_reality_proposal_time = now
                return

            # ─── ADX FILTER: skip if no trend (ADX < 20) ───────────────────
            # Reason: ADX < 20 = market is ranging, trend signals are noise.
            # Implemented 2026-08-06 — prevents low-quality chop entries.
            adx_val = best.get("adx", 0) or 0
            if adx_val < 20:
                log.info(f"⏭️ REALITY SKIP: {best['symbol']} ADX={adx_val:.0f} < 20 (no trend)")
                self._last_reality_proposal_time = now
                try:
                    from guardian.opportunity_loss import record_rejection
                    record_rejection(best["symbol"], best["direction"], best["probability"], best["score"], best["quality"], "adx_low", best["close"], time.time(), best.get("atr",0))
                except Exception:
                    pass
                return

            log.info(f"🎯 REALITY RANK: selected {best['symbol']} {best['direction']} "
                     f"prob={best['probability']:.2f} score={best['score']} from {len(executable)} executable candidates")

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
                    # Свежий data path: кэш stale/нет → живой Bybit API (P1-fix)
                    df, eq_meta = eq_load(best["symbol"])
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
                                             best["close"], time.time(), best.get("atr", 0))
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

            # Create TradeProposal (2:1 RR: SL=2 ATR, TP=4 ATR)
            atr = best["atr"]
            entry = best["close"]
            if best["direction"] == "BUY":
                sl = entry - atr * 2
                tp = entry + atr * 4
            else:
                sl = entry + atr * 2
                tp = entry - atr * 4

            # Exchange minimum order size check
            risk_per_unit = abs(entry - sl)
            position_units = 0.25 / risk_per_unit if risk_per_unit > 0 else 0
            # Generic min_qty: most USDT perpetuals have min 1 unit
            # High-value symbols (BTC, ETH, BNB) have smaller min qty
            exchange_min = 1
            if best["symbol"] in ("BTCUSDT",):
                exchange_min = 0.001
            elif best["symbol"] in ("ETHUSDT", "BNBUSDT"):
                exchange_min = 0.01
            elif best["symbol"] in ("SOLUSDT",):
                exchange_min = 0.1
            if position_units < exchange_min:
                log.info(f"⏭️ REALITY SKIP: {best['symbol']} position {position_units:.4f} < min {exchange_min}")
                self._last_reality_proposal_time = now
                try:
                    from guardian.opportunity_loss import record_rejection
                    record_rejection(best["symbol"], best["direction"], best["probability"], best["score"], best["quality"], "exchange_min_size", best["close"], time.time(), best.get("atr",0))
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
                auto_count = max(current_count - manual_count, 0)
                if auto_count >= max_pos:
                    log.info(f"⏭️ REALITY SKIP: max_positions reached "
                             f"({auto_count}/{max_pos}, total {current_count}, manual {manual_count}) — skipping {best['symbol']} proposal")
                    self._last_reality_proposal_time = now
                    try:
                        from guardian.opportunity_loss import record_rejection
                        record_rejection(best["symbol"], best["direction"], best["probability"], best["score"], best["quality"], "max_positions", best["close"], time.time(), best.get("atr",0))
                    except Exception:
                        pass
                    return
                # Correlation filter: skip if same direction in correlated sector
                open_syms = get_open_position_symbols()
                if best["symbol"] in open_syms:
                    log.info(f"⏭️ REALITY SKIP: {best['symbol']} already has open position")
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
    asyncio.run(main())
