"""
control_plane/ceo/models.py
CEO Control Plane models — pure dataclasses.
"""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ExperimentStatus:
    name: str
    progress: str
    target: str
    status: str  # COLLECTING | FROZEN | READY


@dataclass
class CEOState:
    system_status: str = "UNKNOWN"   # BLOCK | REVIEW | APPROVE
    overall_health: str = "UNKNOWN"  # HEALTHY | WARNING | CRITICAL

    capital_pnl: str = ""
    capital_status: str = ""
    positions_count: int = 0
    risk_level: str = ""
    biggest_exposure: str = ""

    experiments: List[ExperimentStatus] = field(default_factory=list)

    blockers: List[str] = field(default_factory=list)
    next_allowed_step: str = ""
    summary: str = ""

    timestamp: str = ""
