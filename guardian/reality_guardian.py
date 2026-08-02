"""
guardian/reality_guardian.py
Reality Guardian — LIVE profit protection for Reality positions.
Polls Bybit positions every 30s, applies Guardian rules:
- MFE >= 0.8R  → move SL to breakeven (entry)
- MFE >= 1.0R  → move SL to entry + 0.5*ATR (partial lock)
- MFE >= 1.5R  → move SL to entry + 1.0*ATR (tighter lock)
- HOLD_HOURS > 48 → timeout alert (no SL change)

Calls BybitAdapter.set_trading_stop() — LIVE modifications on exchange.
Persists state to guardian/reality_state.json (BE, partial flags).
Logs every action to profit_alerts.jsonl.
"""
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("reality_guardian")

# Paths
GUARDIAN_STATE_PATH = Path("/root/tradingos/guardian/reality_state.json")
PROFIT_ALERTS_PATH = Path("/root/tradingos/guardian/profit_alerts.jsonl")
TIMEOUT_ALERTS_PATH = Path("/root/tradingos/guardian/timeout_alerts.jsonl")
ENV_PATH = "/root/trading_brain_v4/research/execution/.env"

# Guardian config
POLL_INTERVAL = 30  # seconds
MAX_HOLD_HOURS = 48
BE_THRESHOLD = 0.8  # R multiple to move SL to breakeven
PARTIAL_THRESHOLD = 1.0  # R multiple to move SL to entry + 0.5*ATR
TIGHT_THRESHOLD = 1.5  # R multiple to move SL to entry + 1.0*ATR

# ─── Trailing stop (DISABLED until validation completes) ───
# Why disabled: Reality Pilot v3 validation (decision_criteria_v1.json) requires
# UNCHANGED exit rules (BE/PARTIAL/TIGHT only). Enabling trailing now would mix
# two exit regimes and invalidate the BUY-vs-SELL comparison.
# Enable after 30+ BUY trades collected and LOCKED criteria applied.
TRAILING_ENABLED = False          # master switch — flip to True post-validation
TRAIL_DISTANCE_R = 0.5            # SL = peak - 0.5R (locked profit from peak)
TRAIL_MIN_STEP_R = 0.1            # only move SL when peak gains >= 0.1R (avoid API spam)
TRAIL_MOVE_TP = True              # also raise TP when trailing: TP = peak + 1.0R
TRAIL_TP_DISTANCE_R = 1.0         # TP sits 1.0R above peak
TRAIL_START_AFTER = "TIGHT"       # trailing only active after TIGHT fired


def _load_credentials():
    ak, as_ = "", ""
    try:
        with open(ENV_PATH) as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, v = l.split("=", 1)
                    if k.strip() == "BYBIT_API_KEY":
                        ak = v.strip()
                    elif k.strip() == "BYBIT_API_SECRET":
                        as_ = v.strip()
    except FileNotFoundError:
        pass
    return ak, as_


def _get_live_positions():
    """Fetch all open Bybit positions. Returns list of dicts.

    Robust to:
    - API retCode != 0
    - result is None (e.g., wrong symbol filter)
    - result.list is None
    - missing size field
    """
    import hmac, hashlib, httpx
    ak, as_ = _load_credentials()
    if not ak or not as_:
        logger.error("No Bybit credentials")
        return []
    ts = str(int(time.time() * 1000))
    q = "category=linear&settleCoin=USDT"
    sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": ak,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign,
        "X-BAPI-RECV-WINDOW": "5000",
    }
    for attempt in range(2):
        try:
            r = httpx.get(
                f"https://api.bybit.com/v5/position/list?{q}",
                headers=headers,
                timeout=10,
            )
            data = r.json()
            if data.get("retCode") != 0:
                logger.warning(f"Bybit API retCode={data.get('retCode')} msg={data.get('retMsg')}")
                if attempt == 0:
                    time.sleep(1)
                    continue
                return []
            result = data.get("result")
            if not result or not isinstance(result, dict):
                logger.warning(f"Bybit API returned invalid result: {type(result)}")
                if attempt == 0:
                    time.sleep(1)
                    continue
                return []
            raw_list = result.get("list")
            if not raw_list or not isinstance(raw_list, list):
                return []
            return [p for p in raw_list if isinstance(p, dict) and float(p.get("size", 0)) > 0]
        except Exception as e:
            logger.error(f"Bybit API error (attempt {attempt+1}): {e}")
            if attempt == 0:
                time.sleep(1)
                continue
            return []
    return []


