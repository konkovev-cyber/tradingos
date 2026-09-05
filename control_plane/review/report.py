"""
control_plane/review/report.py
Decision Review Report — renders and saves final review.

Uses canonical KPI Framework v2 (K75) terminology.
Decision Review is a CONSUMER of TE/ESR/ERG, not a producer.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from .aggregator import (
    REVIEW_PATH,
    collect_action_stats,
    collect_approval_stats,
    collect_data_quality,
    collect_kpi_samples,
    collect_local_signals,
)
from .evaluator import evaluate
from .models import DecisionReview

REPORT_PATH = Path("/root/tradingos/control_plane/review/decision_review_v2.json")

KPI_SAMPLE_TARGET = 20


def kpi_status(samples: int) -> str:
    """
    Canonical KPI status from K75 framework.
    NOT_STARTED: 0 samples
    COLLECTING: 1-19 samples
    READY: >= 20 samples (sufficient for evaluation)
    """
    if samples == 0:
        return "NOT_STARTED"
    if samples < KPI_SAMPLE_TARGET:
        return "COLLECTING"
    return "READY"


def kpi_status_icon(status: str) -> str:
    return {"NOT_STARTED": "⚪", "COLLECTING": "🟡", "READY": "🟢"}.get(status, "?")


def decision_eligibility(kpi_statuses: dict, dq: float) -> str:
    """
    Decision can only be made when:
    - All 3 KPIs are READY (>=20 samples each)
    - DQ >= 80
    """
    all_ready = all(s == "READY" for s in kpi_statuses.values())
    if not all_ready:
        return "NOT_READY"
    if dq < 80:
        return "NOT_READY"
    return "ELIGIBLE"


def generate_report() -> DecisionReview:
    data_quality = collect_data_quality()
    action_stats = collect_action_stats()
    kpi_samples = collect_kpi_samples()
    local_signals = collect_local_signals()
    approval_stats = collect_approval_stats()

    verdict = evaluate(data_quality, action_stats, local_signals, kpi_samples)

    local_serializable = {
        sym: {
            "paper_delta": s.paper_delta,
            "net_vs_hold": s.net_vs_hold,
            "local_recommendation": s.local_recommendation,
        }
        for sym, s in local_signals.items()
    }

    return DecisionReview(
        timestamp=datetime.now(timezone.utc).isoformat(),
        experiment="ACTION_SHADOW_v1",
        verdict=verdict,
        data_quality=data_quality,
        action_stats=action_stats,
        local_signals=local_serializable,
        kpi_samples=kpi_samples,
        notes=f"Approvals: {approval_stats.get('approved', 0)} approved, {approval_stats.get('rejected', 0)} rejected",
    )


def save_report(review: DecisionReview) -> Path:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": review.timestamp,
        "experiment": review.experiment,
        "verdict": {
            "decision": review.verdict.verdict,
            "confidence": review.verdict.confidence,
            "reasons": review.verdict.reasons,
            "next_action": review.verdict.next_action,
        },
        "data_quality": review.data_quality,
        "action_stats": review.action_stats,
        "kpi_samples": {
            "te": review.kpi_samples.te,
            "esr": review.kpi_samples.esr,
            "erg": review.kpi_samples.erg,
        },
        "local_signals": review.local_signals,
        "notes": review.notes,
    }
    with REPORT_PATH.open("w") as f:
        json.dump(data, f, indent=2)
    return REPORT_PATH


def render_text(review: DecisionReview) -> str:
    v = review.verdict
    icon = {"PROMOTE": "🟢", "REVIEW": "🟡", "FREEZE": "🔴", "NOT_READY": "⚪"}.get(v.verdict, "?")

    dq = review.data_quality.get("score", 0)
    te_status = kpi_status(review.kpi_samples.te)
    esr_status = kpi_status(review.kpi_samples.esr)
    erg_status = kpi_status(review.kpi_samples.erg)
    eligibility = decision_eligibility(
        {"te": te_status, "esr": esr_status, "erg": erg_status}, dq
    )

    lines = [
        "=" * 64,
        "  TRADINGOS DECISION REVIEW v2",
        "  (KPI Framework v2 consumer. Final gate.)",
        "=" * 64,
        f"  Timestamp:  {review.timestamp}",
        f"  Experiment: {review.experiment}",
        "",
        "  ── Verdict ──",
        f"  Decision:    {icon} {v.verdict}",
        f"  Confidence:  {v.confidence:.0%}",
        f"  Next:        {v.next_action}",
        "",
        "  ── Data Quality ──",
        f"  DQ score:    {dq}/100",
        "",
        "  ── KPI STATUS (canonical, from K75) ──",
        f"  TE    {kpi_status_icon(te_status):2s} {te_status:14s}  Samples: {review.kpi_samples.te} / {KPI_SAMPLE_TARGET}",
        f"  ESR   {kpi_status_icon(esr_status):2s} {esr_status:14s}  Samples: {review.kpi_samples.esr} / {KPI_SAMPLE_TARGET}",
        f"  ERG   {kpi_status_icon(erg_status):2s} {erg_status:14s}  Samples: {review.kpi_samples.erg} / {KPI_SAMPLE_TARGET}",
        "",
        f"  Decision Eligibility: {'✅ ELIGIBLE' if eligibility == 'ELIGIBLE' else '⛔ ' + eligibility}",
        "",
        "  ── Action Stats ──",
        f"  PG total:        {review.action_stats.get('pg_total', 0)}",
        f"  PG valid MOVE_SL:{review.action_stats.get('pg_valid_move_sl', 0)}",
        f"  Action approved: {review.action_stats.get('action_approved_raw', 0)}",
        f"  Action unique:   {review.action_stats.get('action_unique', 0)}",
        "",
        "  ── Local Signals (per-decision, NOT KPIs) ──",
    ]
    for sym, sig in review.local_signals.items():
        rec_icon = {"ACTION_BETTER": "🟢", "HOLD_BETTER": "🔴", "NEUTRAL": "⚪"}.get(
            sig.get("local_recommendation", "?"), "?"
        )
        lines.append(
            f"    {rec_icon} {sym:14s} paper_delta={sig.get('paper_delta', 0):+.4f} "
            f"net_vs_hold={sig.get('net_vs_hold', 0):+.4f} → {sig.get('local_recommendation', '?')}"
        )
    if not review.local_signals:
        lines.append("    (no local signals yet)")

    lines += [
        "",
        "  ── Verdict Reasons ──",
    ]
    for r in v.reasons:
        lines.append(f"    • {r}")
    lines += [
        "",
        f"  {review.notes}",
        "",
        "  Architecture (per K75 / R56 / AD46):",
        "    Reality Engine → Local Signals (per-decision, not system-wide)",
        "    KPI Collector  → TE/ESR/ERG (system-wide, ≥20 samples each)",
        "    Decision Review → Consumer only (no KPI computation here)",
        "",
        "  Note: Read-only aggregator. No execution. No LIVE changes.",
        "=" * 64,
    ]
    return "\n".join(lines)
