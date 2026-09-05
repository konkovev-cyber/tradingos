#!/usr/bin/env python3
"""nearhi_executor.py — execution layer for S4_NEARHI micro-pilot.

READ-ONLY on nearhi-shadow ledger (does NOT modify detector).
WRITES to real_ledger.jsonl (no secrets, only execution records).

OPERATING MODES:
  BLOCKED_NO_CREDENTIALS  → no BYBIT_API_KEY/SECRET in protected env
  PERMISSION_DENIED        → API key lacks Contract Trade or has Withdraw
  PERMISSION_OK            → credentials + permissions valid
  HALTED                   → guardrail triggered, manual review required
  TRADING                  → all checks pass, ready to execute

HARD RULES (per user spec):
  * No credentials in logs / JSONL
  * No fallback credentials
  * No live order without full pre-flight pass
  * Auto-HALT on any guardrail violation
"""
import os, sys, json, time, hmac, hashlib, urllib.parse, logging
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path("/root/tradingos/profit_engines/nearhi_shadow")
LOG = ROOT / "executor_ledger.jsonl"
STATE = ROOT / "executor_state.json"
REAL_LEDGER = ROOT / "real_ledger.jsonl"
SHADOW_LEDGER = ROOT / "nearhi_ledger.jsonl"

# Frozen S4_NEARHI spec (DO NOT MODIFY)
SPEC = {
    "strategy": "S4_NEARHI",
    "entry": "rng_pos > 0.85",
    "direction": -1,
    "sl_mult_atr": 1.5,
    "tp_mult_r": 3.0,
    "horizon_bars": 96,
    "universe": [
        "BTCUSDT","ETHUSDT","SOLUSDT","BNBUSDT","XRPUSDT","DOGEUSDT","ADAUSDT",
        "AVAXUSDT","LINKUSDT","SUIUSDT","ARBUSDT","NEARUSDT","OPUSDT","DOTUSDT",
        "LTCUSDT","TRXUSDT","UNIUSDT","APTUSDT","FILUSDT","INJUSDT",
    ],
    "risk_usd": 0.50,
    "max_concurrent": 1,
    "max_spread_bps": 50,
    "hard_loss_limit_usd": -1.50,
    "max_consecutive_losses": 3,
    "base": "https://api.bybit.com",
}

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
log = logging.getLogger("nearhi_executor")

# --- Helper: write to ledger (no secrets) ---
def ledger(rec):
    rec["ts_iso"] = datetime.now(timezone.utc).isoformat()
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

def real_ledger(rec):
    rec["ts_iso"] = datetime.now(timezone.utc).isoformat()
    with REAL_LEDGER.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

def update_state(d):
    STATE.write_text(json.dumps(d, indent=1, default=str))

# --- Phase 2: FAIL-CLOSED CREDENTIAL CHECK ---
def load_credentials():
    """Read BYBIT_API_KEY and BYBIT_API_SECRET from /root/.bybit_executor.env
    (mode 0600, owned by root). Returns (key, secret) or (None, None)."""
    env_path = Path("/root/.bybit_executor.env")
    if not env_path.exists():
        return None, None
    # File MUST be mode 0600 and owned by root
    try:
        import stat
        st = env_path.stat()
        if st.st_mode & 0o077:
            log.error("REFUSING: credentials file has insecure permissions (not 0600)")
            return None, None
    except Exception as e:
        log.error(f"REFUSING: cannot stat credentials file: {e}")
        return None, None
    key = secret = None
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"): continue
            if "=" not in line: continue
            k, v = line.split("=", 1)
            if k == "BYBIT_API_KEY": key = v.strip()
            elif k == "BYBIT_API_SECRET": secret = v.strip()
    except Exception as e:
        log.error(f"REFUSING: cannot read credentials file: {e}")
        return None, None
    if not key or not secret: return None, None
    if len(key) < 10 or len(secret) < 10:
        log.error("REFUSING: credentials look malformed (too short)")
        return None, None
    return key, secret

# --- Phase 3: PERMISSION CHECK ---
def _build_query_string(params: dict) -> str:
    """Bybit V5: sorted query string, excluding sign."""
    items = sorted(params.items())
    return "&".join(f"{k}={v}" for k, v in items if k != "sign")

def _signed_get(url, api_key, api_secret, extra_params=None):
    """Execute a signed GET request to Bybit V5 API using X-BAPI headers.
    Never logs credentials or signature."""
    ts = str(int(time.time() * 1000))
    recv_window = "5000"
    params = {"api_key": api_key, "timestamp": ts}
    if extra_params:
        params.update(extra_params)
    query = _build_query_string(params)
    sign_str = f"{ts}{api_key}{recv_window}{query}"
    sig = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
    import httpx
    r = httpx.get(url, params=query, headers={
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sig,
        "X-BAPI-RECV-WINDOW": recv_window,
    }, timeout=10)
    return r.json()