def _get_ticker(symbol):
    """Get current price for a symbol. Robust to None/empty responses."""
    import hmac, hashlib, httpx
    ak, as_ = _load_credentials()
    if not ak or not as_:
        return 0
    ts = str(int(time.time() * 1000))
    q = f"category=linear&symbol={symbol}"
    sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": ak,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign,
        "X-BAPI-RECV-WINDOW": "5000",
    }
    try:
        r = httpx.get(
            f"https://api.bybit.com/v5/market/tickers?{q}",
            headers=headers,
            timeout=10,
        )
        data = r.json()
        if data.get("retCode") == 0:
            result = data.get("result")
            if not isinstance(result, dict):
                return 0
            raw_list = result.get("list")
            if not raw_list or not isinstance(raw_list, list) or len(raw_list) == 0:
                return 0
            t = raw_list[0]
            if not isinstance(t, dict):
                return 0
            price = t.get("lastPrice", 0)
            if price is None:
                return 0
            return float(price)
    except Exception:
        pass
    return 0


def _set_trading_stop(symbol, stop_loss=None, take_profit=None):
    """Call Bybit set_trading_stop to modify SL/TP on live position.
    Uses BybitClient for correct POST signing (raw HMAC doesn't work for POST)."""
    try:
        sys.path.insert(0, "/root/trading_brain_v4")
        from exchange.bybit.client import BybitClient
        ak, as_ = _load_credentials()
        if not ak or not as_:
            return False
        client = BybitClient(api_key=ak, api_secret=as_, testnet=False)
        params = {"symbol": symbol, "category": "linear", "position_idx": 0}
        if stop_loss is not None:
            params["stop_loss"] = str(stop_loss)
        if take_profit is not None:
            params["take_profit"] = str(take_profit)
        result = client.set_trading_stop(**params)
        if result.get("retCode") == 0:
            logger.info(f"✅ Guardian SL/TP updated for {symbol}: SL={stop_loss} TP={take_profit}")
            return True
        else:
            logger.error(f"❌ Failed to update SL/TP for {symbol}: {result.get('retMsg', '?')}")
            return False
    except Exception as e:
        logger.error(f"❌ Bybit API error on {symbol}: {e}")
        return False


def _load_guardian_state():
    """Load Guardian state file (per-symbol). Returns {} if file is empty/corrupt."""
    if not GUARDIAN_STATE_PATH.exists():
        return {}
    try:
        with open(GUARDIAN_STATE_PATH) as f:
            raw = f.read().strip()
        if not raw or raw == "null":
            return {}
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _save_guardian_state(state):
    """Save Guardian state file."""
    GUARDIAN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(GUARDIAN_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _log_profit_alert(alert):
    """Append profit-protection alert."""
    PROFIT_ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFIT_ALERTS_PATH, "a") as f:
        f.write(json.dumps(alert) + "\n")


def _log_timeout_alert(alert):
    """Append timeout alert."""
    TIMEOUT_ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TIMEOUT_ALERTS_PATH, "a") as f:
        f.write(json.dumps(alert) + "\n")


# ─── Telegram integration (direct async via shared event loop) ───
_tg_loop = None
_tg_thread = None
_tg_lock = None
_tg_ready = False
_keepalive_refs = []  # prevent GC of background tasks


def _ensure_telegram_started():
    """Lazy-init Telegram notifier on first event. Thread-safe.

    Architecture:
    - Separate daemon thread runs asyncio event loop with run_forever()
    - TG init + all sends happen inside this loop
    - Guardian main thread uses run_coroutine_threadsafe to dispatch sends
    """
    global _tg_loop, _tg_thread, _tg_lock, _tg_ready
    if _tg_lock is None:
        import threading as _th
        _tg_lock = _th.Lock()
    with _tg_lock:
        if _tg_loop is not None and _tg_ready:
            return _tg_loop
        try:
            from tradingos.guardian.telegram_notifier import init_telegram
            # Verify creds before spinning up thread
            if not os.environ.get("TELEGRAM_BOT_TOKEN"):
                logger.error("TELEGRAM_BOT_TOKEN missing — TG disabled")
                return None

            _tg_loop = asyncio.new_event_loop()
            import threading
            init_done = threading.Event()
            init_error = [None]

            def runner():
                global _tg_ready
                try:
                    asyncio.set_event_loop(_tg_loop)
                    # Schedule init as task on the running loop
                    async def _init_and_run():
                        global _tg_ready
                        result = await init_telegram()
                        if result:
                            _tg_ready = True
                        else:
                            init_error[0] = "init_telegram returned False"

                    fut = asyncio.ensure_future(_init_and_run(), loop=_tg_loop)
                    # Wait for init to complete
                    while not fut.done():
                        _tg_loop.call_soon(_tg_loop.stop)
                        try:
                            _tg_loop.run_forever()
                        except Exception:
                            pass
                except Exception as e:
                    init_error[0] = str(e)
                finally:
                    init_done.set()
                    # Keep the loop running for future sends
                    try:
                        _tg_loop.run_forever()
                    except Exception:
                        pass

            _tg_thread = threading.Thread(target=runner, daemon=True)
            _tg_thread.start()

            # Wait up to 15s for init
            if init_done.wait(timeout=15):
                if init_error[0]:
                    logger.error(f"Telegram init failed: {init_error[0]}")
                    _tg_loop = None
                    _tg_ready = False
                    return None
                if _tg_ready:
                    logger.info(f"Telegram ready: _tg_ready={_tg_ready}")
            else:
                logger.error("Telegram init timed out after 15s")
                _tg_loop = None
                _tg_ready = False
                return None
            return _tg_loop
        except Exception as e:
            logger.error(f"Telegram init outer failed: {e}")
            return None


