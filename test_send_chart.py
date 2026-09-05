"""
Send a test trade chart to Telegram to prove the fix works.
Uses the actual bot token and proxy from environment.
"""
import sys, os, asyncio, json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root/tradingos")

# Load real credentials from .env
env_path = "/root/trading_brain_v4/research/execution/.env"
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if k == "TELEGRAM_BOT_TOKEN":
                os.environ["TELEGRAM_BOT_TOKEN"] = v
            elif k == "TELEGRAM_CHAT_ID":
                os.environ["TELEGRAM_CHAT_ID"] = v

print(f"TOKEN: {os.environ.get('TELEGRAM_BOT_TOKEN', 'MISSING')[:10]}...")
print(f"CHAT_ID: {os.environ.get('TELEGRAM_CHAT_ID', 'MISSING')}")


async def send_test_chart():
    from notifier.notifier import Notifier

    n = Notifier(
        token=os.environ["TELEGRAM_BOT_TOKEN"],
        chat_id=os.environ["TELEGRAM_CHAT_ID"],
    )
    await n.start()
    print("Notifier started")

    now = datetime.now(timezone.utc)

    # Simulate a realistic trade close
    await n.notify_trade_close(
        symbol="TESTUSDT",
        side="BUY",
        entry_price=1.0,
        exit_price=0.95,
        qty=10,
        pnl=-0.15,
        leverage=3,
        entry_time=now - timedelta(hours=2),
        exit_time=now,
        holding_hours=2.0,
        sl=0.95,
        tp=1.10,
        fees=0.001,
        reason="SL",
        mfe_r=1.2,
        mae_r=-0.3,
    )
    print("notify_trade_close() called — check Telegram for chart")

    await asyncio.sleep(5)
    await n.stop()
    print("Done")


if __name__ == "__main__":
    asyncio.run(send_test_chart())
