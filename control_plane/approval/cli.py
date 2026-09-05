"""
control_plane/approval/cli.py
CLI for Human Approval Layer.

Usage:
    python3 -m control_plane.approval.cli list
    python3 -m control_plane.approval.cli list --status PENDING
    python3 -m control_plane.approval.cli create --rec "PROTECT_VELVET" --source "CEO" --reason "concentration 79.6%"
    python3 -m control_plane.approval.cli approve <request_id> --action "ALLOW_PAPER"
    python3 -m control_plane.approval.cli reject <request_id> --reason "too risky"
    python3 -m control_plane.approval.cli stats
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from control_plane.approval.approval_store import (
    approve,
    create_request,
    get_pending,
    get_stats,
    list_requests,
    reject,
)
from control_plane.approval.models import ApprovalStatus


def cmd_list(args) -> int:
    requests = list_requests(status_filter=args.status)
    if not requests:
        print("No approval requests found.")
        return 0
    print(f"{'ID':8s} {'Status':10s} {'Source':15s} {'Recommendation':30s} {'Created':20s}")
    print("-" * 85)
    for r in requests:
        icon = {"PENDING": "⏳", "APPROVED": "✅", "REJECTED": "❌", "EXPIRED": "⏰"}.get(r.status, "?")
        print(f"{r.request_id:8s} {icon} {r.status:8s} {r.source:15s} {r.recommendation:30s} {r.timestamp_created[:19]}")
    return 0


def cmd_create(args) -> int:
    req = create_request(
        recommendation=args.rec,
        source=args.source,
        reasons=[args.reason] if args.reason else [],
        notes=args.notes or "",
    )
    print(f"Created approval request: {req.request_id}")
    print(f"  Recommendation: {req.recommendation}")
    print(f"  Source: {req.source}")
    print(f"  Status: {req.status}")
    return 0


def cmd_approve(args) -> int:
    req = approve(args.request_id, operator_action=args.action)
    if req:
        print(f"✅ APPROVED: {req.request_id}")
        print(f"   Recommendation: {req.recommendation}")
        print(f"   Action: {req.operator_action}")
    else:
        print(f"❌ Request {args.request_id} not found or not PENDING")
        return 1
    return 0


def cmd_reject(args) -> int:
    req = reject(args.request_id, reason=args.reason or "rejected by operator")
    if req:
        print(f"❌ REJECTED: {req.request_id}")
        print(f"   Recommendation: {req.recommendation}")
        print(f"   Reason: {req.operator_action}")
    else:
        print(f"❌ Request {args.request_id} not found or not PENDING")
        return 1
    return 0


def cmd_stats(args) -> int:
    stats = get_stats()
    print(f"Total requests: {stats['total']}")
    for status, count in stats["by_status"].items():
        print(f"  {status}: {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Human Approval Layer")
    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser("list")
    p_list.add_argument("--status", choices=["PENDING", "APPROVED", "REJECTED", "EXPIRED"])

    p_create = sub.add_parser("create")
    p_create.add_argument("--rec", required=True, help="Recommendation text")
    p_create.add_argument("--source", required=True, help="Source (e.g. CEO, DECISION_ENGINE)")
    p_create.add_argument("--reason", default="", help="Reason")
    p_create.add_argument("--notes", default="", help="Notes")

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("request_id")
    p_approve.add_argument("--action", required=True, help="Operator action")

    p_reject = sub.add_parser("reject")
    p_reject.add_argument("request_id")
    p_reject.add_argument("--reason", default="")

    sub.add_parser("stats")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    cmds = {"list": cmd_list, "create": cmd_create, "approve": cmd_approve, "reject": cmd_reject, "stats": cmd_stats}
    return cmds[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