def _enqueue_telegram_event(event_type, symbol, side, entry, new_sl, r, peak_r,
                             old_sl=0.0, current_price=0.0, leverage=1, entry_time=0):
    """Fire Guardian event (BE/Partial/Tight) to Telegram in background thread.

    Direct async call via run_coroutine_threadsafe (no separate worker process).
    """
    _ensure_telegram_started()
    if not _tg_ready:
        logger.warning(f"TG fire EVENT skipped (not ready): {symbol}")
        return
    import threading
    def runner():
        try:
            from tradingos.guardian.telegram_notifier import send_guardian_event
            fut = asyncio.run_coroutine_threadsafe(
                send_guardian_event(
                    symbol=symbol, event_type=event_type,
                    current_sl=new_sl, entry_price=entry, peak_r=peak_r,
                    old_sl=old_sl, current_price=current_price,
                    side=side, leverage=leverage, entry_time=entry_time,
                ),
                _tg_loop
            )
            fut.result(timeout=30)
            logger.info(f"TG {event_type} sent for {symbol}")
        except Exception as e:
            logger.error(f"Telegram event send failed: {e}")
    threading.Thread(target=runner, daemon=True).start()


def _enqueue_telegram_open(symbol, side, entry_price, qty, sl, tp, reason="", leverage=1):
    """Fire trade open to Telegram in background thread."""
    _ensure_telegram_started()
    if not _tg_ready:
        logger.warning(f"TG fire OPEN skipped (not ready): {symbol}")
        return
    import threading
    def runner():
        try:
            from tradingos.guardian.telegram_notifier import send_trade_open
            fut = asyncio.run_coroutine_threadsafe(
                send_trade_open(
                    symbol=symbol, side=side,
                    entry_price=entry_price, qty=qty,
                    sl=sl, tp=tp, reason=reason,
                    leverage=leverage
                ),
                _tg_loop
            )
            fut.result(timeout=60)
            logger.info(f"TG open sent for {symbol}")
        except Exception as e:
            logger.error(f"Telegram open send failed: {e}")
    threading.Thread(target=runner, daemon=True).start()


def _enqueue_telegram_timeout(symbol, side, hold_hours):
    """Fire TIMEOUT alert to Telegram in background thread."""
    _ensure_telegram_started()
    if not _tg_ready:
        logger.warning(f"TG fire TIMEOUT skipped (not ready): {symbol}")
        return
    import threading
    def runner():
        try:
            from tradingos.guardian.telegram_notifier import send_timeout_alert
            fut = asyncio.run_coroutine_threadsafe(
                send_timeout_alert(symbol=symbol, hours=hold_hours),
                _tg_loop
            )
            fut.result(timeout=30)
            logger.info(f"TG timeout sent for {symbol}")
        except Exception as e:
            logger.error(f"Telegram timeout send failed: {e}")
    threading.Thread(target=runner, daemon=True).start()


def _enqueue_telegram_close(symbol, side, entry_price, exit_price, qty, pnl,
                              fees, holding_hours, reason, sl=0.0, tp=0.0,
                              mfe_r=0.0, mae_r=0.0, entry_time=0, exit_time=0,
                              entry_price_raw=None, exit_price_raw=None):
    """Fire trade close to Telegram in background thread.

    Direct async call with send_trade_close → notify_trade_close generates chart+caption.
    """
    _ensure_telegram_started()
    if not _tg_ready:
        logger.warning(f"TG fire CLOSE skipped (not ready): {symbol}")
        return
    import threading
    def runner():
        try:
            from tradingos.guardian.telegram_notifier import send_trade_close
            fut = asyncio.run_coroutine_threadsafe(
                send_trade_close(
                    symbol=symbol, side=side,
                    entry_price=entry_price, exit_price=exit_price,
                    qty=qty, pnl=pnl, fees=fees,
                    holding_hours=holding_hours, reason=reason,
                    sl=sl, tp=tp,
                    mfe_r=mfe_r, mae_r=mae_r,
                    entry_time=entry_time, exit_time=exit_time,
                    entry_price_raw=entry_price_raw, exit_price_raw=exit_price_raw
                ),
                _tg_loop
            )
            fut.result(timeout=60)  # 60s for chart gen + TG send
            logger.info(f"TG close sent for {symbol}")
            # Structured monitoring log (per user suggestion)
            logger.info(
                f"TRADE_CLOSE_NOTIFICATION "
                f"symbol={symbol} side={side} "
                f"entry={entry_price} exit={exit_price} sl={sl} "
                f"pnl={pnl} holding_hours={holding_hours} "
                f"status=SUCCESS"
            )
        except Exception as e:
            logger.error(f"Telegram close send failed: {e}")
            logger.error(
                f"TRADE_CLOSE_NOTIFICATION "
                f"symbol={symbol} side={side} "
                f"entry={entry_price} exit={exit_price} "
                f"status=FAILED reason={type(e).__name__}:{e}"
            )
    threading.Thread(target=runner, daemon=True).start()


