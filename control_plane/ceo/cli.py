"""control_plane/ceo/cli.py"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from control_plane.ceo.aggregator import aggregate, save_ceo_state, render_text

def main() -> int:
    ceo = aggregate()
    path = save_ceo_state(ceo)
    print(render_text(ceo))
    print(f"\nCEO state saved to {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
