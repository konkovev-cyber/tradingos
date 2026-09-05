"""
control_plane/reality/models.py
Reality Engine models — pure dataclasses.
"""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class CostBreakdown:
    commission: float = 0.0
    spread: float = 0.0
    slippage: float = 0.0
    funding: float = 0.0
    latency_penalty: float = 0.0
    total: float = 0.0


@dataclass
class RealityResult:
    timestamp: str
    symbol: str
    action: str
    gross_result: float
    position_value: float
    costs: CostBreakdown
    net_result: float
    hold_result: float
    vs_hold: float
    recommendation: str
    notes: str = ""