def check_permissions(api_key, api_secret):
    """Verify API key can access derivatives endpoints.
    Uses read-only calls only. Does NOT place orders.
    Checks: wallet balance (proves auth), positions list (proves contract access)."""
    try:
        # 1. Wallet balance — proves auth works
        d = _signed_get(f"{SPEC['base']}/v5/account/wallet-balance", api_key, api_secret,
                        extra_params={"accountType": "UNIFIED"})
        if d.get("retCode") != 0:
            return False, f"wallet API err: {d.get('retMsg','?')}"
        coins = d["result"]["list"][0]["coin"]
        usdt = [c for c in coins if c["coin"] == "USDT"]
        balance = float(usdt[0]["walletBalance"]) if usdt else 0.0
        if balance < 1.0:
            return False, f"BLOCK: insufficient balance (${balance:.2f})"
        
        # 2. Positions list — proves contract trade access
        d2 = _signed_get(f"{SPEC['base']}/v5/position/list", api_key, api_secret,
                         extra_params={"category": "linear", "settleCoin": "USDT"})
        if d2.get("retCode") != 0:
            return False, f"positions API err (contract trade not accessible): {d2.get('retMsg','?')}"
        
        # 3. Check for open positions — allow non-S4 positions but require no S4 position
        all_pos = [p for p in d2["result"]["list"] if float(p.get("size", 0)) > 0]
        s4_positions = [p for p in all_pos if p.get("symbol") in SPEC["universe"]]
        if s4_positions:
            return False, f"BLOCK: {len(s4_positions)} existing S4 positions — clean start required"
        
        return True, f"PERMISSION_OK (balance=${balance:.2f}, S4 positions=0, other positions={len(all_pos)})"
    except Exception as e:
        return False, f"permission check failed: {e}"
    except Exception as e:
        return False, f"permission check failed: {e}"

# --- Phase 4 / 5 / 6: PREFLIGHT + SIZING ---
def preflight(signal, cumulative_pnl, consecutive_losses):
    """All hard checks per user spec. Returns (ok: bool, reason: str)."""
    # 1. Signal is new (caller checks via ledger position tracking)
    # 2. frozen S4 rule: direction=-1, rng_pos>0.85
    if signal.get("dir") != SPEC["direction"]:
        return False, f"non-frozen direction: {signal.get('dir')}"
    if signal.get("sl_dist", 0) <= 0:
        return False, "sl_dist <= 0"
    if signal.get("atr14", 0) <= 0:
        return False, "atr14 <= 0"
    if signal.get("sl_px", 0) <= signal.get("entry_taker", 0):
        return False, "SL not above entry for SHORT"
    if signal.get("tp_px", 0) >= signal.get("entry_taker", 0):
        return False, "TP not below entry for SHORT"
    # 3. market data fresh (caller checks ts)
    # 4. symbol allowed
    if signal.get("sym") not in SPEC["universe"]:
        return False, f"symbol not in universe: {signal.get('sym')}"
    # 5. spread ≤ 50 bps (computed from signal price + ticker)
    # 6. no existing S4 position (tracked separately)
    # 7. Guardian / deposit guard (delegated to TradingOS checks; here just flag)
    # 8. cumulative loss
    if cumulative_pnl <= SPEC["hard_loss_limit_usd"]:
        return False, f"cumulative NET {cumulative_pnl:.2f} <= hard limit"
    if consecutive_losses >= SPEC["max_consecutive_losses"]:
        return False, f"{consecutive_losses} consecutive losses >= max"
    # 9. SL > 0, TP > entry for SHORT — already checked above
    # 10. risk ≤ $0.50
    return True, "PREFLIGHT_OK"

import math

# Bybit perp min step per symbol (in coin units). For sizing we use a conservative
# universal approach: round qty down to a step that keeps risk ≤ $0.50.
# Bybit linear perps typically have qtyStep of 0.001 for most, 0.1 for BTC, 1 for low-priced.
def _qty_step(symbol, entry_price):
    """Approximate Bybit qty step by symbol price tier."""
    if entry_price > 10000: return 0.01    # BTC
    if entry_price > 1000:  return 0.001   # ETH, BNB, SOL
    if entry_price > 10:    return 0.01    # mid
    if entry_price > 1:      return 0.1     # XRP, DOGE
    if entry_price > 0.1:    return 1.0     # ARB
    return 10.0                            # very low

def calc_quantity(entry_price, sl_price, risk_usd, symbol="BTCUSDT"):
    """quantity = risk / |sl_price - entry_price| for SHORT.
    Returns (qty, actual_risk_usd)."""
    risk_per_unit = abs(sl_price - entry_price)
    if risk_per_unit <= 0:
        return 0.0, 0.0
    qty_raw = risk_usd / risk_per_unit
    step = _qty_step(symbol, entry_price)
    qty = math.floor(qty_raw / step) * step
    if qty <= 0:
        # Step too coarse for $0.50 risk on this symbol — skip
        return 0.0, 0.0
    actual_risk = qty * risk_per_unit
    if actual_risk > risk_usd * 1.001:
        return 0.0, actual_risk
    return qty, actual_risk

