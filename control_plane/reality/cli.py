"""control_plane/reality/cli.py"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from control_plane.reality.simulator import simulate_reality, save_result, render_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Reality Engine Simulation")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--action", default="TAKE_PARTIAL", choices=["HOLD", "MOVE_SL_BE", "TAKE_PARTIAL"])
    parser.add_argument("--hold-pnl", type=float, default=0.0)
    parser.add_argument("--action-pnl", type=float, default=0.0)
    args = parser.parse_args()

    result = simulate_reality(
        symbol=args.symbol,
        action=args.action,
        hold_pnl=args.hold_pnl,
        action_pnl=args.action_pnl,
    )
    path = save_result(result)
    print(render_text(result))
    print(f"\nResult saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