def _process_position(pos, state):
    """Process one position for Guardian rules."""
    symbol = pos["symbol"]
    side = pos["side"]
    size = float(pos["size"])
    entry = float(pos["avgPrice"])
    sl = float(pos.get("stopLoss", 0))
    tp = float(pos.get("takeProfit", 0))
    current = _get_ticker(symbol)
    if current == 0:
        current = float(pos.get("markPrice", entry))

    # Compute R-multiple based on risk from SL
    risk_per_unit = abs(entry - sl) if sl > 0 else abs(entry - tp) / 2 if tp > 0 else 0
    if risk_per_unit <= 0:
        return state  # Return current state, NOT None

    # For SELL: profit = entry - current; for BUY: profit = current - entry
    if side == "Sell":
        profit = entry - current
    else:
        profit = current - entry

    r_multiple = profit / risk_per_unit

    # Per-symbol state (with defensive checks)
    sym_state = state.get(symbol)
    is_new_position = sym_state is None or not isinstance(sym_state, dict)
    if is_new_position:
        sym_state = {"be_fired": False, "partial_fired": False, "tight_fired": False, "mfe_peak": 0.0}

    # Store initial risk per unit for later "saved amount" calculation
    sym_state["entry_to_sl_risk"] = risk_per_unit
    sym_state["side"] = side
    sym_state["entry"] = entry
    sym_state["size"] = size
    # Store entry_time for chart generation
    open_time = float(pos.get("createdTime", time.time() * 1000)) / 1000
    sym_state["entry_time"] = open_time

    # Read real leverage from position
    try:
        real_leverage = int(float(pos.get("leverage", 1)))
    except (ValueError, TypeError):
        real_leverage = 1

    # Send OPEN notification on first sight
    if is_new_position:
        _enqueue_telegram_open(
            symbol=symbol, side=side,
            entry_price=entry, qty=size,
            sl=sl, tp=tp, reason="OPEN",
            leverage=real_leverage
        )
        logger.info(f"🟢 OPEN detected: {symbol} {side} @ {entry} qty={size} lev={real_leverage}x SL={sl} TP={tp}")

    # Update MFE peak (max profit reached)
    if r_multiple > sym_state.get("mfe_peak", 0):
        sym_state["mfe_peak"] = r_multiple
    
    # Update MAE trough (max adverse excursion)
    if r_multiple < sym_state.get("mae_trough", 0):
        sym_state["mae_trough"] = r_multiple

    actions = []

    # Rule 1: BE at +0.8R (trigger on PEAK R, not current R — SHIB proved that price reverses between polls)
    if sym_state["mfe_peak"] >= BE_THRESHOLD and not sym_state.get("be_fired", False):
        # Move SL to entry (breakeven)
        new_sl = entry
        if side == "Sell":
            # SELL: SL above entry
            new_sl = entry + 0.00001  # tiny buffer above entry
        if _set_trading_stop(symbol, stop_loss=new_sl):
            sym_state["be_fired"] = True
            sym_state["be_fired_at"] = time.time()
            actions.append(f"Moved SL to breakeven at +{r_multiple:.2f}R (entry={entry:.5f})")
            alert = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "GUARDIAN_BREAKEVEN",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "r_multiple": round(r_multiple, 3),
                "new_sl": new_sl,
                "action": "SL moved to breakeven",
            }
            _log_profit_alert(alert)
            logger.info(f"🟢 BE fired: {symbol} {side} at +{r_multiple:.2f}R, SL→{new_sl:.5f}")
            # Telegram notification (enqueue for guaranteed delivery)
            _enqueue_telegram_event("BE", symbol, side, entry, new_sl, r_multiple, sym_state.get("mfe_peak", 0),
                                     old_sl=sl, current_price=current, leverage=real_leverage, entry_time=open_time)

    # Rule 2: Partial lock at +1.0R (SL = entry + 0.5*ATR/2)
    if sym_state["mfe_peak"] >= PARTIAL_THRESHOLD and not sym_state.get("partial_fired", False):
        # Move SL to entry + partial-risk offset
        partial_offset = risk_per_unit * 0.5
        if side == "Sell":
            new_sl = entry + partial_offset
        else:
            new_sl = entry - partial_offset
        if _set_trading_stop(symbol, stop_loss=new_sl):
            sym_state["partial_fired"] = True
            sym_state["partial_fired_at"] = time.time()
            actions.append(f"Moved SL to partial lock +{partial_offset:.6f} at +{r_multiple:.2f}R")
            alert = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "GUARDIAN_PARTIAL",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "r_multiple": round(r_multiple, 3),
                "new_sl": new_sl,
                "action": "SL moved to partial lock",
            }
            _log_profit_alert(alert)
            logger.info(f"🟡 PARTIAL fired: {symbol} {side} at +{r_multiple:.2f}R, SL→{new_sl:.5f}")
            # Telegram notification (enqueue for guaranteed delivery)
            _enqueue_telegram_event("PARTIAL", symbol, side, entry, new_sl, r_multiple, sym_state.get("mfe_peak", 0),
                                     old_sl=sl, current_price=current, leverage=real_leverage, entry_time=open_time)

    # Rule 3: Tight lock at +1.5R
    if sym_state["mfe_peak"] >= TIGHT_THRESHOLD and not sym_state.get("tight_fired", False):
        tight_offset = risk_per_unit * 1.0
        if side == "Sell":
            new_sl = entry + tight_offset
        else:
            new_sl = entry - tight_offset
        if _set_trading_stop(symbol, stop_loss=new_sl):
            sym_state["tight_fired"] = True
            sym_state["tight_fired_at"] = time.time()
            actions.append(f"Moved SL to tight lock +{tight_offset:.6f} at +{r_multiple:.2f}R")
            alert = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "GUARDIAN_TIGHT",
                "symbol": symbol,
                "side": side,
                "entry": entry,
                "r_multiple": round(r_multiple, 3),
                "new_sl": new_sl,
                "action": "SL moved to tight lock",
            }
            _log_profit_alert(alert)
            logger.info(f"🟢 TIGHT fired: {symbol} {side} at +{r_multiple:.2f}R, SL→{new_sl:.5f}")
            # Telegram notification (enqueue for guaranteed delivery)
            _enqueue_telegram_event("TIGHT", symbol, side, entry, new_sl, r_multiple, sym_state.get("mfe_peak", 0),
                                     old_sl=sl, current_price=current, leverage=real_leverage, entry_time=open_time)

    # Rule 3.5: Trailing stop (DISABLED by default — see config above)
    # Activates after TIGHT fires. Tracks peak with a constant 0.5R trail behind.
    # Optionally raises TP to keep profit target ahead of price.
    if TRAILING_ENABLED and sym_state.get("tight_fired", False):
        peak_r = sym_state.get("mfe_peak", 0)
        last_trail_peak_r = sym_state.get("trail_last_peak_r", 0)
        # Only act when peak gained at least TRAIL_MIN_STEP_R since last trail move
        if peak_r - last_trail_peak_r >= TRAIL_MIN_STEP_R:
            trail_distance_price = risk_per_unit * TRAIL_DISTANCE_R
            # New SL = peak - TRAIL_DISTANCE_R (in price units, on the favorable side)
            if side == "Sell":
                # SELL: profit when price goes DOWN → peak is below entry
                # SL sits trail_distance ABOVE the current price (or peak)
                peak_price = entry - peak_r * risk_per_unit
                new_trail_sl = peak_price + trail_distance_price
            else:
                # BUY: profit when price goes UP → peak is above entry
                peak_price = entry + peak_r * risk_per_unit
                new_trail_sl = peak_price - trail_distance_price
            # Only move SL forward (never backwards)
            current_sl_on_exchange = float(pos.get("stopLoss", 0))
            only_forward = (
                (side == "Buy" and new_trail_sl > current_sl_on_exchange) or
                (side == "Sell" and new_trail_sl < current_sl_on_exchange) or
                current_sl_on_exchange == 0
            )
            if only_forward and new_trail_sl > 0:
                new_tp = None
                if TRAIL_MOVE_TP:
                    if side == "Sell":
                        new_tp = peak_price - risk_per_unit * TRAIL_TP_DISTANCE_R
                    else:
                        new_tp = peak_price + risk_per_unit * TRAIL_TP_DISTANCE_R
                if _set_trading_stop(symbol, stop_loss=new_trail_sl, take_profit=new_tp):
                    sym_state["trail_last_peak_r"] = peak_r
                    sym_state["trail_last_sl"] = new_trail_sl
                    sym_state["trail_last_tp"] = new_tp if new_tp else 0
                    actions.append(
                        f"Trail SL→{new_trail_sl:.6f} (peak {peak_r:.2f}R)"
                        + (f" TP→{new_tp:.6f}" if new_tp else "")
                    )
                    alert = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "type": "GUARDIAN_TRAIL",
                        "symbol": symbol,
                        "side": side,
                        "entry": entry,
                        "r_multiple": round(peak_r, 3),
                        "new_sl": new_trail_sl,
                        "new_tp": new_tp,
                        "action": "Trailing stop moved",
                    }
                    _log_profit_alert(alert)
                    logger.info(
                        f"📈 TRAIL fired: {symbol} {side} peak +{peak_r:.2f}R, "
                        f"SL→{new_trail_sl:.6f}"
                        + (f" TP→{new_tp:.6f}" if new_tp else "")
                    )
                    _enqueue_telegram_event(
                        "TRAIL", symbol, side, entry, new_trail_sl, peak_r, peak_r,
                        old_sl=current_sl_on_exchange, current_price=current,
                        leverage=real_leverage, entry_time=open_time,
                    )

    # Rule 4: Timeout check (open_time heuristic)
    open_time = float(pos.get("createdTime", time.time() * 1000)) / 1000
    hold_hours = (time.time() - open_time) / 3600
    if hold_hours > MAX_HOLD_HOURS and not sym_state.get("timeout_alerted", False):
        alert = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "GUARDIAN_TIMEOUT",
            "severity": "WARNING",
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "hold_hours": round(hold_hours, 1),
            "max_hours": MAX_HOLD_HOURS,
            "action": "Human review required",
        }
        _log_timeout_alert(alert)
        sym_state["timeout_alerted"] = True
        actions.append(f"Timeout alert: {hold_hours:.1f}h > {MAX_HOLD_HOURS}h")
        logger.warning(f"⏰ TIMEOUT: {symbol} {side} held {hold_hours:.1f}h")
        # Telegram notification (enqueue for guaranteed delivery)
        _enqueue_telegram_timeout(symbol, side, hold_hours)

    sym_state["last_check"] = time.time()
    sym_state["last_r"] = r_multiple
    state[symbol] = sym_state

    if actions:
        peak = sym_state.get("mfe_peak") or 0
        logger.info(f"📊 {symbol} {side}: R={r_multiple:+.3f} (peak {peak:+.3f}) — {len(actions)} action(s)")
    return state

