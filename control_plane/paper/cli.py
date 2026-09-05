"""control_plane/paper/cli.py"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from control_plane.paper.simulator import run_simulation, save_simulation, render_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Paper Simulation")
    parser.add_argument("--symbol", required=True, help="Symbol to simulate")
    args = parser.parse_args()

    sim = run_simulation(args.symbol)
    path = save_simulation(sim)
    print(render_text(sim))
    print(f"\nSimulation saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
