"""
control_plane/capital/models.py
Capital Intelligence models — pure dataclasses.
"""
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class RiskAlert:
    alert_type: str
    symbol: str
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL
    value: float
    description: str


@dataclass
class CapitalIntelligence:
    health: str = "UNKNOWN"          # HEALTHY | WARNING | CRITICAL
    risk_score: float = 0.0          # 0-100 (higher = more risk)
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    open_positions: int = 0
    closed_trades: int = 0
    winning: int = 0
    losing: int = 0
    win_rate: float = 0.0
    biggest_risk_symbol: str = ""
    biggest_risk_pct: float = 0.0
    alerts: List[RiskAlert] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    timestamp: str = ""