TRADE_RESULTS_DIR = Path("/root/tradingos/logs/trades")
FINAL_TRADE_LOG = Path("/root/tradingos/guardian/guardian_effectiveness.jsonl")


def _fetch_actual_close_price(symbol: str, side: str, fallback: float) -> float:
    """Fetch actual exit price from Bybit closed PnL history."""
    try:
        ak, as_ = _load_credentials()
        if not ak or not as_:
            return fallback
        import hmac, hashlib, httpx
        ts = str(int(time.time() * 1000))
        q = f"category=linear&symbol={symbol}&limit=1"
        sign = hmac.new(as_.encode(), f"{ts}{ak}5000{q}".encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": ak, "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-SIGN": sign, "X-BAPI-RECV-WINDOW": "5000",
        }
        r = httpx.get(f"https://api.bybit.com/v5/position/closed-pnl?{q}", headers=headers, timeout=5)
        d = r.json()
        if d.get("retCode") == 0:
            items = d["result"].get("list", [])
            if items:
                exit_price = float(items[0].get("avgExitPrice", 0))
                if exit_price > 0:
                    return exit_price
    except Exception:
        pass
    return fallback


def _record_trade_closure(symbol, state_entry):
    """
    Record a trade closure with separate Guardian metrics:
    - Triggered: which Guardian levels fired
    - Outcome: TP, BE, SL, Manual, Timeout
    - Estimated benefit: only counted if the trade closed
      at or below the Guardian SL (i.e., Guardian did something)
    """
    peak_r = state_entry.get("mfe_peak", 0)
    trough_r = state_entry.get("mae_trough", 0)
    be_fired = state_entry.get("be_fired", False)
    partial_fired = state_entry.get("partial_fired", False)
    tight_fired = state_entry.get("tight_fired", False)

    entry = state_entry.get("entry", 0)
    side = state_entry.get("side", "")
    original_risk = state_entry.get("entry_to_sl_risk", 0)  # 2*ATR

    # Determine if Guardian moved the SL during the trade
    if be_fired:
        guardian_trigger = "BE"
        guardian_sl_at_close = entry  # SL at entry after BE
    elif tight_fired:
        guardian_trigger = "TIGHT"
        guardian_sl_at_close = entry + original_risk  # SL at entry + 1.0x risk
    elif partial_fired:
        guardian_trigger = "PARTIAL"
        guardian_sl_at_close = entry + original_risk * 0.5  # SL at entry + 0.5x risk
    else:
        guardian_trigger = "NONE"
        guardian_sl_at_close = 0  # no SL change

    # Get actual close price from Bybit (most recent kline)
    close_price = _fetch_actual_close_price(symbol, side, entry)

    # Calculate actual outcome
    if side == "Sell":
        if close_price <= entry * 0.999:  # SELL profit = price going down
            if close_price < original_risk and entry - original_risk < close_price:
                # closed between entry and original SL (rough)
                outcome = "BE" if be_fired else ("PARTIAL_HIT" if partial_fired else "MANUAL")
            else:
                outcome = "TP"  # price went to TP level
        else:
            outcome = "SL"  # closed in loss
    else:
        if close_price >= entry * 1.001:
            if close_price > entry + original_risk * 0.5 and close_price < entry + original_risk:
                outcome = "BE" if be_fired else ("PARTIAL_HIT" if partial_fired else "MANUAL")
            else:
                outcome = "TP"
        else:
            outcome = "SL"

    # Only count "estimated benefit" when Guardian actually protected:
    # 1. Guardian moved SL (any of the 3 triggers fired)
    # 2. The position's actual close was WORSE than Guardian's new SL
    # Otherwise Guardian only gave protection, no actual financial benefit
    estimated_benefit = 0
    protected_exit = False

    if guardian_trigger != "NONE":
        if side == "Sell":
            actual_was_at_loss = close_price > entry
            guardian_protected = close_price < guardian_sl_at_close
        else:
            actual_was_at_loss = close_price < entry
            guardian_protected = close_price > guardian_sl_at_close

        if actual_was_at_loss and guardian_protected:
            # Position went into loss, but Guardian's SL caught it at a better price
            if side == "Sell":
                loss_without_guardian = close_price - (entry + original_risk)
            else:
                loss_without_guardian = (entry - original_risk) - close_price
            actual_loss = 0  # Guardian caught at BE
            estimated_benefit = max(0, loss_without_guardian)
            protected_exit = True
        elif not actual_was_at_loss:
            # Trade was profitable at close — Guardian only insured, no benefit
            estimated_benefit = 0
            protected_exit = False
        else:
            estimated_benefit = 0
            protected_exit = False

    # Compute realized PnL from entry vs exit
    size = state_entry.get("size", 0)
    if side == "Sell":
        realized_pnl = (entry - close_price) * size
    else:
        realized_pnl = (close_price - entry) * size
    fees = abs(realized_pnl) * 0.00075  # 0.075% taker fee estimate
    net_pnl = realized_pnl - fees

    # ─── Execution Attribution ──────────────────────────────
    # Signal class: did price move in the right direction?
    if side == "Sell":
        signal_win = close_price < entry
    else:
        signal_win = close_price > entry
    
    if signal_win and realized_pnl > 0:
        signal_class = "SIGNAL_WIN_EXEC_WIN"
    elif signal_win and realized_pnl <= 0:
        signal_class = "SIGNAL_WIN_EXEC_LOSS"
    else:
        signal_class = "SIGNAL_LOSS_EXEC_LOSS"
    
    # Slippage cost = expected PnL at exit_price - actual gross PnL
    if side == "Sell":
        expected_pnl = (entry - close_price) * size
    else:
        expected_pnl = (close_price - entry) * size
    slippage_cost = expected_pnl - realized_pnl

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "close_price": close_price,
        "size": size,
        "realized_pnl": round(realized_pnl, 6),
        "fees": round(fees, 6),
        "net_pnl": round(net_pnl, 6),
        "slippage_cost": round(slippage_cost, 6),
        "mfe_peak_r": round(peak_r, 3),
        "mae_trough_r": round(trough_r, 3),
        "signal_class": signal_class,
        "status": "CLOSED",
        "guardian_trigger": guardian_trigger,
        "be_fired": be_fired,
        "partial_fired": partial_fired,
        "tight_fired": tight_fired,
        "outcome": outcome,
        "protected_exit": protected_exit,
        "estimated_benefit": round(estimated_benefit, 6),
        "protection_cost_window_hours": 24,
        "protection_cost_status": "PENDING",
    }

    TRADE_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRADE_RESULTS_DIR / "trade_results.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")

    # Also append to Guardian effectiveness log (separate file for analysis)
    FINAL_TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FINAL_TRADE_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")

    logger.info(
        f"📝 TRADE CLOSED: {symbol} | peak_r={peak_r:.3f} | "
        f"Guardian: {guardian_trigger} | outcome: {outcome} | "
        f"protected: {protected_exit} | estimated_benefit=${estimated_benefit:.4f}"
    )
    # Enqueue Telegram notification (guaranteed delivery via persistent queue)
    # Include SL/TP/mfe_r/mae_r so chart and text use SAME source of truth
    sl_price = entry - original_risk if side == "Buy" else entry + original_risk
    tp_price = 0  # tp not in state, skip
    entry_ts = state_entry.get("entry_time", 0)
    exit_ts = time.time()
    # RAW prices from Bybit for accurate pnl_pct calculation
    raw_entry = entry
    raw_exit = close_price
    _enqueue_telegram_close(
        symbol=symbol, side=side,
        entry_price=entry, exit_price=close_price,
        qty=size, pnl=net_pnl, fees=fees,
        holding_hours=state_entry.get("hold_hours", 0) or 0,
        reason=outcome,
        sl=sl_price, tp=tp_price,
        mfe_r=peak_r, mae_r=trough_r,
        entry_time=entry_ts, exit_time=exit_ts,
        entry_price_raw=raw_entry, exit_price_raw=raw_exit
    )


