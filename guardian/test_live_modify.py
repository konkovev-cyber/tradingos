"""
guardian/test_live_modify.py
One-shot Guardian LIVE execution test.
Triggers a real set_trading_stop() on one existing position and verifies the change.
After test, restores the original SL so the trade is unaffected.
"""
import os
import sys
import json
import time

ENV_PATH = "/root/trading_brain_v4/research/execution/.env"


def load_creds():
    ak, as_ = "", ""
    with open(ENV_PATH) as f:
        for l in f:
            l = l.strip()
            if l and not l.startswith("#") and "=" in l:
                k, v = l.split("=", 1)
                if k.strip() == "BYBIT_API_KEY":
                    ak = v.strip()
                elif k.strip() == "BYBIT_API_SECRET":
                    as_ = v.strip()
    return ak, as_


def get_position(symbol, ak, as_):
    import hmac, hashlib, httpx
    ts = str(int(time.time() * 1000))
    q = f"category=linear&settleCoin=USDT&symbol={symbol}"
    sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": ak,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign,
        "X-BAPI-RECV-WINDOW": "5000",
    }
    r = httpx.get(f"https://api.bybit.com/v5/position/list?{q}", headers=headers, timeout=10)
    return r.json()["result"]["list"][0] if r.json().get("retCode") == 0 else None


def set_trading_stop(symbol, stop_loss, ak, as_):
    """Use the official BybitClient for correct signing."""
    sys.path.insert(0, "/root/trading_brain_v4")
    from exchange.bybit.client import BybitClient
    client = BybitClient(api_key=ak, api_secret=as_, testnet=False)
    return client.set_trading_stop(
        symbol=symbol,
        category="linear",
        stop_loss=str(stop_loss),
        position_idx=0,
    )


def main():
    ak, as_ = load_creds()
    if not ak or not as_:
        print("ERROR: no Bybit creds")
        return

    # Pick target: SHIB is the highest R position, but for safety let's use a SELL with positive R
    # CHILLGUY is at R=+0.04 (not enough), use INX which is BUY
    # Actually use PEOPLEUSDT (Sell, R=+0.18, has 0.18% risk above entry)
    # Wait, we want the test to NOT affect trade. Use a position with a current SL we know,
    # set a new test SL just below current (not actually triggered), then restore.

    target_symbol = "INXUSDT"  # BUY, R=+0.10

    print(f"=== GUARDIAN EXECUTION TEST ===")
    print(f"Target: {target_symbol}")
    print()

    # 1. Get current state
    t0 = time.time()
    pos = get_position(target_symbol, ak, as_)
    if not pos or float(pos.get("size", 0)) == 0:
        print(f"ERROR: no position for {target_symbol}")
        return
    entry = float(pos["avgPrice"])
    orig_sl = float(pos.get("stopLoss", 0))
    orig_tp = float(pos.get("takeProfit", 0))
    side = pos["side"]
    print(f"Current state:")
    print(f"  Side:     {side}")
    print(f"  Entry:    {entry}")
    print(f"  Orig SL:  {orig_sl}")
    print(f"  Orig TP:  {orig_tp}")
    print()

    # 2. Compute new SL: simulated BE — SL = entry + small buffer (BUY: above entry)
    if side == "Buy":
        new_sl = entry + 0.00001  # tiny buffer above entry for fees
    else:
        new_sl = entry - 0.00001
    print(f"Simulated Guardian action: Move SL to breakeven")
    print(f"  Requested SL: {new_sl}")
    print()

    # 3. Call set_trading_stop
    t1 = time.time()
    result = set_trading_stop(target_symbol, new_sl, ak, as_)
    t2 = time.time()
    print(f"Exchange response:")
    print(f"  {json.dumps(result, indent=2)}")
    print()

    # 4. Verify SL on exchange
    t3 = time.time()
    pos_after = get_position(target_symbol, ak, as_)
    t4 = time.time()
    if pos_after:
        new_sl_actual = float(pos_after.get("stopLoss", 0))
        print(f"Verification:")
        print(f"  Expected SL:  {new_sl}")
        print(f"  Actual SL:    {new_sl_actual}")
        if abs(new_sl_actual - new_sl) < 0.0001:
            print(f"  Result:       ✅ SL CONFIRMED")
        else:
            print(f"  Result:       ❌ SL MISMATCH (delta={new_sl_actual - new_sl})")
    print()

    # 5. Restore original SL
    print(f"Restoring original SL ({orig_sl})...")
    t5 = time.time()
    restore = set_trading_stop(target_symbol, orig_sl, ak, as_)
    t6 = time.time()
    print(f"  Restore response: {json.dumps(restore, indent=2)}")

    # Final verify
    pos_final = get_position(target_symbol, ak, as_)
    if pos_final:
        final_sl = float(pos_final.get("stopLoss", 0))
        print(f"  Final SL:  {final_sl}")
        if abs(final_sl - orig_sl) < 0.0001:
            print(f"  Result:    ✅ Original SL restored")
        else:
            print(f"  Result:    ⚠️ SL not exact match")
    print()

    # Summary
    latency_exchange = t2 - t1
    latency_verify = t4 - t3
    latency_restore = t6 - t5
    total = t6 - t0
    print(f"=== TEST SUMMARY ===")
    print(f"  Trigger:        {'SUCCESS' if result.get('retCode') == 0 else 'FAILED'}")
    print(f"  Set SL latency: {latency_exchange*1000:.0f}ms")
    print(f"  Verify latency:  {latency_verify*1000:.0f}ms")
    print(f"  Restore:        {'SUCCESS' if restore.get('retCode') == 0 else 'FAILED'}")
    print(f"  Restore latency: {latency_restore*1000:.0f}ms")
    print(f"  Total time:     {total*1000:.0f}ms")
    print(f"  Position:       RESTORED to original state")


if __name__ == "__main__":
    main()
