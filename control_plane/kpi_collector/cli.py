"""
control_plane/kpi_collector/cli.py
KPI Collector CLI entry point.

Usage:
    python3 -m control_plane.kpi_collector.cli run     # one cycle
    python3 -m control_plane.kpi_collector.cli status  # show current state
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from control_plane.kpi_collector.sampler import collect_samples
from control_plane.kpi_collector.state import load_state, save_state, add_samples


def cmd_run() -> int:
    state = load_state()
    new_samples = collect_samples()
    before_n = len(state.samples)
    add_samples(state, new_samples)
    after_n = len(state.samples)
    path = save_state(state)

    print("=" * 56)
    print("  KPI COLLECTOR v1 — single cycle")
    print("=" * 56)
    print(f"  Samples before: {before_n}")
    print(f"  Samples after:  {after_n}")
    print(f"  TE:    {state.te.status:14s} {state.te.samples:3d} / 20  value={state.te.value}")
    print(f"  ESR:   {state.esr.status:14s} {state.esr.samples:3d} / 20  value={state.esr.value}")
    print(f"  ERG:   {state.erg.status:14s} {state.erg.samples:3d} / 20  value={state.erg.value}")
    print()
    print(f"  State saved to {path}")
    return 0


def cmd_status() -> int:
    state = load_state()
    print("=" * 56)
    print("  KPI COLLECTOR v1 — current state")
    print("=" * 56)
    print(f"  Last updated: {state.last_updated}")
    print(f"  TE:    {state.te.status:14s} {state.te.samples:3d} / 20  value={state.te.value}")
    print(f"  ESR:   {state.esr.status:14s} {state.esr.samples:3d} / 20  value={state.esr.value}")
    print(f"  ERG:   {state.erg.status:14s} {state.erg.samples:3d} / 20  value={state.erg.value}")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 -m control_plane.kpi_collector.cli {run|status}")
        return 1
    cmd = sys.argv[1]
    if cmd == "run":
        return cmd_run()
    if cmd == "status":
        return cmd_status()
    print(f"Unknown command: {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