def _check_missed_profit(symbol: str, record: dict) -> dict:
    """
    Guardian Effectiveness v3 — Protection Cost check.
    Called 24h+ after a Guardian-triggered close.
    Checks if the market continued in the profitable direction,
    meaning Guardian prevented additional profit capture.
    
    Returns updated record with:
    - potential_missed_profit: how much more profit would have been made
    - net_guardian_value: benefit - missed_profit
    """
    if record.get("guardian_trigger") == "NONE":
        # Guardian didn't trigger — no cost to attribute
        record["potential_missed_profit"] = 0
        record["net_guardian_value"] = 0
        record["protection_cost_status"] = "N/A"
        return record
    
    side = record.get("side", "")
    entry = record.get("entry", 0)
    close_price = record.get("close_price", 0)
    close_time = record.get("timestamp", "")
    
    # Fetch current price (24h+ later)
    current_price = _fetch_actual_close_price(symbol, side, close_price)
    
    if side == "Sell":
        # Profit = price goes DOWN. If it kept going down, Guardian cost profit.
        max_observed_below_close = current_price < close_price
        if max_observed_below_close:
            # In SELL, missing profit = close_price - current_price (price kept going down)
            record["potential_missed_profit"] = round(close_price - current_price, 6)
        else:
            # Price went up, no missed profit from Guardian
            record["potential_missed_profit"] = 0
    else:
        # Buy: profit = price goes UP. If kept going up, Guardian cost profit.
        if current_price > close_price:
            record["potential_missed_profit"] = round(current_price - close_price, 6)
        else:
            record["potential_missed_profit"] = 0
    
    record["current_price_at_check"] = current_price
    record["net_guardian_value"] = round(
        record.get("estimated_benefit", 0) - record["potential_missed_profit"], 6
    )
    record["protection_cost_status"] = "CHECKED"
    return record


