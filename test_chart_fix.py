"""
Test that _record_trade_closure() does NOT raise NameError on 'pos' variable.
This was the root cause of charts disappearing after Round 5 fix.
"""
import sys, os, json, time, tempfile
from pathlib import Path

# Mock Bybit API calls to prevent real network requests
import unittest
from unittest.mock import patch

# Patch BEFORE importing the module
sys.path.insert(0, "/root/tradingos")

# We'll test the function directly by importing the module
# and calling _record_trade_closure with a mock state_entry

def test_record_trade_closure_no_nameerror():
    """_record_trade_closure must not crash with NameError on 'pos'."""
    from guardian.reality_guardian import _record_trade_closure, TRADE_RESULTS_DIR, FINAL_TRADE_LOG

    # Use temp dirs to avoid polluting real logs
    with tempfile.TemporaryDirectory() as tmpdir:
        # Redirect log paths to temp
        orig_trade_dir = TRADE_RESULTS_DIR
        orig_final_log = FINAL_TRADE_LOG

        try:
            # Override paths
            import guardian.reality_guardian as rg
            rg.TRADE_RESULTS_DIR = Path(tmpdir)
            rg.FINAL_TRADE_LOG = Path(tmpdir) / "guardian_effectiveness.jsonl"

            # Mock _fetch_actual_close_price to return a fixed value (no network)
            original_fetch = rg._fetch_actual_close_price
            rg._fetch_actual_close_price = lambda sym, side, fallback: 0.95

            # Mock _enqueue_telegram_close to verify it's called
            call_log = []
            original_enqueue = rg._enqueue_telegram_close
            rg._enqueue_telegram_close = lambda *a, **kw: call_log.append(("called", kw))

            # Simulate a BUY trade state_entry
            state_entry = {
                "mfe_peak": 1.2,
                "mae_trough": -0.3,
                "be_fired": True,
                "partial_fired": False,
                "tight_fired": False,
                "entry": 1.0,
                "side": "Buy",
                "entry_to_sl_risk": 0.05,
                "size": 10,
                "entry_time": time.time() - 3600,
                "hold_hours": 1.0,
            }

            # This must NOT raise NameError
            try:
                _record_trade_closure("TESTUSDT", state_entry)
                print("✅ _record_trade_closure() completed without NameError")
            except NameError as e:
                print(f"❌ NameError raised: {e}")
                return False
            except Exception as e:
                # Other exceptions are OK (network, etc) — NameError is the bug
                print(f"ℹ️  Other exception (not NameError): {type(e).__name__}: {e}")
                # Still check if _enqueue_telegram_close was called
                if call_log:
                    print(f"✅ _enqueue_telegram_close WAS called ({len(call_log)} time(s))")
                else:
                    print("❌ _enqueue_telegram_close was NOT called")
                    return False
                return True

            # Verify _enqueue_telegram_close was called
            if call_log:
                print(f"✅ _enqueue_telegram_close WAS called ({len(call_log)} time(s))")
                kw = call_log[0][1]
                print(f"   symbol={kw.get('symbol')} side={kw.get('side')}")
                print(f"   entry={kw.get('entry_price')} exit={kw.get('exit_price')}")
                print(f"   mfe_r={kw.get('mfe_r')} mae_r={kw.get('mae_r')}")
                return True
            else:
                print("❌ _enqueue_telegram_close was NOT called")
                return False

        finally:
            # Restore originals
            rg.TRADE_RESULTS_DIR = orig_trade_dir
            rg.FINAL_TRADE_LOG = orig_final_log
            rg._fetch_actual_close_price = original_fetch
            rg._enqueue_telegram_close = original_enqueue


if __name__ == "__main__":
    success = test_record_trade_closure_no_nameerror()
    sys.exit(0 if success else 1)
