"""
control_plane/kpi_collector/models.py
KPI Collector models — pure dataclasses.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict


class KPIStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    COLLECTING = "COLLECTING"
    READY = "READY"


KPI_TARGET_SAMPLES = 20


@dataclass(frozen=True)
class ActionSample:
    """Immutable sample for KPI calculation."""
    symbol: str
    side: str
    action_type: str
    gross_pnl: float
    net_pnl: float
    hold_pnl: float
    regime: str = "UNKNOWN"

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "action_type": self.action_type,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "hold_pnl": self.hold_pnl,
            "regime": self.regime,
        }


@dataclass
class KPIMetric:
    status: str = KPIStatus.NOT_STARTED.value
    value: Optional[float] = None
    samples: int = 0
    target_samples: int = KPI_TARGET_SAMPLES
    extra: Dict = field(default_factory=dict)


@dataclass
class CollectorState:
    schema_version: str = "v1"
    last_updated: str = ""
    te: KPIMetric = field(default_factory=KPIMetric)
    esr: KPIMetric = field(default_factory=KPIMetric)
    erg: KPIMetric = field(default_factory=KPIMetric)
    samples: list = field(default_factory=list)
