"""
control_plane/review/models.py
Decision Review models — pure dataclasses.
Uses canonical terminology from KPI Framework v2 (K75).
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ReviewVerdict:
    verdict: str
    confidence: float
    reasons: List[str] = field(default_factory=list)
    next_action: str = ""


@dataclass
class LocalSignal:
    """Per-action decision signal. NOT a system KPI."""
    paper_delta: float
    net_vs_hold: float
    local_recommendation: str  # HOLD_BETTER | ACTION_BETTER | NEUTRAL


@dataclass
class KPISampleCounts:
    """Sample counts toward canonical KPIs (TE, ESR, ERG)."""
    te: int = 0
    esr: int = 0
    erg: int = 0


@dataclass
class DecisionReview:
    timestamp: str
    experiment: str
    verdict: ReviewVerdict
    data_quality: dict
    action_stats: dict
    local_signals: dict  # symbol → LocalSignal
    kpi_samples: KPISampleCounts
    notes: str = ""
