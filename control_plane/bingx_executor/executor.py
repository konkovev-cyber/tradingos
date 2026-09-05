"""
control_plane/bingx_executor/executor.py
High-level executor with State Machine, retries, and verification.

Это первый реальный Executor с правильным контрактом:
  PENDING → BUILDING → SENDING → SENT → VERIFIED
  On failure: FAILED → RETRYING (до 3) → MANUAL_REQUIRED
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .client import BingXExecutor
from .execution_state_machine import ExecutionRecord, ExecutionState, ExecutionStateMachine

JOURNAL_PATH = Path("/root/tradingos/control_plane/bingx_executor/execution_journal.jsonl")


def _load_journal() -> List[dict]:
    if not JOURNAL_PATH.exists():
        return []
    try:
        with JOURNAL_PATH.open() as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []


def _append_journal(record: dict) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def render_state_machine(r: ExecutionRecord) -> str:
    icons = {
        "PENDING": "⏳",
        "BUILDING": "🔨",
        "SENDING": "📤",
        "SENT": "📨",
        "VERIFIED": "✅",
        "FAILED": "❌",
        "RETRYING": "🔄",
        "MANUAL_REQUIRED": "⚠️",
        "REJECTED": "🚫",
    }
    icon = icons.get(r.state.value, "?")
    lines = [
        "=" * 60,
        f"  EXECUTION STATE MACHINE",
        "=" * 60,
        f"  Record:   {r.record_id}",
        f"  Action:   {r.action}",
        f"  Symbol:   {r.symbol}",
        f"  Target:   {r.target_price}",
        f"  State:    {icon} {r.state.value}",
        f"  Attempts: {r.attempts}/{r.max_attempts}",
        f"  Created:  {r.created_at}",
        f"  Updated:  {r.updated_at}",
    ]
    if r.verified_at:
        lines.append(f"  Verified: {r.verified_at}")
    if r.last_error:
        lines.append(f"  Last err: {r.last_error}")
    if r.last_response:
        lines.append(f"  Response: {json.dumps(r.last_response)[:200]}")
    lines.append("=" * 60)
    return "\n".join(lines)


async def execute_set_tp(
    symbol: str,
    side: str,
    position_side: str,
    quantity: float,
    stop_price: float,
    max_attempts: int = 3,
) -> ExecutionRecord:
    """
    Execute SET_TP on BingX with full state machine.

    Returns the final ExecutionRecord.
    VERIFIED = TP confirmed on exchange
    FAILED → RETRYING → after max_attempts → MANUAL_REQUIRED
    """
    record_id = str(uuid.uuid4())[:8]
    record = ExecutionRecord(
        record_id=record_id,
        symbol=symbol,
        action="SET_TP",
        target_price=stop_price,
        state=ExecutionState.PENDING,
        max_attempts=max_attempts,
    )
    sm = ExecutionStateMachine(record)

    executor = BingXExecutor()

    while not sm.is_terminal():
        if record.attempts >= record.max_attempts and record.state == ExecutionState.RETRYING:
            sm.transition(ExecutionState.MANUAL_REQUIRED, error="max attempts reached")
            break

        sm.transition(ExecutionState.BUILDING)
        result = await executor.execute_with_verification(
            symbol, side, position_side, quantity, stop_price
        )

        if result.get("verified"):
            sm.transition(ExecutionState.SENT, response=result)
            sm.transition(ExecutionState.VERIFIED, response=result)
            record.last_response = result
            break
        else:
            error_msg = result.get("error", "verification failed")
            sm.transition(ExecutionState.FAILED, error=error_msg, response=result)
            if sm.should_retry():
                sm.transition(ExecutionState.RETRYING, error=error_msg)
                await asyncio.sleep(2)
                continue
            else:
                sm.transition(ExecutionState.MANUAL_REQUIRED, error=error_msg)
                break

    _append_journal(record.to_dict())
    return record


def list_executions() -> List[dict]:
    return _load_journal()
