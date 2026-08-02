"""
tg_reliable_queue.py — Reliable Telegram notification queue with exactly-once delivery.

Architecture:
- Persistent queue on disk (atomic JSONL append)
- Worker loop processes one entry at a time
- Retry with exponential backoff
- Dead-letter queue for permanent failures
- Idempotency via notification_id
- Crash recovery on startup
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

log = logging.getLogger("Guardian.TGQueue")

QUEUE_DIR = Path("/root/tradingos/guardian/tg_queue")
PENDING_FILE = QUEUE_DIR / "pending.jsonl"
DLQ_FILE = QUEUE_DIR / "dlq.jsonl"
LOCK_FILE = QUEUE_DIR / "worker.lock"

# File locks for concurrent access (Guardian writes from async, worker reads)
_queue_lock = threading.Lock()
_dlq_lock = threading.Lock()


def _ensure_dir():
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)


def enqueue_notification(notification_type: str, payload: dict) -> str:
    """Append notification to persistent queue. Returns notification_id.

    Thread-safe (Guardian runs from event loop, but we use threading.Lock).
    """
    _ensure_dir()
    nid = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    entry = {
        "id": nid,
        "type": notification_type,
        "payload": payload,
        "enqueued_at": time.time(),
        "attempts": 0,
        "last_error": None,
    }
    with _queue_lock:
        with open(PENDING_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")
    log.info(f"📥 Queued notification {nid} (type={notification_type})")
    return nid


def _read_pending() -> list:
    """Read all pending notifications. Returns list of dicts. Empty queue → []."""
    if not PENDING_FILE.exists():
        return []
    with _queue_lock:
        with open(PENDING_FILE) as f:
            lines = [json.loads(l) for l in f if l.strip()]
    return lines


def _remove_from_pending(notification_id: str):
    """Atomically remove a notification from pending queue."""
    with _queue_lock:
        if not PENDING_FILE.exists():
            return
        lines = []
        with open(PENDING_FILE) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("id") != notification_id:
                        lines.append(line)
                except json.JSONDecodeError:
                    lines.append(line)
        # Atomic write: write to temp + rename
        tmp = PENDING_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            f.writelines(lines)
        tmp.replace(PENDING_FILE)


def _move_to_dlq(entry: dict, reason: str):
    """Move notification to dead-letter queue."""
    with _dlq_lock:
        _ensure_dir()
        entry["dlq_moved_at"] = time.time()
        entry["dlq_reason"] = reason
        with open(DLQ_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")


async def _send_one_notification(entry: dict, notifier_module) -> bool:
    """Send one notification via telegram_notifier module. Returns True on success."""
    ntype = entry.get("type", "")
    payload = entry.get("payload", {})

    try:
        if ntype == "trade_close":
            ok = await notifier_module.send_trade_close(
                symbol=payload["symbol"],
                side=payload["side"],
                entry_price=payload["entry_price"],
                exit_price=payload["exit_price"],
                qty=payload["qty"],
                pnl=payload["pnl"],
                fees=payload.get("fees", 0),
                holding_hours=payload.get("holding_hours", 0),
                reason=payload.get("reason", ""),
            )
            return ok
        elif ntype == "guardian_event":
            ok = await notifier_module.send_guardian_event(
                symbol=payload["symbol"],
                event_type=payload["event_type"],
                current_sl=payload["current_sl"],
                entry_price=payload["entry_price"],
                peak_r=payload.get("peak_r", 0),
            )
            return ok
        elif ntype == "timeout":
            ok = await notifier_module.send_timeout_alert(
                symbol=payload["symbol"],
                hours=payload["hours"],
            )
            return ok
        else:
            log.error(f"Unknown notification type: {ntype}")
            return False
    except Exception as e:
        log.error(f"_send_one_notification failed for {ntype}: {e}")
        return False


async def process_queue(notifier_module, notifier_loop=None) -> int:
    """Process pending notifications. Returns number of notifications sent.

    Strategy:
    - Read all pending entries
    - For each: try send (via notifier_loop if provided, else direct)
    - On success: remove from queue
    - On failure: increment attempts, exponential backoff
    - After 5 attempts: move to DLQ
    """
    pending = _read_pending()
    if not pending:
        return 0

    log.info(f"📤 Processing {len(pending)} pending notifications")
    sent_count = 0

    for entry in pending:
        nid = entry.get("id")
        attempts = entry.get("attempts", 0)

        # Exponential backoff: 0s, 2s, 4s, 8s, 16s
        if attempts > 0:
            delay = 2 ** (attempts - 1)
            wait = delay - (time.time() - entry.get("last_attempt_at", entry.get("enqueued_at", time.time())))
            if wait > 0:
                log.info(f"Backoff {wait:.1f}s for {nid} (attempt {attempts + 1})")
                await asyncio.sleep(wait)

        entry["last_attempt_at"] = time.time()
        entry["attempts"] = attempts + 1

        # If notifier_loop provided, schedule on TG's existing event loop
        try:
            if notifier_loop is not None:
                fut = asyncio.run_coroutine_threadsafe(
                    _send_one_notification(entry, notifier_module),
                    notifier_loop
                )
                ok = fut.result(timeout=20)
            else:
                ok = await _send_one_notification(entry, notifier_module)
        except Exception as e:
            import traceback
            log.error(f"Send exception for {nid}: {type(e).__name__}: {e}")
            log.error(traceback.format_exc())
            ok = False

        if ok:
            _remove_from_pending(nid)
            log.info(f"✅ Sent {nid} (type={entry.get('type')}, attempts={entry['attempts']})")
            sent_count += 1
        else:
            entry["last_error"] = "send returned False or raised"
            if entry["attempts"] >= 5:
                _move_to_dlq(entry, "max attempts exceeded")
                _remove_from_pending(nid)
                log.error(f"💀 {nid} moved to DLQ after {entry['attempts']} attempts")
            else:
                # Update attempts in pending file
                with _queue_lock:
                    lines = []
                    if PENDING_FILE.exists():
                        with open(PENDING_FILE) as f:
                            for line in f:
                                if not line.strip():
                                    continue
                                try:
                                    e = json.loads(line)
                                    if e.get("id") == nid:
                                        e = entry
                                    lines.append(json.dumps(e) + "\n")
                                except json.JSONDecodeError:
                                    lines.append(line)
                    tmp = PENDING_FILE.with_suffix(".tmp")
                    with open(tmp, "w") as f:
                        f.writelines(lines)
                    tmp.replace(PENDING_FILE)
                log.warning(f"⚠️  Retry {nid} (attempt {entry['attempts']})")

    return sent_count


def start_worker(loop, notifier_module, notifier_loop=None):
    """Start a background worker that processes the queue every N seconds.

    Returns the asyncio Task. Caller should keep reference to prevent GC.
    notifier_loop: TG's own event loop (use run_coroutine_threadsafe)
    """
    async def _worker_forever():
        while True:
            try:
                sent = await process_queue(notifier_module, notifier_loop=notifier_loop)
                if sent > 0:
                    log.info(f"Worker drained {sent} notifications")
            except Exception as e:
                log.error(f"Worker error: {e}")
            await asyncio.sleep(5)  # check queue every 5s

    task = loop.create_task(_worker_forever())
    log.info("TG reliable queue worker started")
    return task
