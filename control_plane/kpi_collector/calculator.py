"""
control_plane/kpi_collector/calculator.py
Pure computation of TE/ESR/ERG from samples.

No I/O. No state. No side effects.
"""
from typing import List
from .models import ActionSample, KPIStatus, KPI_TARGET_SAMPLES


def compute_te(samples: List[ActionSample]) -> tuple:
    """
    TE = mean(net_pnl - hold_pnl) per action.
    Returns (value, status, sample_count).
    """
    if not samples:
        return None, KPIStatus.NOT_STARTED.value, 0
    count = len(samples)
    if count < KPI_TARGET_SAMPLES:
        return None, KPIStatus.COLLECTING.value, count
    deltas = [s.net_pnl - s.hold_pnl for s in samples]
    value = sum(deltas) / len(deltas)
    return round(value, 6), KPIStatus.READY.value, count


def compute_esr(samples: List[ActionSample]) -> tuple:
    """
    ESR = % of actions where net_pnl > hold_pnl.
    Returns (value, status, sample_count, regime_distribution).
    """
    if not samples:
        return None, KPIStatus.NOT_STARTED.value, 0, {}
    count = len(samples)
    if count < KPI_TARGET_SAMPLES:
        return None, KPIStatus.COLLECTING.value, count, {}
    wins = sum(1 for s in samples if s.net_pnl > s.hold_pnl)
    value = wins / len(samples)
    regime_dist: dict = {}
    for s in samples:
        regime_dist[s.regime] = regime_dist.get(s.regime, 0) + 1
    return round(value, 4), KPIStatus.READY.value, count, regime_dist


def compute_erg(samples: List[ActionSample]) -> tuple:
    """
    ERG = mean of (gross - net) / gross across actions.
    Returns (value, status, sample_count).
    """
    if not samples:
        return None, KPIStatus.NOT_STARTED.value, 0
    count = len(samples)
    if count < KPI_TARGET_SAMPLES:
        return None, KPIStatus.COLLECTING.value, count
    degradations = []
    for s in samples:
        if s.gross_pnl != 0:
            degradations.append((s.gross_pnl - s.net_pnl) / s.gross_pnl)
    if not degradations:
        return None, KPIStatus.NOT_STARTED.value, count
    value = sum(degradations) / len(degradations)
    return round(value, 4), KPIStatus.READY.value, count


def compute_all(samples: List[ActionSample]) -> dict:
    """Returns dict of all three KPIs with status."""
    te_val, te_status, te_n = compute_te(samples)
    esr_val, esr_status, esr_n, regime = compute_esr(samples)
    erg_val, erg_status, erg_n = compute_erg(samples)
    return {
        "te": {"value": te_val, "status": te_status, "samples": te_n},
        "esr": {"value": esr_val, "status": esr_status, "samples": esr_n, "regime": regime},
        "erg": {"value": erg_val, "status": erg_status, "samples": erg_n},
    }
