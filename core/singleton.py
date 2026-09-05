"""
core/singleton.py — flock-based singleton guard for long-running services.

Prevents duplicate instances of singleton daemons (reality_guardian,
manual_bot, run_observation, ...). A second instance — whether started
manually (`python3 reality_guardian.py`) or by a misconfigured unit —
exits immediately instead of double-polling the exchange and sending
duplicate Telegram notifications (the 2026-08-20 duplicate-process
incident and the 2026-08-24 TQQQ spam incident both involved more than
one active notifier path).

Usage at the top of a service entrypoint:

    from core.singleton import acquire_singleton_lock
    acquire_singleton_lock("reality_guardian")  # exits 1 if already running

The lock is an OS-level flock on /run/lock/<name>.lock: it is released
automatically when the process dies (crash, kill, restart), so systemd
Restart=always keeps working.
"""
import fcntl
import os
import sys
from pathlib import Path

_LOCK_DIR = Path("/run/lock")
# Keep a reference so the fd stays open for the process lifetime
_lock_handle = None


def acquire_singleton_lock(name: str) -> None:
    """Acquire an exclusive flock for `name` or exit(1).

    Idempotent per process: the first call stores the handle module-wide;
    a second call with the same name in the same process is a no-op.
    """
    global _lock_handle
    if _lock_handle is not None:
        return  # already holding the lock in this process
    lock_path = _LOCK_DIR / f"{name}.singleton.lock"
    try:
        _LOCK_DIR.mkdir(parents=True, exist_ok=True)
        fh = lock_path.open("w")
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            existing_pid = ""
            try:
                existing_pid = lock_path.read_text().strip()
            except Exception:
                pass
            print(
                f"[singleton] Another instance of '{name}' is already running"
                f"{f' (pid={existing_pid})' if existing_pid else ''} — exiting.",
                flush=True,
            )
            sys.exit(1)
        fh.write(str(os.getpid()))
        fh.flush()
        _lock_handle = fh  # keep fd open for process lifetime
    except SystemExit:
        raise
    except Exception:
        # Lock acquisition problems must not take down the service: degrade
        # to running without the guard (same behavior as before this module).
        print(f"[singleton] lock unavailable for '{name}' — continuing without guard", flush=True)
