"""
control_plane/approval/approval_store.py
Approval journal — persistent store for human decisions.
Read/Write only to approval.json. No system interaction.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .models import ApprovalRequest, ApprovalStatus

APPROVAL_PATH = Path("/root/tradingos/control_plane/approval/approval.json")


def _load() -> List[dict]:
    if not APPROVAL_PATH.exists():
        return []
    try:
        with APPROVAL_PATH.open() as f:
            return json.load(f)
    except Exception:
        return []


def _save(requests: List[dict]) -> None:
    APPROVAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with APPROVAL_PATH.open("w") as f:
        json.dump(requests, f, indent=2)


def create_request(
    recommendation: str,
    source: str,
    reasons: List[str],
    notes: str = "",
) -> ApprovalRequest:
    now = datetime.now(timezone.utc).isoformat()
    req = ApprovalRequest(
        request_id=str(uuid.uuid4())[:8],
        recommendation=recommendation,
        source=source,
        reasons=reasons,
        status=ApprovalStatus.PENDING,
        timestamp_created=now,
        notes=notes,
    )
    requests = _load()
    requests.append({
        "request_id": req.request_id,
        "recommendation": req.recommendation,
        "source": req.source,
        "reasons": req.reasons,
        "status": req.status,
        "operator_action": req.operator_action,
        "approved_by": req.approved_by,
        "timestamp_created": req.timestamp_created,
        "timestamp_resolved": req.timestamp_resolved,
        "notes": req.notes,
    })
    _save(requests)
    return req


def approve(request_id: str, operator_action: str, approved_by: str = "operator") -> Optional[ApprovalRequest]:
    requests = _load()
    for r in requests:
        if r["request_id"] == request_id and r["status"] == ApprovalStatus.PENDING:
            r["status"] = ApprovalStatus.APPROVED
            r["operator_action"] = operator_action
            r["approved_by"] = approved_by
            r["timestamp_resolved"] = datetime.now(timezone.utc).isoformat()
            _save(requests)
            return ApprovalRequest(**r)
    return None


def reject(request_id: str, reason: str = "", approved_by: str = "operator") -> Optional[ApprovalRequest]:
    requests = _load()
    for r in requests:
        if r["request_id"] == request_id and r["status"] == ApprovalStatus.PENDING:
            r["status"] = ApprovalStatus.REJECTED
            r["operator_action"] = reason
            r["approved_by"] = approved_by
            r["timestamp_resolved"] = datetime.now(timezone.utc).isoformat()
            _save(requests)
            return ApprovalRequest(**r)
    return None


def list_requests(status_filter: Optional[str] = None) -> List[ApprovalRequest]:
    requests = _load()
    out = []
    for r in requests:
        if status_filter and r["status"] != status_filter:
            continue
        out.append(ApprovalRequest(**r))
    return out


def get_pending() -> List[ApprovalRequest]:
    return list_requests(status_filter=ApprovalStatus.PENDING)


def get_stats() -> dict:
    requests = _load()
    by_status = {}
    for r in requests:
        s = r["status"]
        by_status[s] = by_status.get(s, 0) + 1
    return {"total": len(requests), "by_status": by_status}
