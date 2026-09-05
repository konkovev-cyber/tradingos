"""control_plane/bingx_read/cli.py"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from control_plane.bingx_read.adapter import fetch_real_account_state, save_state, render_text


def main() -> int:
    state = asyncio.run(fetch_real_account_state())
    path = save_state(state)
    print(render_text(state))
    print(f"\nState saved to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
