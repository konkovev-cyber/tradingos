#!/usr/bin/env python3
"""
forensic_watchdog.py — CATCH THE ROGUE WRITER.

Monitors /root/tradingos/operations/trading_mode.json for writes and
records the exact timestamp + PID/process that modified it, plus
whether chattr +i was removed (which itself requires a root shell action).

Run as a long-lived background service:
  python3 /root/tradingos/tools/forensic_watchdog.py

On any modify/open-write event it appends to
  /root/tradingos/logs/forensic_incident_2026-08-24/watchdog_events.log

IMPORTANT: chattr +i makes the file immutable; a rogue that wants to
rewrite it MUST first run `chattr -i`, which is a separate admin action
that can also be detected by pollin the lsattr flag every N seconds.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/root/tradingos/operations/trading_mode.json")
EVENT_LOG = Path("/root/tradingos/logs/forensic_incident_2026-08-24/watchdog_events.log")
POLL = 2  # seconds


def log_event(msg: str, extra: dict = None):
    EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": msg,
        "file": str(TARGET),
    }
    if extra:
        rec.update(extra)
    with EVENT_LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def scan_processes_for_handle():
    """Best-effort: find any process with the target file open in write mode."""
    hits = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            fd_dir = proc / "fd"
            for fd in fd_dir.iterdir():
                try:
                    link = os.readlink(str(fd))
                    if os.path.abspath(link) == str(TARGET):
                        cmdline = ""
                        try:
                            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode()[:200]
                        except Exception:
                            pass
                        flags = ""
                        try:
                            flags = open(f"/proc/{proc.name}/fdinfo/{fd.name}").read()
                        except Exception:
                            pass
                        hits.append({"pid": proc.name, "cmd": cmdline, "fdinfo": flags[:200]})
                except (FileNotFoundError, OSError):
                    continue
        except Exception:
            continue
    return hits


def check_immutable():
    try:
        out = os.popen(f"lsattr {TARGET} 2>/dev/null").read()
        return "i" in out.split()[0] if out else None
    except Exception:
        return None


def main():
    log_event("watchdog_started", {"poll_sec": POLL})
    state = {"last_mtime": TARGET.stat().st_mtime if TARGET.exists() else 0,
             "last_hash": hashlib.sha256(TARGET.read_bytes()).hexdigest() if TARGET.exists() else "",
             "last_immutable": check_immutable()}

    while True:
        time.sleep(POLL)
        # 1. Immutable flag change (a rogue removing chattr +i is the FIRST sign)
        cur_imm = check_immutable()
        if cur_imm is not None and cur_imm != state["last_immutable"]:
            log_event("IMMUTABLE_FLAG_CHANGED", {
                "was_immutable": state["last_immutable"],
                "now_immutable": cur_imm,
                "note": "chattr +i was removed/added — requires root admin shell action"
            })
            state["last_immutable"] = cur_imm

        # 2. File content / mtime change
        if TARGET.exists():
            cur_mtime = TARGET.stat().st_mtime
            cur_hash = hashlib.sha256(TARGET.read_bytes()).hexdigest()
            if cur_mtime != state["last_mtime"] or cur_hash != state["last_hash"]:
                hits = scan_processes_for_handle()
                log_event("FILE_WRITE_DETECTED", {
                    "old_mtime": state["last_mtime"],
                    "new_mtime": cur_mtime,
                    "old_hash": state["last_hash"],
                    "new_hash": cur_hash,
                    "processes_with_handle": hits,
                })
                state["last_mtime"] = cur_mtime
                state["last_hash"] = cur_hash


if __name__ == "__main__":
    main()
