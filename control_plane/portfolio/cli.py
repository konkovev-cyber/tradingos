"""control_plane/portfolio/cli.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from control_plane.portfolio.engine import analyze, save_report, render_text

def main() -> int:
    data = analyze()
    path = save_report(data)
    print(render_text(data))
    print(f"\nReport saved to {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
