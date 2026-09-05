"""
control_plane/kpi_collector/state.py
Persistent state management for KPI Collector.

Append-only. Samples are immutable. Status transitions are explicit.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .models import ActionSample, CollectorState, KPIMetric, KPIStatus
from .calculator import compute_te, compute_esr, compute_erg

STATE_PATH = Path("/root/tradingos/control_plane/kpi_collector/state.json")


def load_state() -> CollectorState:
    if not STATE_PATH.exists():
        return CollectorState()
    try:
        with STATE_PATH.open() as f:
            data = json.load(f)
        state = CollectorState()
        state.schema_version = data.get("schema_version", "v1")
        state.last_updated = data.get("last_updated", "")
        state.te = _load_metric(data.get("te", {}))
        state.esr = _load_metric(data.get("esr", {}))
        state.erg = _load_metric(data.get("erg", {}))
        state.samples = data.get("samples", [])
        return state
    except Exception:
        return CollectorState()


def _load_metric(d: dict) -> KPIMetric:
    return KPIMetric(
        status=d.get("status", KPIStatus.NOT_STARTED.value),
        value=d.get("value"),
        samples=d.get("samples", 0),
        target_samples=d.get("target_samples", 20),
        extra=d.get("extra", {}),
    )


def save_state(state: CollectorState) -> Path:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": state.schema_version,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "te": {
            "status": state.te.status,
            "value": state.te.value,
            "samples": state.te.samples,
            "target_samples": state.te.target_samples,
        },
        "esr": {
            "status": state.esr.status,
            "value": state.esr.value,
            "samples": state.esr.samples,
            "target_samples": state.esr.target_samples,
            "extra": state.esr.extra,
        },
        "erg": {
            "status": state.erg.status,
            "value": state.erg.value,
            "samples": state.erg.samples,
            "target_samples": state.erg.target_samples,
        },
        "samples": state.samples,
    }
    with STATE_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return STATE_PATH


def add_samples(state: CollectorState, new_samples: List[ActionSample]) -> None:
    """
    Append new samples. Never modify existing ones.
    Then recompute all KPIs.
    """
    for s in new_samples:
        state.samples.append(s.to_dict())
    _recompute(state)


def _recompute(state: CollectorState) -> None:
    """Recompute TE/ESR/ERG from all samples in state."""
    samples: List[ActionSample] = []
    for d in state.samples:
        samples.append(ActionSample(
            symbol=d["symbol"],
            side=d["side"],
            action_type=d["action_type"],
            gross_pnl=d["gross_pnl"],
            net_pnl=d["net_pnl"],
            hold_pnl=d["hold_pnl"],
            regime=d.get("regime", "UNKNOWN"),
        ))

    te_val, te_status, te_n = compute_te(samples)
    esr_val, esr_status, esr_n, regime = compute_esr(samples)
    erg_val, erg_status, erg_n = compute_erg(samples)

    state.te.value = te_val
    state.te.status = te_status
    state.te.samples = te_n
    state.esr.value = esr_val
    state.esr.status = esr_status
    state.esr.samples = esr_n
    state.esr.extra = {"regime_distribution": regime} if regime else {}
    state.erg.value = erg_val
    state.erg.status = erg_status
    state.erg.samples = erg_n
    state.last_updated = datetime.now(timezone.utc).isoformat()
