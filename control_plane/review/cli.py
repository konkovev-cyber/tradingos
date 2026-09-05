"""control_plane/review/cli.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from control_plane.review.report import generate_report, save_report, render_text


def main() -> int:
    review = generate_report()
    path = save_report(review)
    print(render_text(review))
    print(f"\nReview saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
