"""
Signal Diagnostic — причины отклонения/сигнала.

v8.0: Работает с FeatureVector (immutable), ноль вычислений.
Каждый сигнал/отклонение содержит детальную диагностическую сводку:
- RSI%: где находится RSI (overbought/oversold/neutral)
- Volume%: соотношение volume к среднему
- Trend%: согласованность с HTF трендом
- HTF%: статус 4H контекста
- SetupScore/TriggerScore: детали каждого уровня
- Integrity: оценка качества признаков

Usage:
    diag = SignalDiagnostic()
    report = diag.analyze(fv, h1_filter, h4_filter, setup, trigger, score)
    print(report.summary)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from tradingos.signals.feature_vector import FeatureVector
from .signal_types import H1Filter, H4Filter, Setup, Trigger
from .signal_scoring import SignalScore


# ═══════════════════════════════════════════════════════════
#  Diagnostic Report
# ═══════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Полная диагностика одного сигнала или отклонения."""

    # Сигнал?
    is_signal: bool
    signal_side: Optional[str]  # 'BUY'/'SELL' or None

    # RSI%
    rsi_value: float
    rsi_zone: str  # 'overbought', 'oversold', 'neutral'
    rsi_score: float  # 0-1 contribution to setup

    # Volume%
    volume_ratio: float
    volume_zone: str  # 'spike', 'above_avg', 'normal', 'below_avg'
    volume_score: float  # 0-1 contribution to trigger

    # Trend%
    trend_aligned: bool
    trend_strength: str  # 'strong', 'moderate', 'weak', 'none'
    trend_score: float  # 0-1 contribution to trend filter

    # HTF%
    htf_aligned: bool
    htf_strength: str  # 'strong', 'moderate', 'weak', 'none', 'unknown'
    htf_score: float  # 0-1 contribution to h4 filter

    # Setup/Trigger scores
    setup_score: float
    trigger_score: float
    final_score: float
    threshold: float

    # Integrity
    integrity_score: float

    # Rejection reason (if not signal)
    rejection_reasons: list[str]

    @property
    def summary(self) -> str:
        """Короткая сводка для логов."""
        sig = f"✅ {self.signal_side}" if self.is_signal else f"❌ REJECTED"
        lines = [
            f"[DIAGNOSTIC] {sig} {self.rsi_zone}({self.rsi_value:.1f}) "
            f"Vol{self.volume_ratio:.2f}({self.volume_zone}) "
            f"Trend{'+' if self.trend_aligned else '-'}"
            f" HTF{'+' if self.htf_aligned else '-'} "
            f"Score={self.final_score:.3f} vs {self.threshold:.3f} "
            f"Integrity={self.integrity_score:.1f}",
        ]
        if not self.is_signal and self.rejection_reasons:
            lines.append(f"  Reasons: {'; '.join(self.rejection_reasons)}")
        return '\n'.join(lines)

    def to_dict(self) -> dict:
        """Серийлизовать в dict (для FeatureSnapshot)."""
        return {
            'is_signal': self.is_signal,
            'signal_side': self.signal_side,
            'rsi': self.rsi_value,
            'rsi_zone': self.rsi_zone,
            'volume_ratio': self.volume_ratio,
            'volume_zone': self.volume_zone,
            'trend_aligned': self.trend_aligned,
            'htf_aligned': self.htf_aligned,
            'setup_score': self.setup_score,
            'trigger_score': self.trigger_score,
            'final_score': self.final_score,
            'threshold': self.threshold,
            'integrity': self.integrity_score,
            'rejection_reasons': self.rejection_reasons,
        }


# ═══════════════════════════════════════════════════════════
#  Signal Diagnostic Engine
# ═══════════════════════════════════════════════════════════

class SignalDiagnostic:
    """
    Анализирует WHY сигнал был создан или отклонен.
    
    Работает с готовыми объектами (FeatureVector, H1Filter, H4Filter, Setup, Trigger, Score).
    Никаких вычислений индикаторов.
    """

    def analyze(
        self,
        fv: FeatureVector,
        h1_filter: H1Filter,
        h4_filter: H4Filter,
        setup: Setup,
        trigger: Trigger,
        score: SignalScore,
        is_signal: bool,
        signal_side: Optional[str] = None,
        rejection_reasons: Optional[list[str]] = None,
    ) -> DiagnosticReport:
        """
        Полный анализ одного бэктест-бара.
        
        Args:
            fv: FeatureVector (все индикаторы precomputed)
            h1_filter: 1H контекст из FeatureVector
            h4_filter: 4H контекст из HTFCollector
            setup: Результат _derive_setup()
            trigger: Результат _derive_trigger()
            score: SignalScore от SignalScoringEngine
            is_signal: True если есть сигнал
            signal_side: 'BUY'/'SELL' если сигнал, None если отклонение
            rejection_reasons: Список причин отклонения (если is_signal=False)
            
        Returns:
            DiagnosticReport с полной сводкой
        """
        # ── RSI Zone ──
        rsi_val = fv.rsi
        if rsi_val >= 70:
            rsi_zone = "overbought"
            rsi_contrib = 0.3
        elif rsi_val <= 30:
            rsi_zone = "oversold"
            rsi_contrib = 0.8
        elif rsi_val >= 60:
            rsi_zone = "neutral_high"
            rsi_contrib = 0.6
        elif rsi_val <= 40:
            rsi_zone = "neutral_low"
            rsi_contrib = 0.6
        else:
            rsi_zone = "neutral"
            rsi_contrib = 0.5
        rsi_score = rsi_contrib  # 0-1 contribution

        # ── Volume Zone ──
        vol_ratio = fv.volume_ratio
        if vol_ratio >= 2.0:
            vol_zone = "spike"
            vol_contrib = 1.0
        elif vol_ratio >= 1.5:
            vol_zone = "above_avg"
            vol_contrib = 0.8
        elif vol_ratio >= 0.8:
            vol_zone = "normal"
            vol_contrib = 0.5
        else:
            vol_zone = "below_avg"
            vol_contrib = 0.2
        vol_score = vol_contrib

        # ── Trend Strength ──
        trend_score_val = h1_filter.trend_score if h1_filter else 0.0
        if trend_score_val >= 0.7:
            trend_strength = "strong"
        elif trend_score_val >= 0.4:
            trend_strength = "moderate"
        elif trend_score_val >= 0.2:
            trend_strength = "weak"
        else:
            trend_strength = "none"

        # ── HTF Strength ──
        htf_score_val = h4_filter.score if h4_filter else 0.0
        if htf_score_val >= 0.7:
            htf_strength = "strong"
        elif htf_score_val >= 0.4:
            htf_strength = "moderate"
        elif htf_score_val >= 0.2:
            htf_strength = "weak"
        else:
            htf_strength = "none"

        # ── Build Report ──
        return DiagnosticReport(
            is_signal=is_signal,
            signal_side=signal_side,
            rsi_value=rsi_val,
            rsi_zone=rsi_zone,
            rsi_score=rsi_score,
            volume_ratio=vol_ratio,
            volume_zone=vol_zone,
            volume_score=vol_score,
            trend_aligned=h1_filter.aligned if h1_filter else False,
            trend_strength=trend_strength,
            trend_score=trend_score_val,
            htf_aligned=h4_filter.aligned if h4_filter else False,
            htf_strength=htf_strength,
            htf_score=htf_score_val,
            setup_score=setup.score,
            trigger_score=trigger.score,
            final_score=score.final_probability if score else 0.0,
            threshold=score.threshold if score else 0.0,
            integrity_score=fv.integrity_score,
            rejection_reasons=rejection_reasons or [],
        )


