"""
Signal Generator v8.0 — Чистая логика без вычислений.

v8.0 BREAKING CHANGE:
- Принимает FeatureVector (immutable) вместо dict
- ВСЕ индикаторы уже precomputed в FeatureStore
- Ноль pandas, ноль ewm, ноль rolling внутри SignalGenerator
- Только логика принятия решений
- Интегрирован SignalDiagnostic для анализа WHY

Архитектура:
FeatureStore.precompute() → HTFCollector.join() → FeatureVector
    ↓
SignalGenerator.decide(FeatureVector) → "BUY" | "SELL" | None
    ↓
SignalScoringEngine.calculate(FeatureVector) → SignalScore
"""

import logging
import time
from typing import Optional, List, Dict, Any

from tradingos.signals.feature_vector import FeatureVector

from tradingos.signals.signal_scoring import SignalScoringEngine, SignalScore
from tradingos.signals.signal_types import H1Filter, H4Filter, Setup, Trigger
from tradingos.signals.signal_diagnostic import SignalDiagnostic, DiagnosticReport, TradeSnapshot

log = logging.getLogger("SignalGenerator")


class SignalGenerator:
    """v8.0 — Чистая логика. Ноль вычислений индикаторов."""
    
    CONFIG = {
        # LAYER 1
        "require_htf_alignment": True,
        "min_trend_score": 10,
        
        # LAYER 2 — УЖЕСТОЧЕНИЕ 2026-09-01
        "rsi_oversold": 35,        # 2026-09-01: возвращено (было 30 — перекрут)
        "rsi_overbought": 65,      # 2026-09-01: возвращено (было 70)
        "ema_proximity_pct": 0.01,  # 2026-09-01: возвращено 1% (было 0.5%)
        
        # LAYER 3 — УЖЕСТОЧЕНИЕ 2026-09-01
        "volume_spike_ratio": 1.3,   # 2026-09-01: возвращено (было 2.0 — перекрут, душил)
        "momentum_threshold": 0.003,  # 2026-09-01: возвращено 0.3% (было 0.8%)
        
        # COOLDOWN
        "cooldown_after_signal": 300,
        "cooldown_after_reject": 60,
        
        # PROBABILITY — УЖЕСТОЧЕНИЕ 2026-09-01
        "min_probability_threshold": 0.55,  # 2026-09-01: было 0.70 — недостижимо моделью (max 0.66), глушило ВСЕ сигналы
    }
    
    def __init__(self, htf_collector: 'HTFCollector' = None):
        self.htf = htf_collector
        self.scoring_engine = SignalScoringEngine()
        self.diagnostic = SignalDiagnostic()
        
        # Cooldown (bar-timestamp based for backtest compatibility)
        self.last_signal_bar = {}  # symbol -> bar_idx
        self.last_reject_bar = {}
        
        # Statistics
        self.stats = {
            "signals_total": 0,
            "buy_signals": 0,
            "sell_signals": 0,
            "rejected_htf": 0,
            "rejected_setup": 0,
            "rejected_trigger": 0,
            "rejected_cooldown": 0,
            "rejected_probability": 0,
            # Diagnostic counters
            "total_evaluated": 0,
            "rejected_trend_low": 0,
            "rejected_rsi": 0,
            "rejected_volume": 0,
            "rejected_pattern": 0,
        }
        
        # Diagnostic: reject reasons
        self.reject_reasons: List[Dict[str, Any]] = []
    
    def decide(self, symbol: str, fv: FeatureVector, bar_idx: int = 0) -> Optional[str]:
        """
        Determine trade direction from a FeatureVector.
        
        v8.0: Pure logic — NO indicator calculations.
        
        Args:
            symbol: Trading pair
            fv: Precomputed feature vector
            bar_idx: Current bar index (for cooldown tracking)
            
        Returns:
            "BUY" | "SELL" | None
        """
        if not fv:
            return None
        
        self.stats["total_evaluated"] += 1
        
        # ==================== LAYER 1: DERIVE 1H FILTER ====================
        h1 = self._derive_h1_filter(fv)
        
        # ==================== LAYER 1: DERIVE 4H FILTER ====================
        h4 = self._derive_h4_filter(bar_idx)
        
        # ==================== LAYER 2: SETUP ====================
        setup = self._derive_setup(fv, h1)
        
        # ==================== LAYER 3: TRIGGER ====================
        trigger = self._derive_trigger(fv, setup)
        
        # ==================== DIRECTION ====================
        direction = self._determine_direction(h1, h4)
        if not direction:
            return None
        
        # ==================== SCORING ====================
        signal_score = self.scoring_engine.calculate_with_vectors(
            symbol=symbol,
            h1_filter=h1,
            h4_filter=h4,
            setup=setup,
            trigger=trigger,
            fv=fv,
            direction=direction,
            bar_idx=bar_idx,
        )
        
        if not signal_score:
            self._record_reject(symbol, fv, direction, signal_score, h1, h4, setup, trigger, bar_idx)
            return None
        
        if signal_score.final_probability < signal_score.threshold:
            self._record_reject(symbol, fv, direction, signal_score, h1, h4, setup, trigger, bar_idx)
            self.stats["rejected_probability"] += 1
            return None
        
        # ==================== REGISTER ====================
        self._register_signal(symbol, direction, bar_idx)
        
        log.info(
            f"🎯 {symbol} {direction}: "
            f"Score={signal_score.total_score:.1f} ({signal_score.quality}) | "
            f"Trend={signal_score.trend_score:.1f} "
            f"Setup={signal_score.setup_score:.1f} "
            f"Trigger={signal_score.trigger_score:.1f} | "
            f"Prob: {signal_score.base_probability:.2f} → {signal_score.final_probability:.2f}"
        )
        
        return direction
    
    def analyze(
        self,
        symbol: str,
        fv: FeatureVector,
        bar_idx: int = 0,
        expected_tp: float = 0.0,
        expected_sl: float = 0.0,
        expected_rr: float = 0.0,
    ) -> Optional[TradeSnapshot]:
        """
        Полный анализ одного бэктест-бара с TradeSnapshot.
        
        Возвращает TradeSnapshot только если есть сигнал.
        """
        if not fv:
            return None
        
        h1 = self._derive_h1_filter(fv)
        h4 = self._derive_h4_filter(bar_idx)
        setup = self._derive_setup(fv, h1)
        trigger = self._derive_trigger(fv, setup)
        
        direction = self._determine_direction(h1, h4)
        if not direction:
            return None
        
        signal_score = self.scoring_engine.calculate_with_vectors(
            symbol=symbol,
            h1_filter=h1,
            h4_filter=h4,
            setup=setup,
            trigger=trigger,
            fv=fv,
            direction=direction,
            bar_idx=bar_idx,
        )
        
        if not signal_score or signal_score.final_probability < signal_score.threshold:
            return None
        
        return TradeSnapshot.from_diagnostic(
            fv=fv, h4_filter=h4, h1_filter=h1,
            setup=setup, trigger=trigger,
            score=signal_score,
            signal_side=direction,
            expected_tp=expected_tp, expected_sl=expected_sl,
            expected_rr=expected_rr,
        )
    
    # ==================== LAYER 1: DERIVE FILTERS ====================
    
    def _derive_h1_filter(self, fv: FeatureVector) -> H1Filter:
        """Derive 1H context directly from FeatureVector."""
        momentum = (fv.close - fv.open) / fv.open if fv.open > 0 else 0
        trend_up = fv.ema_bullish
        # Третье состояние: боковик (ema20≈ema50) или отсутствие данных ≠ bearish
        e20, e50 = fv.ema20, fv.ema50
        if not (e20 > 0 and e50 > 0):
            trend_state = "NEUTRAL"  # отсутствие данных
        elif e20 > e50:
            trend_state = "BULLISH"
        elif e20 < e50:
            trend_state = "BEARISH"
        else:
            trend_state = "NEUTRAL"  # ema20 == ema50 (flat)

        # Trend score: ADX + EMA alignment
        adx_score = min(fv.adx / 25.0, 1.0) if fv.adx > 0 else 0.0
        ema_score = 1.0 if trend_state == "BULLISH" else 0.0
        trend_score = (adx_score * 0.5 + ema_score * 0.5)

        # Alignment (трёхзначный, а не бинарный BULLISH/BEARISH)
        alignment = trend_state

        return H1Filter(
            ema20=fv.ema20,
            ema50=fv.ema50,
            rsi=fv.rsi,
            adx=fv.adx,
            volume_ratio=fv.volume_ratio,
            trend_up=trend_up,
            price=fv.close,
            momentum=momentum,
            trend_score=trend_score,
            alignment=alignment,
            aligned=trend_up,
        )
    
    def _derive_h4_filter(self, bar_idx: int = 0):
        """Derive 4H context from HTFCollector."""
        if not self.htf:
            return H4Filter()
        
        ctx = self.htf.get_4h_context(bar_idx)
        if not ctx:
            return H4Filter()
        
        trend_up = ctx["trend_up"]
        return H4Filter(
            ema50=ctx["ema50"],
            ema20=ctx["ema20"],
            trend_up=trend_up,
            score=1.0 if trend_up else 0.0,
            aligned=trend_up,
        )
    
    # ==================== LAYER 2: SETUP ====================
    
    def _derive_setup(self, fv: FeatureVector, h1: H1Filter) -> Setup:
        """Derive setup context from FeatureVector."""
        price = fv.close
        
        # Distance to EMAs
        dist_ema20 = abs(price - h1.ema20) / h1.ema20 if h1.ema20 > 0 else 999
        dist_ema50 = abs(price - h1.ema50) / h1.ema50 if h1.ema50 > 0 else 999
        nearest_dist = min(dist_ema20, dist_ema50)
        
        # Direction: three-state. Sideways (ema20≈ema50) → NEUTRAL, никогда
        # не default в SELL. Setup direction не должен противоречить trend_up.
        if h1.trend_up:
            direction = "BUY"
        elif h1.ema20 > 0 and h1.ema50 > 0 and h1.ema20 < h1.ema50:
            direction = "SELL"
        else:
            direction = "NEUTRAL"
        
        # Pullback: price within 1% of nearest EMA
        is_pullback = nearest_dist < self.CONFIG["ema_proximity_pct"]
        
        # Score: 0-1 based on pullback quality
        score = 0.0
        if is_pullback:
            score = 1.0 - (nearest_dist / 0.05)  # 0-1 scale
        score = max(0.0, min(1.0, score))
        
        return Setup(
            direction=direction,
            ema_distance_pct=nearest_dist * 100,
            rsi=fv.rsi,
            volume_ratio=fv.volume_ratio,
            is_pullback=is_pullback,
            score=score,
        )
    
    # ==================== LAYER 3: TRIGGER ====================
    
    def _derive_trigger(self, fv: FeatureVector, setup: Setup) -> Trigger:
        """Derive trigger context from FeatureVector."""
        volume_ratio = fv.volume_ratio
        momentum = (fv.close - fv.open) / fv.open if fv.open > 0 else 0
        
        # Volume spike
        volume_spike = volume_ratio >= self.CONFIG["volume_spike_ratio"]
        
        # Pattern detection
        pattern = self._detect_candle_pattern(fv, setup.direction)
        
        # Momentum ok — явное трёх-состояние (T85): NEUTRAL никогда не получает
        # directional-подтверждение через generic else. Только BUY/SELL имеют
        # направленный momentum-критерий; NEUTRAL/unknown → momentum_ok=False.
        momentum_ok = False
        if setup.direction == "BUY":
            momentum_ok = momentum > self.CONFIG["momentum_threshold"]
        elif setup.direction == "SELL":
            momentum_ok = momentum < -self.CONFIG["momentum_threshold"]
        # NEUTRAL/None/invalid → momentum_ok остаётся False (без side-подтверждения)
        
        # RSI exit
        rsi_exit = False
        if setup.direction == "BUY" and fv.rsi < 40:
            rsi_exit = True
        elif setup.direction == "SELL" and fv.rsi > 60:
            rsi_exit = True
        
        # Score: 0-1 based on trigger quality
        score = 0.0
        if volume_spike:
            score += 0.4
        if pattern:
            score += 0.3
        if momentum_ok:
            score += 0.3
        score = max(0.0, min(1.0, score))
        
        return Trigger(
            volume_spike=volume_spike,
            volume_ratio=volume_ratio,
            pattern=pattern,
            momentum=momentum,
            momentum_ok=momentum_ok,
            rsi_exit=rsi_exit,
            score=score,
        )
    
    # ==================== DIRECTION ====================
    
    def _determine_direction(self, h1: H1Filter, h4: H4Filter) -> Optional[str]:
        """Determine trade direction. THREE-STATE: BUY / SELL / None (NO_SIGNAL).

        Sideways/range/flat/missing data must NEVER become a direction.
        Only a CONFIRMED trend produces a side; everything else is NO_SIGNAL."""
        # H4 confirmed (ema20/ema50 present, not equal)
        if h4.trend_up is True:
            return "BUY"
        if h4.trend_up is False and h4.ema20 is not None and h4.ema50 is not None:
            if h4.ema20 < h4.ema50:
                return "SELL"
            # ema20 == ema50 (flat) → sideways, not bearish
            return None
        # H4 missing/unconfirmed → fall back to H1 with the same three-state logic
        if h1.trend_up:
            return "BUY"
        if h1.ema20 > 0 and h1.ema50 > 0 and h1.ema20 < h1.ema50:
            return "SELL"
        # H1 sideways / flat / missing / NaN → NO_SIGNAL (never SELL by default)
        return None
    
    # ==================== PATTERN DETECTION ====================
    
    def _detect_candle_pattern(self, fv: FeatureVector, direction: str) -> Optional[str]:
        """
        Detect candlestick patterns from a single FeatureVector.
        Uses OHLC from the same bar.
        """
        # For single-bar detection, use body/shadow ratios
        body = abs(fv.close - fv.open)
        upper_shadow = fv.high - max(fv.close, fv.open)
        lower_shadow = min(fv.close, fv.open) - fv.low
        total_range = fv.high - fv.low
        
        if total_range == 0:
            return None
        
        # We need at least 2 bars for engulfing
        # For single-bar: pin bar and hammer/shooting star
        
        if direction == "BUY":
            if lower_shadow > body * 2 and lower_shadow > upper_shadow:
                return "pin_bar_bullish"
            if body < total_range * 0.3 and lower_shadow > total_range * 0.6:
                return "hammer"
        else:
            if upper_shadow > body * 2 and upper_shadow > lower_shadow:
                return "pin_bar_bearish"
            if body < total_range * 0.3 and upper_shadow > total_range * 0.6:
                return "shooting_star"
        
        return None
    
    # ==================== HELPERS ====================
    
    def _check_cooldown(self, symbol: str, current_bar: int) -> bool:
        """Check cooldown using bar indices (not wall clock)."""
        if symbol in self.last_signal_bar:
            if current_bar - self.last_signal_bar[symbol] < 1:  # 1 bar minimum
                self.stats["rejected_cooldown"] += 1
                return False
        
        if symbol in self.last_reject_bar:
            if current_bar - self.last_reject_bar[symbol] < 1:
                return False
        
        return True
    
    def _register_signal(self, symbol: str, direction: str, bar_idx: int):
        """Register a signal."""
        self.last_signal_bar[symbol] = bar_idx
        self.stats["signals_total"] += 1
        if direction == "BUY":
            self.stats["buy_signals"] += 1
        else:
            self.stats["sell_signals"] += 1
    
    def _record_reject(
        self,
        symbol: str, fv: FeatureVector, direction: str,
        score: Optional[SignalScore], h1: H1Filter, h4: H4Filter,
        setup: Setup, trigger: Trigger, bar_idx: int,
    ):
        """Record rejection for diagnostics."""
        reasons = []
        
        if score and score.final_probability < score.threshold:
            reasons.append(f"probability={score.final_probability:.3f} < {score.threshold:.3f}")
            if score.trend_score < 15:
                self.stats["rejected_trend_low"] += 1
                reasons.append("trend_low")
            if setup.direction != direction:
                self.stats["rejected_setup"] += 1
                reasons.append("setup_mismatch")
            if not trigger.volume_spike and setup.direction != direction:
                self.stats["rejected_volume"] += 1
                reasons.append("volume_low")
        
        self.reject_reasons.append({
            "symbol": symbol,
            "bar_idx": bar_idx,
            "timestamp_ms": fv.timestamp_ms,
            "direction": direction,
            "rsi": fv.rsi,
            "volume_ratio": fv.volume_ratio,
            "ema_bullish": fv.ema_bullish,
            "htf_trend": fv.htf_trend,
            "reason": "; ".join(reasons),
        })
    
    def get_stats(self) -> dict:
        """Get statistics."""
        return self.stats.copy()
    
    def reset_stats(self):
        """Reset statistics."""
        self.stats = {
            "signals_total": 0,
            "buy_signals": 0,
            "sell_signals": 0,
            "rejected_htf": 0,
            "rejected_setup": 0,
            "rejected_trigger": 0,
            "rejected_cooldown": 0,
            "rejected_probability": 0,
            "total_evaluated": 0,
            "rejected_trend_low": 0,
            "rejected_rsi": 0,
            "rejected_volume": 0,
            "rejected_pattern": 0,
        }
        self.reject_reasons = []
