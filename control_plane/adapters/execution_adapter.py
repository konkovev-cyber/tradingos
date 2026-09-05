"""
control_plane/adapters/execution_adapter.py
Read-only adapter for Execution State.
Monitors systemd services and bot health.
"""
import subprocess

from ..models import ExecutionState

SERVICES = [
    "ubot-bingx",
    "pie-observer",
    "position-guard",
    "tradingos-telegram",
    "trade-collector",
    "scalper-v6",
    "market-agent-bot",
    "trading-control",
]


def _check_service(name: str) -> str:
    try:
        out = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def collect() -> ExecutionState:
    statuses = {name: _check_service(name) for name in SERVICES}
    running = sum(1 for s in statuses.values() if s == "active")

    return ExecutionState(
        ubot_status=statuses.get("ubot-bingx", "unknown"),
        pie_status=statuses.get("pie-observer", "unknown"),
        guard_status=statuses.get("position-guard", "unknown"),
        live_permission="NONE",
        services_running=running,
        services_total=len(SERVICES),
    )
