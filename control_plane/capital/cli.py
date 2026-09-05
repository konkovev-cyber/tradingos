"""control_plane/capital/cli.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from control_plane.capital.engine import analyze, analyze_v2, save_report, save_report_v2, render_text, render_text_v2

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Capital Intelligence")
    parser.add_argument("--v2", action="store_true", help="Use lab CapitalEngine (v2)")
    args = parser.parse_args()

    if args.v2:
        data = analyze_v2()
        path = save_report_v2(data)
        print(render_text_v2(data))
    else:
        ci = analyze()
        path = save_report(ci)
        print(render_text(ci))

    print(f"\nReport saved to {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
