"""Regression tests for singleton guard (core/singleton.py).

Bug: duplicate processes (2026-08-20 manual_bot + reality_guardian had
two instances each, causing duplicate Telegram notifications). No
mechanism prevented a second instance from starting alongside the
systemd-managed one.
Fix: flock-based singleton guard in core/singleton.py.
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/root/tradingos")


def _run_python(code: str, timeout: int = 10) -> tuple[int, str]:
    """Run python code as a subprocess; return (exit_code, stdout+stderr)."""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=timeout,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def test_singleton_first_instance_acquires():
    """First call to acquire_singleton_lock succeeds (does not exit)."""
    code = """
import sys
sys.path.insert(0, '/root/tradingos')
from core.singleton import acquire_singleton_lock
acquire_singleton_lock('test_singleton_unit')
print('ACQUIRED')
"""
    rc, out = _run_python(code, timeout=5)
    # exit 0 only if the process finished normally (not from the guard exit(1))
    # but since the process exits normally after printing, rc should be 0
    assert "ACQUIRED" in out, f"Expected ACQUIRED in output, got: {out}"


def test_singleton_second_instance_exits():
    """A second instance with the same name exits with code 1."""
    # Start a holder process that keeps the lock for 5 seconds
    holder_code = """
import sys, time
sys.path.insert(0, '/root/tradingos')
from core.singleton import acquire_singleton_lock
acquire_singleton_lock('test_singleton_unit2')
time.sleep(5)
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
    )
    time.sleep(1.5)  # let holder acquire the lock

    # Try to acquire the same lock from a second process
    second_code = """
import sys
sys.path.insert(0, '/root/tradingos')
from core.singleton import acquire_singleton_lock
acquire_singleton_lock('test_singleton_unit2')
print('BUG: second instance acquired lock!')
"""
    rc, out = _run_python(second_code, timeout=5)

    # Clean up holder
    holder.terminate()
    holder.wait(timeout=5)

    assert rc == 1, f"Second instance should exit 1, got {rc}. Output: {out}"
    assert "already running" in out, f"Expected 'already running' message, got: {out}"
    assert "BUG" not in out, "Second instance should NOT acquire the lock"


def test_singleton_lock_released_on_exit():
    """After a process exits, the lock is available again."""
    holder_code = """
import sys
sys.path.insert(0, '/root/tradingos')
from core.singleton import acquire_singleton_lock
acquire_singleton_lock('test_singleton_release')
"""
    # First process acquires and exits immediately
    rc, _ = _run_python(holder_code, timeout=5)
    assert rc == 0

    # Second process should be able to acquire immediately
    rc, out = _run_python(holder_code, timeout=5)
    assert rc == 0, f"Lock should be available after first process exited. Got rc={rc}, out={out}"


def test_singleton_different_names_coexist():
    """Different lock names do not block each other."""
    code1 = """
import sys, time
sys.path.insert(0, '/root/tradingos')
from core.singleton import acquire_singleton_lock
acquire_singleton_lock('test_singleton_A')
time.sleep(3)
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", code1],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"},
    )
    time.sleep(1)

    code2 = """
import sys
sys.path.insert(0, '/root/tradingos')
from core.singleton import acquire_singleton_lock
acquire_singleton_lock('test_singleton_B')
print('ACQUIRED_B')
"""
    rc, out = _run_python(code2, timeout=5)
    holder.terminate()
    holder.wait(timeout=5)

    assert rc == 0, f"Different name should not block. rc={rc}, out={out}"
    assert "ACQUIRED_B" in out