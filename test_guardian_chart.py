"""
Send a test GUARDIAN chart to Telegram to prove the fix works.
Shows: entry, old SL (red dashed), new SL (green solid), current price.
"""
import sys, os, asyncio
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root/tradingos")

# Load real credentials
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


async def send_test_guardian_chart():
    from notifier.notifier import Notifier

    n = Notifier(
        token=os.environ["TELEGRAM_BOT_TOKEN"],
        chat_id=os.environ["TELEGRAM_CHAT_ID"],
    )
    await n.start()
    print("Notifier started")

    now = datetime.now(timezone.utc)

    # Simulate a BE event: entry=1.0, old SL=0.95, new SL=1.0 (breakeven)
    # Current price=1.03, peak_r=+0.83R
    await n.notify_guardian_event(
        symbol="TESTUSDT",
        event_type="BE",
        current_sl=1.0,        # new SL (breakeven)
        entry_price=1.0,
        peak_r=0.83,
        old_sl=0.95,           # original SL
        current_price=1.03,    # current market price
        side="BUY",
        leverage=3,
        entry_time=now - timedelta(hours=2),
    )
    print("notify_guardian_event(BE) called — check Telegram for chart")

    await asyncio.sleep(5)

    # Also test PARTIAL event
    await n.notify_guardian_event(
        symbol="TESTUSDT",
        event_type="PARTIAL",
        current_sl=1.025,      # new SL (entry + 0.5R)
        entry_price=1.0,
        peak_r=1.2,
        old_sl=0.95,           # original SL
        current_price=1.06,    # current market price
        side="BUY",
        leverage=3,
        entry_time=now - timedelta(hours=2),
    )
    print("notify_guardian_event(PARTIAL) called — check Telegram for chart")

    await asyncio.sleep(5)
    await n.stop()
    print("Done")


if __name__ == "__main__":
    asyncio.run(send_test_guardian_chart())