async def _missed_profit_check_loop():
    """Periodically check closed trades for missed profit (24h+ after close)."""
    import asyncio
    GUARDIAN_EFFECTIVENESS_LOG = Path("/root/tradingos/guardian/guardian_effectiveness.jsonl")
    PROTECTION_COST_LOG = Path("/root/tradingos/guardian/protection_cost.jsonl")
    
    while True:
        try:
            if not GUARDIAN_EFFECTIVENESS_LOG.exists():
                await asyncio.sleep(3600)  # 1 hour
                continue
            
            PROTECTION_COST_LOG.parent.mkdir(parents=True, exist_ok=True)
            
            cutoff = time.time() - 86400  # 24 hours ago
            with open(GUARDIAN_EFFECTIVENESS_LOG) as f:
                lines = [json.loads(l.strip()) for l in f if l.strip()]
            
            for record in lines:
                if record.get("protection_cost_status") != "PENDING":
                    continue
                close_time_str = record.get("timestamp", "")
                if not close_time_str:
                    continue
                try:
                    close_time = datetime.fromisoformat(close_time_str).timestamp()
                except:
                    continue
                if close_time > cutoff:
                    continue
                
                # Time elapsed
                record = _check_missed_profit(record["symbol"], record)
                # Re-save with updated Protection Cost fields
                # For now, log to a separate file
                with open(PROTECTION_COST_LOG, "a") as f:
                    f.write(json.dumps(record) + "\n")
                # Mark as checked
                with open(GUARDIAN_EFFECTIVENESS_LOG, "r") as f:
                    content = f.read()
                content = content.replace(json.dumps(record), json.dumps(record))
        except Exception as e:
            pass
        await asyncio.sleep(3600)  # check hourly