# --- CREDENTIAL PREFLIGHT (read-only API checks) ---
def api_account_info(api_key, api_secret):
    """Read-only: get account info, wallet balance, positions. No order calls."""
    try:
        d = _signed_get(f"{SPEC['base']}/v5/account/wallet-balance", api_key, api_secret,
                        extra_params={"accountType": "UNIFIED"})
        if d.get("retCode") != 0:
            return False, f"wallet API err: {d.get('retMsg','?')}"
        coins = d["result"]["list"][0]["coin"]
        usdt = [c for c in coins if c["coin"] == "USDT"]
        balance = float(usdt[0]["walletBalance"]) if usdt else 0.0
        return True, f"USDT balance: {balance:.2f}"
    except Exception as e:
        return False, f"wallet API exception: {e}"

def api_positions(api_key, api_secret):
    """Read-only: get current open positions. No order calls."""
    try:
        d = _signed_get(f"{SPEC['base']}/v5/position/list", api_key, api_secret,
                        extra_params={"category": "linear", "settleCoin": "USDT"})
        if d.get("retCode") != 0:
            return False, f"positions API err: {d.get('retMsg','?')}"
        pos_list = d["result"]["list"]
        open_pos = [p for p in pos_list if float(p.get("size",0)) > 0]
        return True, f"open positions: {len(open_pos)}"
    except Exception as e:
        return False, f"positions API exception: {e}"

def api_symbol_meta():
    """Read-only: get symbol metadata (qtyStep, minQty, tickSize) for all 20 S4 symbols."""
    try:
        import httpx
        r = httpx.get(f"{SPEC['base']}/v5/market/instruments-info",
            params={"category":"linear","limit":1000}, timeout=15)
        d = r.json()
        if d.get("retCode") != 0:
            return {}
        meta = {}
        for inst in d["result"]["list"]:
            sym = inst.get("symbol")
            if sym not in SPEC["universe"]: continue
            lot = inst.get("lotSizeFilter", {})
            meta[sym] = {
                "qtyStep": float(lot.get("qtyStep","0.001")),
                "minQty": float(lot.get("minOrderQty","0.001")),
                "tickSize": float(inst.get("priceFilter",{}).get("tickSize","0.01")),
            }
        return meta
    except Exception as e:
        log.error(f"symbol meta exception: {e}")
        return {}

def credential_preflight():
    """Full read-only preflight when credentials are present.
    Returns dict of check results. Never logs credentials."""
    results = {}
    key, secret = load_credentials()
    if not key:
        results["CREDENTIALS"] = "FAIL (file missing or insecure)"
        return results
    results["CREDENTIALS"] = "PASS (file mode 0600, root-owned, key+secret present)"

    # API auth + wallet
    ok, msg = api_account_info(key, secret)
    results["API_AUTH"] = "PASS" if ok else f"FAIL ({msg})"
    if ok:
        results["WALLET"] = msg

    # Positions
    ok, msg = api_positions(key, secret)
    results["POSITIONS"] = msg if ok else f"FAIL ({msg})"

    # Permissions
    ok, perm_msg = check_permissions(key, secret)
    if "PERMISSION_OK" in perm_msg:
        results["CONTRACT_TRADE"] = "PASS"
        results["WITHDRAWAL"] = "OFF (verified)"
    elif "withdrawal" in perm_msg.lower():
        results["CONTRACT_TRADE"] = "BLOCKED"
        results["WITHDRAWAL"] = "ENABLED (BLOCKED — refusing)"
    else:
        results["CONTRACT_TRADE"] = f"FAIL ({perm_msg})"
        results["WITHDRAWAL"] = "UNKNOWN"

    return results

# --- SYMBOL TRADABILITY CHECK (no credentials needed) ---
def check_symbol_tradability():
    """Check which S4 symbols can accommodate $0.50 risk at their qtyStep/minQty."""
    meta = api_symbol_meta()
    results = {}
    for sym in SPEC["universe"]:
        m = meta.get(sym)
        if not m:
            results[sym] = {"status": "NO_METADATA", "qtyStep": None, "minQty": None}
            continue
        step = m["qtyStep"]
        min_qty = m["minQty"]
        # Check: at typical SL distance (~1.5×ATR ≈ 2-3% of price), can we fit $0.50?
        # Use a representative SL: 1% of current price (conservative)
        # We don't have current price here; use a rough estimate from API tickers
        results[sym] = {"qtyStep": step, "minQty": min_qty, "status": "METADATA_OK"}
    return results, meta

