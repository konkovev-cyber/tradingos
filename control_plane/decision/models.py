"""
control_plane/decision/models.py
Decision Layer models — pure dataclasses.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class RuleResult:
    rule_name: str
    passed: bool
    score: float
    reason: str


@dataclass
class SystemRecommendation:
    decision: str  # WAIT | REVIEW | APPROVE | BLOCK
    confidence: float
    reasons: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(default_factory=list)
    rule_results: List[RuleResult] = field(default_factory=list)
    total_score: float = 0.0
