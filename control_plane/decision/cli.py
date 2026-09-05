"""
control_plane/decision/cli.py
CLI entry point for Decision Engine.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from control_plane.decision.engine import run, save_report, render_text


def main() -> int:
    rec = run()
    path = save_report(rec)
    print(render_text(rec))
    print(f"\nReport saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
