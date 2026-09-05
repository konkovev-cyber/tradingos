"""
control_plane/review/evaluator.py
Decision Review Verdict logic.

Consistent with existing Decision Engine rules (KPI Framework v2, K75).
This is a thin aggregator — it does NOT compute TE/ESR/ERG itself.
It only:
  - Tracks sample counts toward canonical KPIs
  - Reports LOCAL decision signals (per-action, not system-wide)
  - Gates verdict on sample size
"""
from typing import Dict, List
from .models import ReviewVerdict


def evaluate(
    data_quality: Dict,
    action_stats: Dict,
    local_signals: Dict,
    kpi_samples: Dict,
) -> ReviewVerdict:
    """
    Sample-based gating per KPI Framework v2:
    - Need ≥20 samples for TE, ESR, ERG
    - DQ must be ≥80
    - Until then, verdict stays NOT_READY or REVIEW
    """
    dq = data_quality.get("score", 0)
    te_samples = kpi_samples.te
    esr_samples = kpi_samples.esr
    erg_samples = kpi_samples.erg
    action_unique = action_stats.get("action_unique", 0)

    has_local_signal = bool(local_signals)
    local_hold_better = any(
        getattr(s, "local_recommendation", None) == "HOLD_BETTER"
        for s in local_signals.values()
    )

    reasons: List[str] = []
    next_action = ""

    if dq < 80:
        verdict = "NOT_READY"
        reasons.append(f"DQ={dq} critical (need ≥80)")
        next_action = "WAIT_FOR_DATA_QUALITY"
    elif te_samples < 20 and esr_samples < 20 and erg_samples < 20:
        verdict = "NOT_READY"
        reasons.append(
            f"KPI samples insufficient: TE={te_samples}, ESR={esr_samples}, ERG={erg_samples}"
        )
        reasons.append("All canonical KPIs require ≥20 samples")
        next_action = "CONTINUE_COLLECTING"
    elif dq < 90:
        verdict = "REVIEW"
        reasons.append(f"DQ={dq} acceptable but below 90")
        reasons.append(f"KPI samples: TE={te_samples}, ESR={esr_samples}, ERG={erg_samples}")
        next_action = "REVISE_POLICY"
    else:
        verdict = "REVIEW"
        reasons.append(f"DQ={dq} ≥90 PASS")
        reasons.append(f"KPI samples: TE={te_samples}, ESR={esr_samples}, ERG={erg_samples}")
        if local_hold_better:
            reasons.append("Local signal: HOLD_BETTER detected")
        next_action = "MONITOR_KPI"

    confidence = 0.5
    if dq >= 90:
        confidence += 0.2
    if te_samples >= 20:
        confidence += 0.1
    if esr_samples >= 20:
        confidence += 0.1
    if erg_samples >= 20:
        confidence += 0.1
    confidence = min(confidence, 1.0)

    return ReviewVerdict(
        verdict=verdict,
        confidence=round(confidence, 2),
        reasons=reasons,
        next_action=next_action,
    )
