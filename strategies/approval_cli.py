"""
strategies/approval_cli.py
Approval CLI — human reviews ARC signals, approves/rejects trades.

Usage:
    python3 -m strategies.approval_cli propose    # show current proposal
    python3 -m strategies.approval_cli approve    # approve + execute
    python3 -m strategies.approval_cli reject     # reject
    python3 -m strategies.approval_cli scan       # scan ARC → new proposal
"""
import asyncio, json, sys, logging
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))
logging.basicConfig(level=logging.INFO)

from strategies.base import MarketSnapshot
from strategies.arc.rules import evaluate as arc_evaluate
from strategies.trade_executor import (
    TradeProposal, execute_trade, save_proposal, save_trade_result,
    PROPOSAL_FILE, RESULT_FILE, check_current_positions,
)


def cmd_show():
    """Show current trade proposal."""
    from strategies.trade_executor import render_proposal_status
    print(render_proposal_status())


def cmd_approve():
    """Approve current proposal and execute."""
    if not PROPOSAL_FILE.exists():
        print("No pending proposal")
        return 1

    with PROPOSAL_FILE.open() as f:
        data = json.load(f)

    if data["status"] != "PENDING":
        print(f"Proposal already {data['status']}")
        return 1

    proposal = TradeProposal(**data)
    valid, msg = proposal.validate()
    if not valid:
        print(f"❌ Validation failed: {msg}")
        return 1

    print(f"✅ Approved: {proposal.symbol} {proposal.side}")
    proposal.status = "APPROVED"
    save_proposal(proposal)

    # Execute
    result = asyncio.run(execute_trade(proposal))
    if result["status"] == "FILLED":
        proposal.status = "EXECUTED"
        save_trade_result({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": proposal.symbol,
            "side": proposal.side,
            "entry": result["price"],
            "sl": proposal.stop_loss,
            "tp": proposal.take_profit,
            "ticket": result["ticket"],
            "status": "OPEN",
            "strategy": proposal.strategy,
        })
        print(f"🟢 Trade executed: ticket={result['ticket']} price=${result['price']:.2f}")
    else:
        print(f"❌ Execution failed: {result.get('error', 'Unknown')}")

    return 0


def cmd_reject():
    """Reject current proposal."""
    if not PROPOSAL_FILE.exists():
        print("No pending proposal")
        return 1

    with PROPOSAL_FILE.open() as f:
        data = json.load(f)
    data["status"] = "REJECTED"
    with PROPOSAL_FILE.open("w") as f:
        json.dump(data, f, indent=2)
    print(f"❌ Rejected: {data['symbol']} {data['side']}")
    return 0


def cmd_scan():
    """Get current XAUUSD price from bridge and generate ARC signal."""
    import asyncio, json

    async def get_price_and_scan():
        # Connect to MT5 bridge for price
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("192.168.1.77", 5555), timeout=5
        )
        writer.write((json.dumps({"action": "get_price", "symbol": "XAUUSD"}) + "\n").encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=10)
        price = json.loads(data.decode().strip())
        writer.close()

        bid = price.get("bid", 0)
        ask = price.get("ask", 0)
        if bid == 0 or ask == 0:
            print("❌ Cannot get XAUUSD price")
            return

        # Get positions to check if already trading
        pos_count = await check_current_positions()
        if pos_count > 0:
            print(f"⚠️ Already {pos_count} XAUUSD position(s) open. Max {MAX_POSITIONS}.")
            return

        # Build a basic snapshot from current price (simplified for live use)
        snap = MarketSnapshot(
            symbol="XAUUSD", timeframe="M5",
            open=bid, high=ask, low=bid, close=(bid + ask) / 2,
            volume=0,
            previous_day_high=ask * 1.01,
            previous_day_low=bid * 0.99,
            swing_high=ask * 1.005,
            swing_low=bid * 0.995,
        )

        signal = arc_evaluate(snap)
        if not signal:
            print("ℹ️ No ARC signal at current price")
            return

        proposal = TradeProposal(
            symbol=signal.symbol,
            side=signal.direction,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            rr=signal.risk_reward,
            confidence=signal.confidence,
            strategy=signal.strategy,
            reason=signal.reasons,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        valid, msg = proposal.validate()
        if not valid:
            print(f"❌ Signal rejected: {msg}")
            return

        save_proposal(proposal)

    asyncio.run(get_price_and_scan())


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="show",
                       choices=["show", "propose", "approve", "reject", "scan"])
    args = parser.parse_args()

    if args.command == "propose" or args.command == "show":
        cmd_show()
    elif args.command == "approve":
        return cmd_approve()
    elif args.command == "reject":
        return cmd_reject()
    elif args.command == "scan":
        cmd_scan()
    return 0


if __name__ == "__main__":
    main()
