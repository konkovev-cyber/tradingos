"""
Test script for Telegram notifier via SOCKS proxy.
"""
import asyncio
import os
import sys

sys.path.insert(0, "/root")

# Load env
env_path = "/root/trading_brain_v4/research/execution/.env"
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

print("Env vars OK")
print(f"  Token: {os.environ['TELEGRAM_BOT_TOKEN'][:10]}...")
print(f"  Chat ID: {os.environ['TELEGRAM_CHAT_ID']}")
print(f"  Proxy: {os.environ.get('TELEGRAM_PROXY', 'none')}")


async def main():
    from tradingos.notifier.notifier import Notifier
    from tradingos.notifier.chart_adapter import BybitChartAdapter

    exchange = BybitChartAdapter()
    notifier = Notifier(
        token=os.environ["TELEGRAM_BOT_TOKEN"],
        chat_id=os.environ["TELEGRAM_CHAT_ID"],
        proxy_url=os.environ.get("TELEGRAM_PROXY"),
    )

    await notifier.start()
    print("Notifier started")

    # Queue all messages
    await notifier.send("🧪 Test 1: Simple text via SOCKS")
    print("Test 1 queued")

    # Trade open card
    await notifier.notify_trade_open(
        symbol="BTCUSDT",
        side="BUY",
        entry_price=60000.0,
        qty=0.01,
        sl=58000.0,
        tp=64000.0,
        reason="Test order",
        exchange=exchange,
    )
    print("Test 2 queued: trade open card")

    # Trade close card
    await notifier.notify_trade_close(
        symbol="ETHUSDT",
        side="SELL",
        entry_price=3500.0,
        exit_price=3450.0,
        qty=0.1,
        pnl=5.0,
        fees=0.5,
        holding_hours=12.5,
        reason="TP hit",
        exchange=exchange,
    )
    print("Test 3 queued: trade close card")

    # Guardian event
    await notifier.notify_guardian_event(
        symbol="DOGEUSDT",
        event_type="BE",
        current_sl=0.07,
        entry_price=0.07,
        peak_r=0.85,
    )
    print("Test 4 queued: Guardian BE event")

    # Wait for queue to flush
    print("Waiting 10s for queue to flush...")
    await asyncio.sleep(10)

    await notifier.stop()
    print("Notifier stopped. All tests complete.")


if __name__ == "__main__":
    asyncio.run(main())
