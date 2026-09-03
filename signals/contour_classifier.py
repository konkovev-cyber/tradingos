"""
contour_classifier.py — Two-contour execution classifier.

Maps setup anatomy to execution contour:
  MARKET  — high-urgency breakout / impulse continuation (immediate fill, taker fee)
  LIMIT   — pullback / mean-reversion entry (post-only limit, maker fee, fill risk)
  NO_TRADE — setup doesn't meet criteria for either contour

Owner principle: "не 'лимитка или рынок', а 'какую часть существующего edge мы
получаем благодаря цене входа и какую теряем из-за отсутствия fill'."

Every decision is explicit, auditable, and versioned.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
import math

Contour = Literal["MARKET", "LIMIT", "NO_TRADE"]

# ─── Thresholds (versioned constants) ──────────────────────────────────
# v1 — 2026-08-29 — initial calibrated from owner review + AAPL/AMZN incidents

# MARKET contour: breakout / impulse continuation
MARKET_MIN_MOMENTUM_SCORE = 12          # momentum component ≥ 12/15
MARKET_MIN_VOL_RATIO = 1.5              # volume surge vs 20-bar avg
MARKET_MIN_H1_TREND_SCORE = 20          # strict H1 trend (25) or strong partial (15+)
MARKET_MAX_DIST_TO_EMA20_PCT = 0.15     # price within 0.15% of EMA20 (no extended chase)
MARKET_REQUIRE_MTF_AGREE = True         # H4/D1 must agree with H1 direction

# LIMIT contour: pullback / mean-reversion
LIMIT_MIN_PULLBACK_MATURE = True        # C1: ≥1 up-bar in last 6 M15, not straight-down≥4
LIMIT_MIN_PULLBACK_DIST_PCT = 0.25      # min 0.25% from current price (or 0.5×ATR)
LIMIT_MIN_PULLBACK_DIST_ATR_MULT = 0.5  # min 0.5×ATR from current price
LIMIT_MAX_RR_FROM_CURRENT = 1.2         # if RR from current < 1.2, market entry has poor reward
LIMIT_REQUIRE_TP_BEYOND_7D = True       # TP must be capped by 7d structure (wait_rr restores it)
LIMIT_MIN_WAIT_RR = 1.2                 # wait_rr (from limit entry) must meet min_rr

# Structural quality gates (apply to both contours)
MIN_H1_NOTIONAL_USD = 200_000           # median H1 notional (liquidity filter)
MIN_H1_MOVE_PCT = 0.25                  # 3H range ≥ 0.25% (movement filter)
MAX_DATA_AGE_HOURS = 3                  # last bar not older than 3H (freshness filter)

# Classifier version for audit trail
CLASSIFIER_VERSION = "v1-20260829"


@dataclass
class ContourFeatures:
    """Raw feature values fed to the classifier."""
    # Core scoring components
    h1_trend_score: int
    m15_structure_score: int
    momentum_score: int
    volume_score: int
    funding_oi_score: int
    entry_quality_score: int
    total_score: int

    # Structural context
    h1_up: bool
    h1_down: bool
    m15_up: bool
    pullback_mature: bool
    dist_to_e20_pct: float
    vol_ratio: float
    atr: float
    closed_last: float

    # TP / RR context
    tp_unreachable: bool
    tp_beyond_7d: bool
    rr_from_current: float
    wait_limit_entry: float
    wait_rr: float
    wait_limit_entry_deep: float
    wait_rr_deep: float
    min_rr: float

    # Gates
    mtf_gate: str | None
    stoch_gate: str | None
    gate_reason: str | None
    rr_invalid: bool

    # Quality filters
    h1_notional_med: float
    h1_move_pct: float
    data_age_hours: float

    # MTF
    h4_trend: str | None
    d1_trend: str | None
    mtf_agree: bool | None

    # Instrument
    is_stock: bool


@dataclass
class ContourThresholds:
    """Thresholds used for this classification (for audit)."""
    market_min_momentum_score: int = MARKET_MIN_MOMENTUM_SCORE
    market_min_vol_ratio: float = MARKET_MIN_VOL_RATIO
    market_min_h1_trend_score: int = MARKET_MIN_H1_TREND_SCORE
    market_max_dist_to_ema20_pct: float = MARKET_MAX_DIST_TO_EMA20_PCT
    market_require_mtf_agree: bool = MARKET_REQUIRE_MTF_AGREE

    limit_min_pullback_mature: bool = LIMIT_MIN_PULLBACK_MATURE
    limit_min_pullback_dist_pct: float = LIMIT_MIN_PULLBACK_DIST_PCT
    limit_min_pullback_dist_atr_mult: float = LIMIT_MIN_PULLBACK_DIST_ATR_MULT
    limit_max_rr_from_current: float = LIMIT_MAX_RR_FROM_CURRENT
    limit_require_tp_beyond_7d: bool = LIMIT_REQUIRE_TP_BEYOND_7D
    limit_min_wait_rr: float = LIMIT_MIN_WAIT_RR

    min_h1_notional_usd: float = MIN_H1_NOTIONAL_USD
    min_h1_move_pct: float = MIN_H1_MOVE_PCT
    max_data_age_hours: float = MAX_DATA_AGE_HOURS

    classifier_version: str = CLASSIFIER_VERSION


@dataclass
class ContourDecision:
    """Classifier output."""
    contour: Contour
    confidence: float  # 0.0–1.0, how strongly features support this contour
    features: ContourFeatures
    thresholds: ContourThresholds
    reasoning: list[str] = field(default_factory=list)  # human-readable trace


class ContourClassifier:
    """Two-contour execution classifier."""

    def __init__(self, thresholds: ContourThresholds | None = None):
        self.thresholds = thresholds or ContourThresholds()

    def classify(self, features: ContourFeatures) -> ContourDecision:
        """Classify setup into MARKET, LIMIT, or NO_TRADE."""
        # Quality gates first — if failed, NO_TRADE regardless of contour
        quality_failed = self._check_quality_gates(features)
        if quality_failed:
            return ContourDecision(
                contour="NO_TRADE",
                confidence=0.95,
                features=features,
                thresholds=self.thresholds,
                reasoning=[f"QUALITY_GATE_FAILED: {quality_failed}"]
            )

        # Hard gates that block both contours
        if features.tp_unreachable:
            return ContourDecision(
                contour="NO_TRADE",
                confidence=0.99,
                features=features,
                thresholds=self.thresholds,
                reasoning=["TP_UNREACHABLE: target beyond 30D range"]
            )
        if features.gate_reason:
            return ContourDecision(
                contour="NO_TRADE",
                confidence=0.95,
                features=features,
                thresholds=self.thresholds,
                reasoning=[f"ENTRY_GATE: {features.gate_reason}"]
            )
        if features.mtf_gate:
            return ContourDecision(
                contour="NO_TRADE",
                confidence=0.95,
                features=features,
                thresholds=self.thresholds,
                reasoning=[f"MTF_CONFLICT: {features.mtf_gate}"]
            )
        if features.stoch_gate and not features.wait_rr >= self.thresholds.limit_min_wait_rr:
            return ContourDecision(
                contour="NO_TRADE",
                confidence=0.9,
                features=features,
                thresholds=self.thresholds,
                reasoning=[f"STOCH_ZONE_CONFLICT: {features.stoch_gate} (wait_rr insufficient)"]
            )

        # Check MARKET contour
        market_check = self._check_market_contour(features)
        if market_check.passed:
            return ContourDecision(
                contour="MARKET",
                confidence=market_check.confidence,
                features=features,
                thresholds=self.thresholds,
                reasoning=market_check.reasons
            )

        # Check LIMIT contour
        limit_check = self._check_limit_contour(features)
        if limit_check.passed:
            return ContourDecision(
                contour="LIMIT",
                confidence=limit_check.confidence,
                features=features,
                thresholds=self.thresholds,
                reasoning=limit_check.reasons
            )

        # Neither contour satisfied
        return ContourDecision(
            contour="NO_TRADE",
            confidence=0.7,
            features=features,
            thresholds=self.thresholds,
            reasoning=["NO_CONTOUR_MATCH: setup doesn't meet MARKET or LIMIT criteria"]
        )

    def _check_quality_gates(self, features: ContourFeatures) -> str | None:
        """Structural quality filters (T19)."""
        if features.h1_notional_med < self.thresholds.min_h1_notional_usd:
            return f"LIQUIDITY: median H1 notional ${features.h1_notional_med:,.0f} < ${self.thresholds.min_h1_notional_usd:,.0f}"
        if features.data_age_hours > self.thresholds.max_data_age_hours:
            return f"STALE_DATA: last bar {features.data_age_hours:.1f}h old > {self.thresholds.max_data_age_hours}h"
        if features.h1_move_pct < self.thresholds.min_h1_move_pct:
            return f"FLAT_MARKET: 3H move {features.h1_move_pct:.2f}% < {self.thresholds.min_h1_move_pct}%"
        return None

    def _check_market_contour(self, features: ContourFeatures) -> _ContourCheck:
        """MARKET: breakout / impulse continuation."""
        reasons = []
        passed = True
        confidence_factors = []

        # 1. Strong momentum component
        if features.momentum_score < self.thresholds.market_min_momentum_score:
            passed = False
            reasons.append(f"momentum_score {features.momentum_score} < {self.thresholds.market_min_momentum_score}")
        else:
            reasons.append(f"momentum_score {features.momentum_score} ≥ {self.thresholds.market_min_momentum_score} ✓")
            confidence_factors.append(0.3)

        # 2. Volume surge
        if features.vol_ratio < self.thresholds.market_min_vol_ratio:
            passed = False
            reasons.append(f"vol_ratio {features.vol_ratio:.2f} < {self.thresholds.market_min_vol_ratio}")
        else:
            reasons.append(f"vol_ratio {features.vol_ratio:.2f} ≥ {self.thresholds.market_min_vol_ratio} ✓")
            confidence_factors.append(0.25)

        # 3. Clear H1 trend structure
        if features.h1_trend_score < self.thresholds.market_min_h1_trend_score:
            passed = False
            reasons.append(f"h1_trend_score {features.h1_trend_score} < {self.thresholds.market_min_h1_trend_score}")
        else:
            reasons.append(f"h1_trend_score {features.h1_trend_score} ≥ {self.thresholds.market_min_h1_trend_score} ✓")
            confidence_factors.append(0.2)

        # 4. Not extended (price near EMA20)
        if abs(features.dist_to_e20_pct) > self.thresholds.market_max_dist_to_ema20_pct:
            passed = False
            reasons.append(f"dist_to_EMA20 {features.dist_to_e20_pct:.2f}% > {self.thresholds.market_max_dist_to_ema20_pct}% (chase)")
        else:
            reasons.append(f"dist_to_EMA20 {features.dist_to_e20_pct:.2f}% ≤ {self.thresholds.market_max_dist_to_ema20_pct}% ✓")
            confidence_factors.append(0.15)

        # 5. MTF agreement
        if self.thresholds.market_require_mtf_agree:
            if features.mtf_agree is not True:
                passed = False
                reasons.append(f"MTF_AGREE: {features.mtf_agree} (need True)")
            else:
                reasons.append(f"MTF_AGREE: True ✓")
                confidence_factors.append(0.1)

        confidence = sum(confidence_factors) if passed else 0.0
        return _ContourCheck(passed, confidence, reasons)

    def _check_limit_contour(self, features: ContourFeatures) -> _ContourCheck:
        """LIMIT: pullback / mean-reversion entry via post-only limit."""
        reasons = []
        passed = True
        confidence_factors = []

        # 1. Pullback maturity (C1)
        if self.thresholds.limit_min_pullback_mature and not features.pullback_mature:
            passed = False
            reasons.append("pullback_mature: False (C1 failed — still falling)")
        else:
            reasons.append("pullback_mature: True ✓")
            confidence_factors.append(0.3)

        # 2. Minimum pullback distance from current price
        min_dist_pct = self.thresholds.limit_min_pullback_dist_pct
        min_dist_atr = self.thresholds.limit_min_pullback_dist_atr_mult * features.atr / features.closed_last * 100
        required_dist = max(min_dist_pct, min_dist_atr)
        actual_dist = abs(features.closed_last - features.wait_limit_entry) / features.closed_last * 100 if features.wait_limit_entry > 0 else 0

        if features.wait_limit_entry <= 0 or actual_dist < required_dist:
            passed = False
            reasons.append(f"pullback_dist {actual_dist:.2f}% < required {required_dist:.2f}% (max of {min_dist_pct}% price, {min_dist_atr:.2f}% ATR)")
        else:
            reasons.append(f"pullback_dist {actual_dist:.2f}% ≥ {required_dist:.2f}% ✓")
            confidence_factors.append(0.25)

        # 3. RR from current price is poor (market entry unattractive)
        if features.rr_from_current >= self.thresholds.limit_max_rr_from_current:
            passed = False
            reasons.append(f"rr_from_current {features.rr_from_current:.2f} ≥ {self.thresholds.limit_max_rr_from_current} (market entry viable)")
        else:
            reasons.append(f"rr_from_current {features.rr_from_current:.2f} < {self.thresholds.limit_max_rr_from_current} ✓ (limit improves entry)")
            confidence_factors.append(0.2)

        # 4. TP capped by 7d structure (wait_rr restores it)
        if self.thresholds.limit_require_tp_beyond_7d and not features.tp_beyond_7d:
            passed = False
            reasons.append("tp_beyond_7d: False (TP not capped — market entry viable)")
        else:
            reasons.append("tp_beyond_7d: True ✓ (limit restores RR)")
            confidence_factors.append(0.15)

        # 5. Wait RR meets minimum
        if features.wait_rr < self.thresholds.limit_min_wait_rr:
            passed = False
            reasons.append(f"wait_rr {features.wait_rr:.2f} < min_rr {self.thresholds.limit_min_wait_rr}")
        else:
            reasons.append(f"wait_rr {features.wait_rr:.2f} ≥ min_rr {self.thresholds.limit_min_wait_rr} ✓")
            confidence_factors.append(0.1)

        confidence = sum(confidence_factors) if passed else 0.0
        return _ContourCheck(passed, confidence, reasons)


@dataclass
class _ContourCheck:
    passed: bool
    confidence: float
    reasons: list[str]


# ─── Factory for building features from scanner internals ──────────────

def build_features_from_scanner(
    *,
    # Core scores
    h1_trend_score: int,
    m15_structure_score: int,
    momentum_score: int,
    volume_score: int,
    funding_oi_score: int,
    entry_quality_score: int,
    total_score: int,
    # Structural
    h1_up: bool,
    h1_down: bool,
    m15_up: bool,
    pullback_mature: bool,
    dist_to_e20_pct: float,
    vol_ratio: float,
    atr: float,
    closed_last: float,
    # TP/RR
    tp_unreachable: bool,
    tp_beyond_7d: bool,
    rr_from_current: float,
    wait_limit_entry: float,
    wait_rr: float,
    wait_limit_entry_deep: float,
    wait_rr_deep: float,
    min_rr: float,
    # Gates
    mtf_gate: str | None,
    stoch_gate: str | None,
    gate_reason: str | None,
    rr_invalid: bool,
    # Quality
    h1_notional_med: float,
    h1_move_pct: float,
    data_age_hours: float,
    # MTF
    h4_trend: str | None,
    d1_trend: str | None,
    mtf_agree: bool | None,
    # Instrument
    is_stock: bool,
) -> ContourFeatures:
    """Convenience factory to build ContourFeatures from scanner locals."""
    return ContourFeatures(
        h1_trend_score=h1_trend_score,
        m15_structure_score=m15_structure_score,
        momentum_score=momentum_score,
        volume_score=volume_score,
        funding_oi_score=funding_oi_score,
        entry_quality_score=entry_quality_score,
        total_score=total_score,
        h1_up=h1_up,
        h1_down=h1_down,
        m15_up=m15_up,
        pullback_mature=pullback_mature,
        dist_to_e20_pct=dist_to_e20_pct,
        vol_ratio=vol_ratio,
        atr=atr,
        closed_last=closed_last,
        tp_unreachable=tp_unreachable,
        tp_beyond_7d=tp_beyond_7d,
        rr_from_current=rr_from_current,
        wait_limit_entry=wait_limit_entry,
        wait_rr=wait_rr,
        wait_limit_entry_deep=wait_limit_entry_deep,
        wait_rr_deep=wait_rr_deep,
        min_rr=min_rr,
        mtf_gate=mtf_gate,
        stoch_gate=stoch_gate,
        gate_reason=gate_reason,
        rr_invalid=rr_invalid,
        h1_notional_med=h1_notional_med,
        h1_move_pct=h1_move_pct,
        data_age_hours=data_age_hours,
        h4_trend=h4_trend,
        d1_trend=d1_trend,
        mtf_agree=mtf_agree,
        is_stock=is_stock,
    )