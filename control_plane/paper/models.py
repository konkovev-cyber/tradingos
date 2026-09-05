"""
control_plane/paper/models.py
Paper Execution models — pure dataclasses.
"""
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PaperScenario:
    scenario_id: str
    action: str  # HOLD, MOVE_SL_BE, TAKE_PARTIAL
    symbol: str
    hold_pnl: float
    action_pnl: float
    delta: float
    verdict: str  # ACTION_BETTER, HOLD_BETTER, NEUTRAL
    notes: str = ""


@dataclass
class PaperSimulation:
    timestamp: str
    symbol: str
    base_position_value: float
    scenarios: List[PaperScenario] = field(default_factory=list)
    best_action: str = "HOLD"
    best_delta: float = 0.0