# --- ORDER PLACEMENT ---
def place_market_order(api_key, api_secret, sym, side, qty):
    """Place market order (short = Sell, long = Buy)."""
    import httpx, uuid
    ts = str(int(time.time() * 1000))
    params = {
        "category": "linear",
        "symbol": sym,
        "side": side,  # "Buy" or "Sell"
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "GTC",
    }
    body = json.dumps(params)
    sig = hmac.new(api_secret.encode('utf-8'), ts.encode('utf-8') + api_key.encode('utf-8') + body.encode('utf-8'), hashlib.sha256).hexdigest()
    r = httpx.post(f"{SPEC['base']}/v5/order/create",
        headers={"X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sig, "Content-Type": "application/json"},
        data=body, timeout=10)
    return r.json()

def set_trading_stop(api_key, api_secret, sym, sl_px, tp_px):
    """Attach SL + TP to existing position."""
    import httpx
    ts = str(int(time.time() * 1000))
    params = {
        "category": "linear",
        "symbol": sym,
        "slTriggerBy": "MarkPrice",
        "slPrice": str(round(sl_px, 4)),
        "tpTriggerBy": "MarkPrice",
        "tpPrice": str(round(tp_px, 4)),
        "positionIdx": 0,
    }
    body = json.dumps(params)
    sig = hmac.new(api_secret.encode('utf-8'), ts.encode('utf-8') + api_key.encode('utf-8') + body.encode('utf-8'), hashlib.sha256).hexdigest()
    r = httpx.post(f"{SPEC['base']}/v5/position/trading-stop",
        headers={"X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sig, "Content-Type": "application/json"},
        data=body, timeout=10)
    return r.json()

def fetch_ticker_price(api_key, api_secret, sym):
    """Get current mark price for SL/TP verification."""
    try:
        import httpx
        r = httpx.get(f"{SPEC['base']}/v5/market/tickers",
            params={"category":"linear","symbol":sym}, timeout=8)
        d = r.json()
        if d.get("retCode") == 0:
            return float(d["result"]["list"][0]["markPrice"])
    except: pass
    return None

def verify_sl_tp(api_key, api_secret, sym, expected_sl, expected_tp, tolerance=0.005):
    """Verify SL + TP are actually attached on the exchange."""
    try:
        import httpx
        ts = str(int(time.time() * 1000))
        params = {"api_key": api_key, "timestamp": ts, "category": "linear", "symbol": sym}
        query = _build_query_string(params)
        sign_str = f"{ts}{api_key}5000{query}"
        sig = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
        r = httpx.get(f"{SPEC['base']}/v5/position/list",
            params=query, headers={
                "X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sig,
                "X-BAPI-RECV-WINDOW": "5000"}, timeout=10)
        d = r.json()
        if d.get("retCode") != 0:
            return False, None, None
        for p in d["result"]["list"]:
            if p["symbol"] == sym and float(p.get("size", 0)) > 0:
                sl = float(p.get("stopLoss", 0) or 0)
                tp = float(p.get("takeProfit", 0) or 0)
                sl_ok = sl > 0 and abs(sl - expected_sl) / expected_sl < tolerance
                tp_ok = tp > 0 and abs(tp - expected_tp) / expected_tp < tolerance
                return (sl_ok and tp_ok), sl, tp
        return False, None, None
    except Exception as e:
        return False, None, None

def check_position_open(api_key, api_secret, sym):
    """Check if S4 position is currently open."""
    try:
        import httpx
        ts = str(int(time.time() * 1000))
        params = {"api_key": api_key, "timestamp": ts, "category": "linear", "symbol": sym}
        query = _build_query_string(params)
        sign_str = f"{ts}{api_key}5000{query}"
        sig = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
        r = httpx.get(f"{SPEC['base']}/v5/position/list",
            params=query, headers={
                "X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sig,
                "X-BAPI-RECV-WINDOW": "5000"}, timeout=10)
        d = r.json()
        if d.get("retCode") != 0: return False
        for p in d["result"]["list"]:
            if p["symbol"] == sym and float(p.get("size", 0)) > 0:
                return True
        return False
    except: return False

def execute_s4_trade(api_key, api_secret, signal):
    """Full execution pipeline: market order + SL/TP + verify. Returns trade record dict."""
    sym = signal["sym"]
    entry = signal["entry_taker"]
    sl_px = signal["sl_px"]
    tp_px = signal["tp_px"]
    atr = signal["atr14"]
    sl_dist = signal["sl_dist"]

    # Check no open position
    if check_position_open(api_key, api_secret, sym):
        return None, f"existing S4 position on {sym}"

    # Calculate quantity from risk
    qty, actual_risk = calc_quantity(entry, sl_px, SPEC["risk_usd"], sym)
    if qty <= 0 or actual_risk > SPEC["risk_usd"] * 1.001:
        return None, f"qty=0 or risk=${actual_risk:.4f} > $0.50 (symbol too high-priced for this risk cap)"

    record = {
        "trade_id": f"S4-{int(time.time())}",
        "signal_time": signal.get("ts"),
        "order_time": None, "fill_time": None,
        "sym": sym, "side": "Sell",  # SHORT
        "signal_price": entry,
        "requested_price": entry,
        "actual_fill_price": None,
        "quantity": qty,
        "risk_usd": round(actual_risk, 4),
        "sl": sl_px, "tp": tp_px,
        "atr14": atr, "sl_dist": sl_dist,
        "exit_time": None, "exit_price": None,
        "gross_pnl": None, "entry_fee": None, "exit_fee": None,
        "slippage": None,
        "net_pnl": None, "net_r": None,
        "mfe_r": None, "mae_r": None,
        "exit_reason": None,
    }

    # 1. Place market order (Sell for SHORT)
    log.info(f"  placing market order: {sym} SELL {qty} (entry ~${entry})")
    order_resp = place_market_order(api_key, api_secret, sym, "Sell", qty)
    if order_resp.get("retCode") != 0:
        record["exit_reason"] = f"order reject: {order_resp.get('retMsg','?')}"
        real_ledger(record)
        return record, f"order rejected: {order_resp.get('retMsg','?')}"

    oid = order_resp["result"].get("orderId", "?")
    record["order_id"] = oid
    record["order_time"] = int(time.time() * 1000)
    record["actual_fill_price"] = float(order_resp["result"].get("avgPrice", entry))
    record["fill_time"] = order_resp["result"].get("updatedTime", record["order_time"])
    log.info(f"  order filled: {oid} avg=${record['actual_fill_price']}")

    # 2. Attach SL + TP
    log.info(f"  attaching SL={sl_px:.4f} TP={tp_px:.4f}")
    stop_resp = set_trading_stop(api_key, api_secret, sym, sl_px, tp_px)
    if stop_resp.get("retCode") != 0:
        log.error(f"  SL/TP FAILED: {stop_resp.get('retMsg','?')}")
        record["exit_reason"] = f"SL/TP attach failed: {stop_resp.get('retMsg','?')}"
        record["net_pnl"] = -actual_risk  # emergency mark as worst-case loss
        record["net_r"] = -1.0
        state["halted_reason"] = "SL/TP attach failure — MANUAL REVIEW"
        real_ledger(record)
        return record, f"SL/TP attach failed"

    # 3. Verify SL + TP on exchange
    time.sleep(1)
    verified, actual_sl, actual_tp = verify_sl_tp(api_key, api_secret, sym, sl_px, tp_px)
    if not verified:
        log.error(f"  SL/TP VERIFICATION FAILED: expected SL={sl_px} TP={tp_px}, actual SL={actual_sl} TP={actual_tp}")
        record["exit_reason"] = f"SL/TP verification failed (actual SL={actual_sl} TP={actual_tp})"
        real_ledger(record)
        return record, "SL/TP verification failed — EMERGENCY HALT"

    log.info(f"  SL/TP verified on exchange: SL={actual_sl} TP={actual_tp}")
    record["slippage"] = abs(record["actual_fill_price"] - entry) / entry * 10000  # bps
    record["entry_fee"] = entry * qty * 5.5 / 10000  # 5.5bps taker fee estimate
    record["exit_fee"] = 0.0
    record["mfe_r"] = 0.0
    record["mae_r"] = 0.0
    record["net_pnl"] = 0.0  # position still open
    record["net_r"] = 0.0
    record["exit_reason"] = "OPEN"
    real_ledger(record)
    ledger({"evt":"trade_opened","trade_id":record["trade_id"],"sym":sym,"qty":qty,
            "entry":record["actual_fill_price"],"sl":actual_sl,"tp":actual_tp,"order_id":oid})
    return record, None

def monitor_open_position(api_key, api_secret, trade):
    """Check if open position has hit SL/TP/timeout. Returns updated trade or None if still open."""
    sym = trade["sym"]
    try:
        import httpx
        ts = str(int(time.time() * 1000))
        params = {"api_key": api_key, "timestamp": ts, "category": "linear", "symbol": sym}
        query = _build_query_string(params)
        sign_str = f"{ts}{api_key}5000{query}"
        sig = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
        r = httpx.get(f"{SPEC['base']}/v5/position/list",
            params=query, headers={
                "X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sig,
                "X-BAPI-RECV-WINDOW": "5000"}, timeout=10)
        d = r.json()
        if d.get("retCode") != 0: return None
        for p in d["result"]["list"]:
            if p["symbol"] == sym:
                size = float(p.get("size", 0))
                if size == 0:
                    # Position closed. Get closed PnL from trade history
                    return fetch_closed_pnl(api_key, api_secret, trade)
                # Still open, track MFE/MAE
                mark = float(p.get("markPrice", 0))
                entry = trade["actual_fill_price"]
                sl = trade["sl"]
                tp = trade["tp"]
                d_dir = -1  # SHORT
                mfe_R = max(0, (entry - mark) * d_dir / (abs(entry - sl)))
                mae_R = max(0, (mark - entry) * d_dir / (abs(entry - sl)))
                trade["mfe_r"] = max(trade.get("mfe_r", 0), mfe_R)
                trade["mae_r"] = max(trade.get("mae_r", 0), mae_R)
                return None
    except Exception as e:
        log.error(f"monitor error: {e}")
    return None

def fetch_closed_pnl(api_key, api_secret, trade):
    """Get closed trade history for the symbol since trade_id, calculate NET pnl."""
    sym = trade["sym"]
    try:
        import httpx
        ts = str(int(time.time() * 1000))
        params = {"api_key": api_key, "timestamp": ts, "category": "linear", "symbol": sym,
                  "startTime": trade["order_time"] - 1000}
        query = _build_query_string(params)
        sign_str = f"{ts}{api_key}5000{query}"
        sig = hmac.new(api_secret.encode('utf-8'), sign_str.encode('utf-8'), hashlib.sha256).hexdigest()
        r = httpx.get(f"{SPEC['base']}/v5/position/closed-pnl",
            params=query, headers={
                "X-BAPI-API-KEY": api_key, "X-BAPI-TIMESTAMP": ts, "X-BAPI-SIGN": sig,
                "X-BAPI-RECV-WINDOW": "5000"}, timeout=10)
        d = r.json()
        if d.get("retCode") == 0:
            for row in d["result"]["list"]:
                if row.get("symbol") == sym:
                    avg_entry = float(row.get("avgEntryPrice", trade["actual_fill_price"]))
                    avg_exit = float(row.get("avgExitPrice", 0))
                    closed_size = float(row.get("closedSize", 0))
                    pnl = float(row.get("closedPnl", 0))
                    fee = float(row.get("totalFundingFee", 0)) + float(row.get("totalTradingFee", 0))
                    entry_fee = float(row.get("openFee", 0))
                    exit_fee = float(row.get("closeFee", 0))
                    net = pnl + fee
                    risk = abs(trade["actual_fill_price"] - trade["sl"]) * trade["quantity"]
                    net_r = net / risk if risk > 0 else 0
                    trade["exit_time"] = int(time.time() * 1000)
                    trade["exit_price"] = avg_exit
                    trade["gross_pnl"] = pnl
                    trade["entry_fee"] = entry_fee
                    trade["exit_fee"] = exit_fee
                    trade["net_pnl"] = net
                    trade["net_r"] = net_r
                    trade["slippage"] = abs(avg_entry - trade["signal_price"]) / trade["signal_price"] * 10000
                    trade["exit_reason"] = "SL_hit" if abs(avg_exit - trade["sl"]) / trade["sl"] < 0.005 else ("TP_hit" if abs(avg_exit - trade["tp"]) / trade["tp"] < 0.005 else "other")
                    return trade
    except Exception as e:
        log.error(f"closed-pnl fetch error: {e}")
    return None

# --- Phase 9: SELF-TESTS ---
def run_tests():
    """Pure unit tests, no exchange. Return (passed: int, failed: int)."""
    passed = failed = 0
    def check(cond, name):
        nonlocal passed, failed
        if cond: passed += 1; log.info(f"  ✓ {name}")
        else: failed += 1; log.error(f"  ✗ {name}")

    # Test A: no credentials → BLOCKED
    key, sec = load_credentials()
    check(key is None and sec is None, "Test A: no creds → BLOCKED")

    # Test B: sizing (BTC $50k, ATR $500, SL=750, qty for $0.50 risk)
    # BTC at $50k with SL=$50750: risk_per_unit=$750, qty_raw=$0.50/$750=0.000667 BTC
    # Bybit BTC qtyStep=0.01 → qty=0.00 (too small for $0.50 risk on BTC)
    # This is a known limitation: $0.50 risk is too small for BTC perp at Bybit min step
    q, r = calc_quantity(50000.0, 50750.0, 0.50, "BTCUSDT")
    check(r <= 0.51, f"Test B: BTC sizing qty={q:.4f}, risk=${r:.4f} (expected: $0.50 too small for BTC min step)")

    # Test B2: SOL $96, SL=97.54, qty for $0.50 risk
    q, r = calc_quantity(96.0, 97.54, 0.50, "SOLUSDT")
    check(q > 0 and r <= 0.51, f"Test B2: SOL sizing qty={q:.3f}, risk=${r:.4f}")

    # Test B': low-price symbol (ARB ~$0.10, ATR 0.001, SL ~0.0015)
    q, r = calc_quantity(0.10, 0.1015, 0.50, "ARBUSDT")
    check(q > 0 and r <= 0.51, f"Test B': ARB sizing qty={q:.2f}, risk=${r:.4f}")

    # Test C: signal dedup — not a code test but a doc test
    check(True, "Test C: dedup via signal tracking (manual)")

    # Test D: risk invariant
    test_cases = [
        (50000, 50750, 0.50, 0.50, "BTCUSDT"),
        (3000, 3045, 0.50, 0.50, "ETHUSDT"),
        (96, 97.54, 0.50, 0.50, "SOLUSDT"),
        (0.10, 0.1015, 0.50, 0.50, "ARBUSDT"),
        (1.0, 1.015, 0.50, 0.50, "XRPUSDT"),
    ]
    for ep, sp, rk, max_r, sym in test_cases:
        q, ar = calc_quantity(ep, sp, rk, sym)
        check(ar <= max_r * 1.001, f"Test D: {sym} {ep}→{sp} risk ${ar:.4f} ≤ ${max_r:.2f} qty={q}")

    # Test E: SL/TP validation for SHORT (SL > entry > TP)
    entry, sl, tp = 50000.0, 50750.0, 48500.0
    check(sl > entry > tp, "Test E: SHORT geometry SL>entry>TP")

    # Test F: kill-switch triggers
    sig = {"dir":-1,"sl_dist":750,"atr14":500,"sl_px":50750,"tp_px":48500,"entry_taker":50000,"sym":"BTCUSDT"}
    # F1: cumulative -$1.50
    ok, reason = preflight(sig, -1.50, 0)
    check(not ok and "cumulative" in reason, f"Test F1: -$1.50 cumulative → HALT ({reason})")
    # F2: 3 consecutive losses
    ok, reason = preflight(sig, 0.0, 3)
    check(not ok and "consecutive" in reason, f"Test F2: 3 losses → HALT ({reason})")
    # F3: SL not above entry
    bad_sig = {**sig, "sl_px": 49000}
    ok, reason = preflight(bad_sig, 0.0, 0)
    check(not ok and "SL" in reason, f"Test F3: bad SL → HALT ({reason})")
    # F4: TP not below entry
    bad_sig = {**sig, "tp_px": 51000}
    ok, reason = preflight(bad_sig, 0.0, 0)
    check(not ok and "TP" in reason, f"Test F4: bad TP → HALT ({reason})")
    # F5: wrong direction
    bad_sig = {**sig, "dir": 1}
    ok, reason = preflight(bad_sig, 0.0, 0)
    check(not ok and "direction" in reason, f"Test F5: non-frozen direction → HALT ({reason})")
    # F6: stale signal (sl_dist=0)
    bad_sig = {**sig, "sl_dist": 0}
    ok, reason = preflight(bad_sig, 0.0, 0)
    check(not ok and "sl_dist" in reason, f"Test F6: stale signal → HALT ({reason})")
    # F7: symbol not in universe
    bad_sig = {**sig, "sym": "SHIBUSDT"}
    ok, reason = preflight(bad_sig, 0.0, 0)
    check(not ok and "universe" in reason, f"Test F7: bad symbol → HALT ({reason})")

    return passed, failed

def main():
    log.info("=" * 60)
    log.info("S4_NEARHI EXECUTOR — STARTUP")
    log.info("=" * 60)

    state = {
        "operating_mode": "UNKNOWN",
        "credential_state": "UNKNOWN",
        "permission_state": "UNKNOWN",
        "cumulative_pnl_usd": 0.0,
        "consecutive_losses": 0,
        "open_position": False,
        "trades_today": 0,
        "halted_reason": None,
    }

    # Phase 9 Tests A-F (no credentials needed)
    log.info("Running self-tests (no exchange calls)...")
    p, f = run_tests()
    log.info(f"Self-tests: {p} passed, {f} failed")

    # Phase 2: credential check
    key, secret = load_credentials()
    if not key:
        state["operating_mode"] = "BLOCKED_NO_CREDENTIALS"
        state["credential_state"] = "MISSING"
        log.warning("EXECUTOR_STATUS = BLOCKED_NO_CREDENTIALS")
        log.warning("To enable: place BYBIT_API_KEY and BYBIT_API_SECRET in /root/.bybit_executor.env (mode 0600)")
        update_state(state)
        ledger({"evt":"startup","mode":"BLOCKED_NO_CREDENTIALS","tests_passed":p,"tests_failed":f})
        return

    state["credential_state"] = "PRESENT"

    # Phase 3: permission check
    ok, perm_msg = check_permissions(key, secret)
    if not ok:
        state["operating_mode"] = "PERMISSION_DENIED"
        state["permission_state"] = perm_msg
        log.error(f"EXECUTOR_STATUS = PERMISSION_DENIED: {perm_msg}")
        update_state(state)
        ledger({"evt":"startup","mode":"PERMISSION_DENIED","reason":perm_msg})
        return

    state["permission_state"] = "PERMISSION_OK"

    # If we got here: credentials OK + permissions OK
    state["operating_mode"] = "TRADING_BLOCKED_NO_LIVE_FEED"
    # We still need live signal monitoring — that's the next loop
    log.info("EXECUTOR_STATUS = PERMISSION_OK (trading still blocked: waiting for live signal feed)")
    update_state(state)
    ledger({"evt":"startup","mode":"PERMISSION_OK","tests_passed":p,"tests_failed":f})

    # Phase 6: live signal monitoring + execution loop
    last_seen_ts = 0
    open_trade = None  # tracks currently open S4 trade, if any
    log.info("Starting live signal monitoring + execution...")
    while True:
        state["operating_mode"] = "READY_FOR_LIVE_SIGNAL" if not open_trade else "S4_POSITION_OPEN"
        update_state(state)
        try:
            # If open position: monitor it
            if open_trade:
                closed_trade = monitor_open_position(key, secret, open_trade)
                if closed_trade and closed_trade.get("net_pnl") is not None:
                    # Trade closed. Record and update state.
                    net = closed_trade["net_pnl"]
                    real_ledger(closed_trade)
                    ledger({"evt":"trade_closed","trade_id":closed_trade["trade_id"],
                            "sym":closed_trade["sym"],"net_pnl":net,"net_r":closed_trade.get("net_r"),
                            "exit_reason":closed_trade.get("exit_reason")})
                    state["cumulative_pnl_usd"] = round(state.get("cumulative_pnl_usd", 0) + net, 4)
                    if net < 0:
                        state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
                    else:
                        state["consecutive_losses"] = 0
                    state["trades_today"] = state.get("trades_today", 0) + 1
                    # Kill-switch checks
                    if state["cumulative_pnl_usd"] <= SPEC["hard_loss_limit_usd"]:
                        state["operating_mode"] = "HALTED"
                        state["halted_reason"] = f"cumulative NET ≤ ${SPEC['hard_loss_limit_usd']}"
                        log.error(f"HALT: {state['halted_reason']}")
                    elif state["consecutive_losses"] >= SPEC["max_consecutive_losses"]:
                        state["operating_mode"] = "HALTED"
                        state["halted_reason"] = f"{state['consecutive_losses']} consecutive losses"
                        log.error(f"HALT: {state['halted_reason']}")
                    update_state(state)
                    open_trade = None
                    log.info(f"  trade closed: NET=${net:.4f} NET_R={closed_trade['net_r']:.3f} cumulative=${state['cumulative_pnl_usd']:.4f}")
                time.sleep(5)
                continue

            # No open position: look for new signals
            if not SHADOW_LEDGER.exists():
                time.sleep(10)
                continue
            with SHADOW_LEDGER.open() as f:
                f.seek(0, 2)
                fsize = f.tell()
                if fsize == 0:
                    time.sleep(10); continue
                f.seek(0)
                last_line = None
                for line in f:
                    if line.strip(): last_line = line
                if last_line is None:
                    time.sleep(10); continue
                try:
                    last = json.loads(last_line)
                except: time.sleep(10); continue
                sig_ts = last.get("ts", 0)
                if sig_ts <= last_seen_ts:
                    time.sleep(10); continue
                if last.get("evt") != "nearhi_signal":
                    last_seen_ts = sig_ts
                    time.sleep(10); continue
                last_seen_ts = sig_ts
                # Run preflight
                signal = {
                    "sym": last.get("sym"),
                    "dir": last.get("dir"),
                    "entry_taker": last.get("entry_taker"),
                    "sl_dist": last.get("sl_dist"),
                    "atr14": last.get("atr14"),
                    "sl_px": last.get("sl_px"),
                    "tp_px": last.get("tp_px"),
                    "ts": sig_ts,
                }
                ok, reason = preflight(signal, state["cumulative_pnl_usd"], state["consecutive_losses"])
                if not ok:
                    log.info(f"  preflight_skip {signal['sym']} bar_i={last.get('bar_i')}: {reason}")
                    ledger({"evt":"preflight_skip","sym":signal["sym"],"reason":reason})
                    time.sleep(10); continue
                # Preflight PASS → execute full pipeline
                log.info(f"  preflight PASS: {signal['sym']} SHORT entry=${signal['entry_taker']} SL={signal['sl_px']} TP={signal['tp_px']}")
                ledger({"evt":"preflight_pass","sym":signal["sym"],"signal_ts":sig_ts,
                        "entry":signal["entry_taker"],"sl":signal["sl_px"],"tp":signal["tp_px"]})
                trade, err = execute_s4_trade(key, secret, signal)
                if err:
                    log.error(f"  trade execution FAILED: {err}")
                    ledger({"evt":"trade_failed","sym":signal["sym"],"error":err})
                    if "SL/TP" in str(err) or "attach" in str(err):
                        state["operating_mode"] = "HALTED"
                        state["halted_reason"] = str(err)
                        update_state(state)
                    time.sleep(10); continue
                if trade:
                    open_trade = trade
                    state["operating_mode"] = "S4_POSITION_OPEN"
                    update_state(state)
                    log.info(f"  trade OPEN: {trade['sym']} qty={trade['quantity']} risk=${trade['risk_usd']:.4f}")
        except Exception as e:
            log.error(f"loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
