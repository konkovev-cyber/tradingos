"""
control_plane/approval/models.py
Human Approval Layer models — pure dataclasses.
"""
from dataclasses import dataclass, field
from typing import List, Optional


class ApprovalStatus:
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class ApprovalRequest:
    request_id: str
    recommendation: str
    source: str  # e.g. "CEO_CONTROL", "DECISION_ENGINE", "CAPITAL_INTELLIGENCE"
    reasons: List[str] = field(default_factory=list)
    status: str = ApprovalStatus.PENDING
    operator_action: Optional[str] = None
    approved_by: Optional[str] = None
    timestamp_created: str = ""
    timestamp_resolved: str = ""
    notes: str = ""
