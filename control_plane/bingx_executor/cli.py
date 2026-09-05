"""control_plane/bingx_executor/cli.py"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from control_plane.bingx_executor.executor import (
    execute_set_tp,
    list_executions,
    render_state_machine,
)


def cmd_set_tp(args) -> int:
    side = "SELL" if args.position_side == "LONG" else "BUY"
    record = asyncio.run(execute_set_tp(
        symbol=args.symbol,
        side=side,
        position_side=args.position_side,
        quantity=args.quantity,
        stop_price=args.stop_price,
        max_attempts=args.max_attempts,
    ))
    print(render_state_machine(record))
    return 0 if record.state.value == "VERIFIED" else 1


def cmd_list(args) -> int:
    executions = list_executions()
    if not executions:
        print("No executions yet")
        return 0
    for ex in executions:
        icon = {"VERIFIED": "✅", "FAILED": "❌", "MANUAL_REQUIRED": "⚠️"}.get(
            ex.get("state", ""), "?"
        )
        print(f"{icon} {ex.get('record_id')} {ex.get('symbol')} {ex.get('action')} "
              f"target={ex.get('target_price')} state={ex.get('state')} "
              f"attempts={ex.get('attempts')}/{ex.get('max_attempts')} "
              f"err={ex.get('last_error', '')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="BingX Executor v1")
    sub = parser.add_subparsers(dest="command")

    p_tp = sub.add_parser("set-tp")
    p_tp.add_argument("--symbol", required=True)
    p_tp.add_argument("--position-side", choices=["LONG", "SHORT"], required=True)
    p_tp.add_argument("--quantity", type=float, required=True)
    p_tp.add_argument("--stop-price", type=float, required=True)
    p_tp.add_argument("--max-attempts", type=int, default=3)

    sub.add_parser("list")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1
    if args.command == "set-tp":
        return cmd_set_tp(args)
    if args.command == "list":
        return cmd_list(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