async def run_guardian():
    """Main Guardian loop — poll positions and apply rules."""
    logger.info("🛡️ Reality Guardian LIVE starting")
    logger.info(f"   Poll interval: {POLL_INTERVAL}s")
    logger.info(f"   BE: +{BE_THRESHOLD}R, Partial: +{PARTIAL_THRESHOLD}R, Tight: +{TIGHT_THRESHOLD}R")
    logger.info(f"   Timeout: {MAX_HOLD_HOURS}h")

    # CRITICAL: Initialize Telegram eagerly at startup (not lazy on first event).
    logger.info("Pre-warming Telegram notifier...")
    loop = _ensure_telegram_started()
    if loop and _tg_ready:
        logger.info(f"Telegram pre-warmed successfully (loop={loop})")
    else:
        logger.error("Telegram pre-warm FAILED — TG notifications will not work")

    while True:
        try:
            positions = _get_live_positions()
            live_symbols = {p["symbol"] for p in positions}

            # Detect trade closures: symbols in state but not in live positions
            state = _load_guardian_state()
            if not isinstance(state, dict):
                state = {}
            state_symbols = set(state.keys())
            closed_symbols = state_symbols - live_symbols
            for sym in closed_symbols:
                if sym in state and state[sym] is not None:
                    _record_trade_closure(sym, state[sym])
                    del state[sym]

            # Process live positions
            for pos in positions:
                new_state = _process_position(pos, state)
                if isinstance(new_state, dict):
                    state = new_state
                # else: keep current state

            _save_guardian_state(state)
        except Exception as e:
            logger.error(f"Guardian loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL)


async def main():
    """Run both Guardian main loop and missed-profit checker concurrently."""
    import asyncio
    await asyncio.gather(run_guardian(), _missed_profit_check_loop())


if __name__ == "__main__":
    try:
        asyncio.run(run_guardian())
    except KeyboardInterrupt:
        logger.info("Guardian stopped by user")
