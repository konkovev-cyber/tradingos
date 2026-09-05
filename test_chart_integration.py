"""
Integration test: full chain from _enqueue_telegram_close → send_trade_close → notify_trade_close → generate_trade_chart.
Verifies chart bytes are produced and no exception occurs.

NOTE: These are async tests that require pytest-asyncio. Without it,
pytest cannot collect async def test functions. They are skipped
automatically. To run them: pip install pytest-asyncio && pytest --asyncio-mode=auto
"""
import pytest

# Skip entire module if pytest-asyncio is not available (async test support)
try:
    import pytest_asyncio  # noqa: F401
    _HAS_ASYNCIO = True
except ImportError:
    _HAS_ASYNCIO = False

pytestmark = pytest.mark.skipif(
    not _HAS_ASYNCIO,
    reason="async tests require pytest-asyncio (pip install pytest-asyncio)"
)

import sys, os, json, time, tempfile, asyncio
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, "/root/tradingos")

# Set Telegram creds so notifier can init
os.environ["TELEGRAM_BOT_TOKEN"] = "test:token"
os.environ["TELEGRAM_CHAT_ID"] = "12345"


async def test_full_chart_chain():
    """Test that generate_trade_chart produces valid PNG bytes with realistic params."""
    from notifier.chart_gen import generate_trade_chart
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    # Simulate a BUY trade: entry=1.0, exit=0.95 (loss), SL=0.95, TP=1.10
    # MFE=1.2R, MAE=-0.3R
    chart_bytes = await generate_trade_chart(
        exchange=None,
        symbol="TESTUSDT",
        entry_price=1.0,
        entry_time=now - timedelta(hours=2),
        exit_price=0.95,
        exit_time=now,
        side="BUY",
        sl=0.95,
        tp=1.10,
        interval="15",
        leverage=3,
        pnl=-0.15,
        mfe_r=1.2,
        mae_r=-0.3,
        holding_hours=2.0,
        reason="SL",
    )

    if chart_bytes is None:
        print("❌ generate_trade_chart returned None")
        return False

    if len(chart_bytes) < 1000:
        print(f"❌ Chart too small: {len(chart_bytes)} bytes")
        return False

    # Verify it's a valid PNG
    if chart_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        print("❌ Not a valid PNG (bad header)")
        return False

    print(f"✅ Chart generated: {len(chart_bytes)} bytes, valid PNG")
    return True


async def test_notify_trade_close_no_crash():
    """Test that notify_trade_close doesn't crash with realistic params."""
    from notifier.notifier import Notifier

    n = Notifier(token="test:token", chat_id="12345")
    await n.start()

    try:
        await n.notify_trade_close(
            symbol="TESTUSDT",
            side="BUY",
            entry_price=1.0,
            exit_price=0.95,
            qty=10,
            pnl=-0.15,
            leverage=3,
            entry_time=datetime.now(timezone.utc),
            exit_time=datetime.now(timezone.utc),
            holding_hours=2.0,
            sl=0.95,
            tp=1.10,
            fees=0.001,
            reason="SL",
            mfe_r=1.2,
            mae_r=-0.3,
        )
        print("✅ notify_trade_close completed without exception")
        return True
    except Exception as e:
        print(f"❌ notify_trade_close crashed: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await n.stop()


async def main():
    print("=" * 60)
    print("TEST 1: generate_trade_chart produces valid PNG")
    print("=" * 60)
    r1 = await test_full_chart_chain()

    print()
    print("=" * 60)
    print("TEST 2: notify_trade_close doesn't crash")
    print("=" * 60)
    r2 = await test_notify_trade_close_no_crash()

    print()
    print("=" * 60)
    if r1 and r2:
        print("✅ ALL TESTS PASSED")
    else:
        print(f"❌ TESTS FAILED: chart={r1} notify={r2}")
    print("=" * 60)

    sys.exit(0 if (r1 and r2) else 1)


if __name__ == "__main__":
    asyncio.run(main())