# ═══════════════════════════════════════════════════════════
#  Feature Snapshot — сохранить признаки сделки
# ═══════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class TradeSnapshot:
    """
    Снимок ВСЕХ признаков в момент открытия сделки.
    
    Используется для post-mortem анализа и обучения.
    """
    # Time
    timestamp_ms: int
    symbol: str

    # Price
    open: float
    close: float
    high: float
    low: float
    volume: float

    # Core Features (precomputed)
    ema20: float
    ema50: float
    ema200: float
    rsi: float
    macd_line: float
    macd_signal: float
    macd_hist: float
    atr: float
    bb_upper: float
    bb_lower: float
    bb_width: float
    adx: float
    volume_ma: float
    volume_ratio: float
    obv: float
    vwap: float

    # Flags
    ema_bullish: bool
    price_above_ema50: bool
    rsi_overbought: bool
    rsi_oversold: bool

    # HTF
    htf_aligned: bool
    htf_ema20: float
    htf_ema50: float
    htf_trend_score: float

    # 1H Context
    h1_trend_score: float
    h1_alignment: str

    # Signal
    signal_side: str
    signal_score: float
    signal_threshold: float

    # Setup/Trigger
    setup_score: float
    trigger_score: float

    # Trade
    expected_tp: float
    expected_sl: float
    expected_rr: float

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_diagnostic(
        cls,
        fv: FeatureVector,
        h4_filter: H4Filter,
        h1_filter: H1Filter,
        setup: Setup,
        trigger: Trigger,
        score: SignalScore,
        signal_side: str,
        expected_tp: float,
        expected_sl: float,
        expected_rr: float,
    ) -> "TradeSnapshot":
        """Создать TradeSnapshot из диагностики."""
        macd_hist = fv.macd_line - fv.macd_signal

        htf_ema20 = 0.0
        htf_ema50 = 0.0
        htf_trend_score = 0.0
        if h4_filter:
            htf_ema20 = h4_filter.ema20 or 0.0
            htf_ema50 = h4_filter.ema50 or 0.0
            htf_trend_score = h4_filter.score or 0.0

        h1_alignment = h1_filter.alignment if h1_filter else "NEUTRAL"

        return cls(
            timestamp_ms=fv.timestamp_ms,
            symbol=fv.symbol,
            open=fv.open, close=fv.close,
            high=fv.high, low=fv.low, volume=fv.volume,
            ema20=fv.ema20, ema50=fv.ema50, ema200=fv.ema200,
            rsi=fv.rsi, macd_line=fv.macd_line,
            macd_signal=fv.macd_signal, macd_hist=macd_hist,
            atr=fv.atr, bb_upper=fv.bb_upper, bb_lower=fv.bb_lower,
            bb_width=fv.bb_width if hasattr(fv, 'bb_width') else 0.0,
            adx=fv.adx, volume_ma=fv.volume_ma,
            volume_ratio=fv.volume_ratio, obv=fv.obv, vwap=fv.vwap,
            ema_bullish=fv.ema_bullish, price_above_ema50=fv.price_above_ema50,
            rsi_overbought=fv.rsi_overbought, rsi_oversold=fv.rsi_oversold,
            htf_aligned=h4_filter.aligned if h4_filter else False,
            htf_ema20=htf_ema20, htf_ema50=htf_ema50,
            htf_trend_score=htf_trend_score,
            h1_trend_score=h1_filter.trend_score if h1_filter else 0.0,
            h1_alignment=h1_alignment,
            signal_side=signal_side,
            signal_score=score.final_probability if score else 0.0,
            signal_threshold=score.threshold if score else 0.0,
            setup_score=setup.score,
            trigger_score=trigger.score,
            expected_tp=expected_tp, expected_sl=expected_sl,
            expected_rr=expected_rr,
        )
