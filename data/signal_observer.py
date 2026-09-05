#!/usr/bin/env python3
"""
Signal Observer — сбор статистики сигналов без торговли.

Запускается параллельно с OHLCVPoller.
Читает FeatureStore, вызывает SignalGenerator, записывает результаты.

Формат:

/root/tradingos/memory/signal_log.jsonl

Каждая строка:

{
  "timestamp": "...",
  "symbol": "DOGEUSDT",
  "direction": "BUY"|"SELL"|"NONE",
  "confidence": 0.0,
  "score": 0,
  "reject_reason": "...",
  "features_rsi": 45.2,
  "features_adx": 22.0,
  "features_ema20": 0.072,
  "features_close": 0.073
}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from tradingos.data.feature_store import FeatureStore
from tradingos.data.indicators import IndicatorCalculator
from tradingos.signals.feature_vector import FeatureVector
from tradingos.signals.signal_generator import SignalGenerator

log = logging.getLogger("signal_observer")

SIGNAL_LOG = Path("/root/tradingos/memory/signal_log.jsonl")
SYMBOLS = ["BTCUSDT", "ETHUSDT", "DOGEUSDT"]
INTERVAL = "1m"
POLL_INTERVAL = 60  # секунд между проверками


class SignalObserver:
    """Наблюдатель за сигналами. Не торгует. Только записывает."""

    def __init__(self):
        self.feature_store = FeatureStore()
        self.indicator_calc = IndicatorCalculator()
        self.generators: dict[str, SignalGenerator] = {}
        self.stats = {
            "checks": 0,
            "signals": 0,
            "buy": 0,
            "sell": 0,
            "rejected": 0,
            "by_symbol": {},
        }
        self._last_log_count = 0

    def _build_fv(self, symbol: str) -> FeatureVector | None:
        """Построить FeatureVector из текущих данных FeatureStore."""
        candles = self.feature_store.get_candles(symbol, INTERVAL)
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
            [c.volume for c in candles], 20
        ) if len(candles) >= 20 else 1.0

        return FeatureVector(
            timestamp_ms=last.timestamp,
            symbol=symbol,
            open=last.open, high=last.high, low=last.low, close=last.close,
            volume=last.volume,
            ema20=ema20, ema50=ema50, ema200=ema200 if ema200 else 0.0,
            rsi=rsi,
            macd_line=macd.get("macd", 0.0),
            macd_signal=macd.get("signal", 0.0),
            atr=atr,
            bb_upper=bb.get("upper", 0.0),
            bb_lower=bb.get("lower", 0.0),
            bb_middle=bb.get("middle", 0.0),
            adx=adx_val,
            volume_ma=0.0,
            volume_ratio=vol_ratio,
            obv=0.0,
            vwap=vwap,
            ema_bullish=ema20 > ema50 if ema20 > 0 and ema50 > 0 else False,
            price_above_ema50=last.close > ema50 if ema50 > 0 else False,
            rsi_overbought=rsi > 70,
            rsi_oversold=rsi < 30,
            htf_ema50=None, htf_ema200=None, htf_trend=None,
            integrity_score=1.0,
        )

    def _get_reject_reason(self, fv: FeatureVector, sg: SignalGenerator) -> str:
        """Определить причину reject по stats."""
        s = sg.stats
        if s.get("rejected_probability", 0) > 0:
            return "probability_below_threshold"
        if s.get("rejected_no_score", 0) > 0:
            return "no_score"
        if s.get("signals_total", 0) == 0 and s["total_evaluated"] > 0:
            return "no_signal"
        return "unknown"

    def check(self, symbol: str) -> dict:
        """Проверить один символ. Вернуть результат."""
        fv = self._build_fv(symbol)
        if fv is None:
            return {"symbol": symbol, "direction": "NONE", "reason": "insufficient_data"}

        if symbol not in self.generators:
            self.generators[symbol] = SignalGenerator()
        sg = self.generators[symbol]

        direction = sg.decide(symbol, fv, bar_idx=0)
        s = sg.stats

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "direction": direction or "NONE",
            "confidence": getattr(fv, 'integrity_score', 0.0),
            "score": s.get("signals_total", 0),
            "reject_reason": self._get_reject_reason(fv, sg) if direction is None else "",
            "features_rsi": round(fv.rsi, 1),
            "features_adx": round(fv.adx, 1),
            "features_ema20": round(fv.ema20, 4),
            "features_close": round(fv.close, 4),
        }

        if direction:
            self.stats["signals"] += 1
            self.stats["buy" if direction == "BUY" else "sell"] += 1
        else:
            self.stats["rejected"] += 1

        self.stats["checks"] += 1
        return result

    def log_signal(self, result: dict):
        """Записать в signal_log.jsonl."""
        SIGNAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(SIGNAL_LOG, "a") as f:
            f.write(json.dumps(result, ensure_ascii=False) + "\n")

    def print_summary(self):
        """Вывести текущую статистику."""
        total = self.stats["signals"] + self.stats["rejected"]
        print(f"\n{'='*50}")
        print(f"SIGNAL OBSERVER — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"{'='*50}")
        print(f"  Checks:       {self.stats['checks']}")
        print(f"  Signals:      {self.stats['signals']} ({self.stats['buy']} BUY / {self.stats['sell']} SELL)")
        print(f"  Rejected:     {self.stats['rejected']}")
        print(f"  Accept rate:  {(self.stats['signals']/max(total,1)*100):.1f}%")
        print(f"  Log entries:  {self._count_log()}")
        print()

    def _count_log(self) -> int:
        if SIGNAL_LOG.exists():
            return sum(1 for _ in open(SIGNAL_LOG))
        return 0


async def main():
    logging.basicConfig(level=logging.WARNING)
    obs = SignalObserver()

    log.info("Signal Observer started — no trading, only observation")

    while True:
        for symbol in SYMBOLS:
            try:
                # Копируем данные из общего FeatureStore (OHLCVPoller записывает в файл)
                # Пока используем независимый FeatureStore для наблюдения
                # В будущем: общая data layer
                result = obs.check(symbol)
                obs.log_signal(result)

                if result["direction"] != "NONE":
                    log.info(f"SIGNAL: {result['symbol']} {result['direction']} "
                             f"RSI={result['features_rsi']} ADX={result['features_adx']}")

            except Exception as e:
                log.error(f"check({symbol}): {e}")

        obs.print_summary()
        await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
