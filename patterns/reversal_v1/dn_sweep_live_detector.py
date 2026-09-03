"""
dn_sweep_live_detector.py — Live paper-trade detector for DN-sweep pattern.

CORRECTIONS vs previous version (2026-08-27):
  1. Exit price is fetched from HISTORICAL M15 bars at exit_target_ts, not current price.
     This is the only way to validate a pattern that fired in the past — we must look
     at what the market actually did during the 4h hold window.
  2. Anti-stale filter at startup: signals older than STALE_MS are not processed.
  3. Fill recording is debounced: only log a fill once, at the actual bar where price
     first touched entry_zone.
  4. Service initializes from existing signals.jsonl but skips any signal with
     signal_ts < now - STALE_MS — they are quarantined, not back-filled.
"""
import json
import time
import sys
import os
import logging
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import httpx
import hmac
import hashlib

sys.path.insert(0, "/root/tradingos")

from patterns.reversal_v1.dn_sweep_detector import detect_dn_sweeps
from patterns.reversal_v1.dn_sweep_context import detect_dn_sweeps_context

# Load demo keys from shared env (fixes hardcoded leak + enables key rotation)
import os
from dotenv import load_dotenv
load_dotenv(Path("/root/.bybit_executor.env"))
KEY = os.getenv("BYBIT_API_KEY", "")
SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE = "https://api-demo.bybit.com"

# Whitelist v2 (VALIDATION_v2_SL800.md): per-symbol OOS WR>=55% AND median>0
# under the SL-only exit model. Dropped vs v1: BTWUSDT/TUTUSDT/STRKUSDT/ETHUSDT.
WHITELIST = [
    "ADAUSDT", "DOGEUSDT", "GMTUSDT", "INXUSDT", "ONDOUSDT", "OPUSDT",
    "SOLUSDT", "VETUSDT", "WLDUSDT", "ZKUSDT",
]

HOLD_BARS = 16          # 4h on M15
EXPIRY_BARS = 2         # 30min LIMIT expiry
STALE_MS = 60 * 60 * 1000  # 1h: signals older than this on startup are quarantined

LOG_DIR = Path("/root/tradingos/patterns/reversal_v1/paper")
LOG_DIR.mkdir(parents=True, exist_ok=True)
SIGNALS_LOG = LOG_DIR / "signals.jsonl"
FILLS_LOG = LOG_DIR / "fills.jsonl"
EXITS_LOG = LOG_DIR / "exits.jsonl"

PENDING = {}  # symbol -> order dict

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger("dn_sweep_live")


def _signed_get(path: str, query: str, timeout: int = 15):
    end_ts = int(time.time() * 1000)
    ts = str(end_ts)
    sig = hmac.new(SECRET.encode(), (ts + KEY + "5000" + query).encode(), hashlib.sha256).hexdigest()
    return httpx.get(
        f"{BASE}{path}?{query}",
        headers={"X-BAPI-API-KEY": KEY, "X-BAPI-TIMESTAMP": ts,
                 "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sig},
        timeout=timeout,
    )


def fetch_bars_at_or_after(symbol: str, start_ms: int, count: int = 32) -> pd.DataFrame | None:
    """Fetch M15 bars covering [start_ms, start_ms + count * 15min]."""
    end_ms = start_ms + count * 15 * 60 * 1000
    q = f"category=linear&symbol={symbol}&interval=15&start={start_ms}&end={end_ms}&limit={count + 2}"
    try:
        r = _signed_get("/v5/market/kline", q)
        d = r.json()
        if d.get("retCode") != 0:
            return None
        rows = []
        for item in d.get("result", {}).get("list", []):
            rows.append({"ts": int(item[0]), "o": float(item[1]), "h": float(item[2]),
                         "l": float(item[3]), "c": float(item[4]), "v": float(item[5])})
        if not rows:
            return None
        df = pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
        return df[df["ts"] >= start_ms].reset_index(drop=True)
    except Exception as e:
        log.debug(f"fetch_bars_at_or_after failed for {symbol}: {e}")
        return None


def fetch_recent_klines(symbol: str, limit: int = 300) -> pd.DataFrame | None:
    """Fetch latest M15 klines for a symbol."""
    end_ts = int(time.time() * 1000)
    q = f"category=linear&symbol={symbol}&interval=15&end={end_ts}&limit={limit}"
    try:
        r = _signed_get("/v5/market/kline", q)
        d = r.json()
        if d.get("retCode") != 0:
            return None
        rows = []
        for item in d.get("result", {}).get("list", []):
            rows.append({"ts": int(item[0]), "o": float(item[1]), "h": float(item[2]),
                         "l": float(item[3]), "c": float(item[4]), "v": float(item[5])})
        if not rows:
            return None
        return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)
    except Exception as e:
        log.debug(f"fetch failed for {symbol}: {e}")
        return None


def get_current_price(symbol: str) -> float | None:
    """Get last price for a symbol."""
    q = f"category=linear&symbol={symbol}"
    try:
        r = _signed_get("/v5/market/tickers", q, timeout=10)
        d = r.json()
        items = (d.get("result") or {}).get("list", [])
        if items:
            return float(items[0].get("lastPrice", 0))
    except Exception:
        pass
    return None


def predict_regime(symbol: str, signal_ts: int) -> int:
    """Predict regime from features at signal_ts using GMM model."""
    try:
        features_path = Path("/root/tradingos/regimes_v1/regime_features_labeled.parquet")
        if not features_path.exists():
            return 1
        df = pd.read_parquet(features_path, columns=["ts_s","symbol","log_ret","vol_z","abs_funding","oi_chg","regime"])
        sym_df = df[df["symbol"] == symbol].sort_values("ts_s")
        if len(sym_df) == 0:
            return 1
        sig_s = signal_ts // 1000
        idx = (sym_df["ts_s"] - sig_s).abs().idxmin()
        if abs(sym_df.loc[idx, "ts_s"] - sig_s) > 14400:
            return 1
        return int(sym_df.loc[idx, "regime"])
    except Exception as e:
        log.debug(f"regime predict failed: {e}")
        return 1


def scan_symbol(symbol: str):
    """Scan one symbol for new DN-sweep signals.

    Only signals whose bar timestamp is within FRESH_BARS of the latest bar are
    considered "live". This avoids logging a 75-hour-old sweep as a new entry —
    we want the trigger to fire only on the most recent bar(s), so that the
    30-minute LIMIT expiry window is actually usable.
    """
    FRESH_BARS = 4  # accept signals from the last 4 M15 bars (1h window)
    df = fetch_recent_klines(symbol, limit=300)
    if df is None or len(df) < 50:
        return
    # 2026-08-31 (owner): контекстный DN-sweep. Старый ловил ТОЛЬКО падающие
    # ножи (реплей: 38/38 свепов при медиане роста −2.9% перед пробоем → WR
    # ~45%, live paper 0/4). Теперь сигнал принимается ТОЛЬКО когда пробой
    # происходит ВНУТРИ сильного ап-импульса (HH + рост ≥1.2%/12бар) у 21-MA
    # (зона баланса — видео «Ложный пробой в логике сильного рынка»).
    sigs = detect_dn_sweeps_context(df, symbol=symbol,
                                    pivot_h=16, sweep_pierce_bps=20)
    if not sigs:
        return

    # Only take signals from the freshest bars (not 75h-old history)
    latest_bar_ts = int(df["ts"].iloc[-1])
    fresh_cutoff = latest_bar_ts - FRESH_BARS * 15 * 60 * 1000
    fresh_sigs = [s for s in sigs if s.ts >= fresh_cutoff]
    if not fresh_sigs:
        return
    latest = fresh_sigs[-1]

    # Skip if already logged (avoid duplicates)
    last_log_ts = 0
    if SIGNALS_LOG.exists():
        with SIGNALS_LOG.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec["symbol"] == symbol:
                        last_log_ts = max(last_log_ts, rec["ts"])
                except Exception:
                    pass
    if latest.ts <= last_log_ts:
        return

    # Same signal already tracked in this process (e.g. restored by hydrate)
    pending_key = f"{symbol}:{latest.ts}"

    # Regime-aware Thompson sampling
    # LOOSE_MODE (env DN_SWEEP_PAPER_LOOSE=1) is for paper-trade accumulation:
    # we don't yet have enough fills to trust the bandit, so skip the
    # "regime ≠ best of 4 → $0" gate and accept the trade at base_risk.
    loose_mode = os.environ.get("DN_SWEEP_PAPER_LOOSE", "1") == "1"
    regime = predict_regime(symbol, latest.ts)
    try:
        from regimes_v1.regime_bandit import RegimeBandit
        bandit = RegimeBandit()
        bet_size = bandit.get_bet_size(base_risk=500, regime=regime)
        if loose_mode:
            # In loose mode, the bandit only vetoes trades when its sampled
            # P(win) for this regime is dramatically below prior (sample < 50% of prior).
            prior = bandit.prior_wr.get(regime, 0.5)
            sample_wr = bandit.alpha[regime] / (bandit.alpha[regime] + bandit.beta[regime])
            if sample_wr < 0.5 * prior:
                log.info(f"⏭️ LOOSE-SKIP {symbol}: regime={regime} sample_wr={sample_wr:.2f} prior={prior:.2f}")
                return
            # Always at least base_risk, scaled modestly
            bet_size = max(250.0, min(750.0, bet_size if bet_size > 0 else 500.0))
        else:
            if bet_size < 50:
                log.info(f"⏭️ REGIME-SKIP {symbol}: regime={regime} bandit_bet=${bet_size:.0f}")
                return
    except Exception as e:
        log.debug(f"bandit failed: {e}")
        bet_size = 500
    regime_name = ["VOL_SPIKE","TRENDING_UP","QUIET","BEAR_DROP"][regime]

    rec = {
        "ts": latest.ts,
        "ts_iso": datetime.fromtimestamp(latest.ts/1000, tz=timezone.utc).isoformat(),
        "symbol": symbol,
        "side": latest.side,
        "entry_zone": latest.entry_zone,
        "sl": latest.sl_price,
        "tp": latest.tp_price,
        "confidence": latest.confidence,
        "pattern": latest.pattern,
        "horizon_bars": latest.horizon_bars,
        "metadata": latest.metadata,
        "regime": regime,
        "regime_name": regime_name,
        "bet_size": round(bet_size, 2),
        "status": "PENDING",
    }
    if pending_key in PENDING:
        return
    with SIGNALS_LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    log.info(f"📍 NEW SIGNAL {symbol} {latest.side} entry={latest.entry_zone:.6g} SL={latest.sl_price:.6g} TP={latest.tp_price:.6g} conf={latest.confidence:.2f}")

    PENDING[pending_key] = {
        "sym": symbol,  # FIX 2026-09-02 (audit CRITICAL): check_fills/check_exits читают order["sym"]
        "entry_zone": latest.entry_zone,
        "sl": latest.sl_price,
        "tp": latest.tp_price,
        "signal_ts": latest.ts,
        # LIMIT placed at close of signal bar → fill window starts at the NEXT
        # bar's open. Including the signal bar would auto-fill every signal
        # (its low is below its close by construction of the sweep).
        "expiry_ts": latest.ts + (EXPIRY_BARS + 1) * 15 * 60 * 1000,
        "exit_target_ts": latest.ts + HOLD_BARS * 15 * 60 * 1000,
        "side": latest.side,
        "confidence": latest.confidence,
        "metadata": latest.metadata,
    }

    # LIVE-исполнение (2026-08-30): та же post-only лимитка, что и в paper-модели,
    # но реальный ордер на демо. Paper-трекинг продолжается параллельно (не мешает).
    try:
        _live_place(symbol, latest.side, latest.entry_zone, latest.sl_price,
                    latest.ts, latest.confidence)
    except Exception as e:
        log.warning(f"LIVE-PLACE exception {symbol}: {e}")


def check_fills():
    """
    For each pending BUY order, fetch M15 bars in [signal_ts + 1 bar, expiry_ts]
    and find first bar where low <= entry_zone. The signal bar itself is
    EXCLUDED: the LIMIT order is placed at that bar's close, so it cannot fill
    on it.

    Conservative fill model:
      - For BUY LIMIT at price E, bar with low <= E → filled at E (maker).
      - We DO NOT fill retroactively for stale signals (handled in init).
    """
    now_ms = int(time.time() * 1000)
    for sym_key, order in list(PENDING.items()):
        if order.get("filled") or order.get("expired") or order.get("exit_recorded"):
            continue
        sym = order["sym"]
        signal_ts = order["signal_ts"]
        expiry_ts = order["expiry_ts"]

        # Fetch bars AFTER the signal bar (LIMIT live from next bar open)
        start = signal_ts + 15 * 60 * 1000
        count = EXPIRY_BARS + 2
        df = fetch_bars_at_or_after(sym, start, count=count)
        if df is None or len(df) == 0:
            # Fetch failure/transient gap: retry next cycle. NEVER expire on
            # missing data — the bar data may show a fill we haven't seen yet.
            continue
        else:
            # Trim to live window: bars in [signal_ts+1bar, expiry_ts]
            df = df[(df["ts"] > signal_ts) & (df["ts"] <= expiry_ts)]

        side = order["side"]
        entry_zone = order["entry_zone"]

        # Find first bar where price touched entry_zone
        if side == "BUY":
            hit = df[df["l"] <= entry_zone]
        else:
            hit = df[df["h"] >= entry_zone]

        if hit.empty:
            # Not yet filled — let it run until expiry
            if now_ms > expiry_ts:
                order["expired"] = True
                with FILLS_LOG.open("a") as f:
                    f.write(json.dumps({
                        "ts": expiry_ts,
                        "ts_iso": datetime.fromtimestamp(expiry_ts/1000, tz=timezone.utc).isoformat(),
                        "symbol": sym,
                        "side": side,
                        "outcome": "EXPIRED",
                        "entry_zone": entry_zone,
                        "sl": order["sl"],
                        "tp": order["tp"],
                        "signal_ts": signal_ts,
                        "confidence": order["confidence"],
                    }) + "\n")
                log.info(f"⏰ EXPIRED {sym} (price never reached entry_zone)")
            continue

        fill_bar = hit.iloc[0]
        # Fill price: post-only LIMIT fills AT our limit price (maker)
        fill_price = entry_zone
        fill_ts = int(fill_bar["ts"])

        order["filled"] = True
        order["fill_ts"] = fill_ts
        order["fill_price"] = fill_price
        # Hold window counts from the FILL, not from the signal
        order["exit_target_ts"] = fill_ts + HOLD_BARS * 15 * 60 * 1000

        with FILLS_LOG.open("a") as f:
            f.write(json.dumps({
                "ts": fill_ts,
                "ts_iso": datetime.fromtimestamp(fill_ts/1000, tz=timezone.utc).isoformat(),
                "symbol": sym,
                "side": side,
                "fill_price": fill_price,
                "entry_zone": entry_zone,
                "sl": order["sl"],
                "tp": order["tp"],
                "signal_ts": signal_ts,
                "confidence": order["confidence"],
            }) + "\n")
        log.info(f"✅ FILLED {sym} {side} @ {fill_price:.6g} (limit {entry_zone:.6g}, filled at bar {datetime.fromtimestamp(fill_ts/1000, tz=timezone.utc).isoformat()})")


def check_exits():
    """
    For each filled position, evaluate exit using HISTORICAL M15 bars covering
    [fill_ts, exit_target_ts]. Walk bar-by-bar and apply SL/TP/TIME rules.
    This is the correct way: we are replaying what the market did during the
    4h hold window, not asking "what is the price right now".
    """
    now_ms = int(time.time() * 1000)
    for sym_key, order in list(PENDING.items()):
        if not order.get("filled") or order.get("exit_recorded"):
            continue
        sym = order["sym"]
        exit_target_ts = order["exit_target_ts"]
        # Don't evaluate exit until either (a) we are past exit_target_ts, OR
        # (b) the window is fully closed historically (always true for past signals)
        # For live signals, we wait until now_ms >= exit_target_ts.
        if now_ms < exit_target_ts:
            continue

        fill_ts = order["fill_ts"]
        fill_px = order["fill_price"]
        sl_px = order["sl"]
        tp_px = order["tp"]
        side = order["side"]
        signal_ts = order["signal_ts"]

        # Fetch the entire hold window (from FILL bar, not signal bar)
        count = HOLD_BARS + 2
        df = fetch_bars_at_or_after(sym, fill_ts, count=count)
        if df is None or len(df) == 0:
            log.warning(f"⚠️ EXIT-SKIP {sym}: no bars available for hold window")
            continue
        # Trim to the hold window: bars strictly before exit_target_ts.
        # The fetch has a +2 buffer; without trimming, the TIME exit would
        # use a bar up to 30 min after the intended horizon.
        df = df[df["ts"] < order["exit_target_ts"]]
        if df.empty:
            log.warning(f"⚠️ EXIT-SKIP {sym}: no bars within hold window")
            continue

        # Walk bars in order; SL-only exit model (VALIDATION_v2_SL800.md):
        # validated edge is the 4h forward close; TP=2R chopped it negative.
        # No TP — exit at SL or at hold end (TIME at 4h close).
        outcome = None
        exit_bar_idx = None
        exit_price_used = None

        for i, bar in df.iterrows():
            if side == "BUY":
                sl_hit = bar["l"] <= sl_px
            else:
                sl_hit = bar["h"] >= sl_px
            if sl_hit:
                outcome = "SL"
                exit_bar_idx = i
                exit_price_used = sl_px
                break

        # No SL hit during hold → TIME exit at last bar's close (4h horizon)
        if outcome is None:
            outcome = "TIME"
            exit_bar_idx = len(df) - 1
            exit_price_used = float(df.iloc[-1]["c"])

        exit_bar = df.iloc[exit_bar_idx]
        exit_bar_ts = int(exit_bar["ts"])

        if side == "BUY":
            ret = (exit_price_used - fill_px) / fill_px
        else:
            ret = (fill_px - exit_price_used) / fill_px

        # Win/loss classification (loss = negative net of maker fee)
        ret_net_maker = ret - 0.0010
        is_win = ret_net_maker > 0

        order["exit_recorded"] = True
        order["exit_ts"] = exit_bar_ts
        order["exit_price"] = exit_price_used
        order["ret_gross"] = ret
        order["outcome"] = outcome
        order["is_win"] = is_win

        with EXITS_LOG.open("a") as f:
            f.write(json.dumps({
                "ts": exit_bar_ts,
                "ts_iso": datetime.fromtimestamp(exit_bar_ts/1000, tz=timezone.utc).isoformat(),
                "evaluated_at": now_ms,
                "evaluated_at_iso": datetime.fromtimestamp(now_ms/1000, tz=timezone.utc).isoformat(),
                "symbol": sym,
                "side": side,
                "fill_price": fill_px,
                "entry_zone": order["entry_zone"],
                "sl": sl_px,
                "tp": tp_px,
                "exit_price": exit_price_used,
                "outcome": outcome,
                "ret_gross": ret,
                "ret_net_maker": ret_net_maker,
                "ret_net_taker": ret - 0.0025,
                "is_win": is_win,
                "hold_bars": int(exit_bar_idx) + 1,
                "signal_ts": signal_ts,
                "fill_ts": fill_ts,
            }) + "\n")
        log.info(f"🏁 EXIT {sym} {outcome} fill={fill_px:.6g} exit={exit_price_used:.6g} ret={ret*100:+.2f}% net_maker={ret_net_maker*100:+.2f}% WIN={is_win}")


def cleanup_old():
    for key in list(PENDING.keys()):
        if PENDING[key].get("exit_recorded") or PENDING[key].get("expired"):
            del PENDING[key]


def load_outcome_index() -> dict:
    """
    (symbol, signal_ts) -> {'filled', 'fill_ts', 'fill_price', 'expired', 'exit_recorded'}

    Rebuilt from FILLS_LOG/EXITS_LOG on startup. Two restart-safety problems
    this solves:
      1. hydrate used to quarantine every signal older than STALE_MS — a filled
         but not-yet-exited position was silently dropped on restart (server
         reboot 2026-08-28 16:48 MSK lost 4 fills' exits).
      2. a filled signal must never be re-logged as EXPIRED or re-filled after
         a restart (fill-wins rule: the FILL record is ground truth).
    """
    idx: dict = {}

    def _load(path: Path, kind: str):
        if not path.exists():
            return
        with path.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                key = (rec.get("symbol"), rec.get("signal_ts"))
                if key[0] is None or key[1] is None:
                    continue
                e = idx.setdefault(key, {})
                if kind == "exit":
                    e["exit_recorded"] = True
                elif rec.get("outcome") == "EXPIRED":
                    e["expired"] = True
                elif rec.get("fill_price") is not None:
                    e["filled"] = True
                    e["fill_ts"] = rec["ts"]
                    e["fill_price"] = rec["fill_price"]

    _load(FILLS_LOG, "fill")
    _load(EXITS_LOG, "exit")
    return idx


def hydrate_from_signals_log():
    """
    On startup, rebuild PENDING from signals.jsonl reconciled against
    FILLS_LOG/EXITS_LOG:
      - exit already recorded          → skip (fully processed)
      - fill recorded, no exit         → restore as open position (any age):
                                           check_exits() records the exit
      - unfilled + past expiry         → backfill one EXPIRED record (guard: none exists)
      - unfilled + still inside window → restore as pending
    Signals are keyed by (symbol, signal_ts): two live orders on the same
    symbol (e.g. a re-sweep an hour later) must not overwrite each other.
    """
    index = load_outcome_index()
    if not SIGNALS_LOG.exists():
        return
    now_ms = int(time.time() * 1000)
    loaded = backfilled = skipped = 0
    seen = set()
    with SIGNALS_LOG.open() as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            sym = rec["symbol"]
            signal_ts = rec["ts"]
            key = (sym, signal_ts)
            if key in seen:
                continue
            seen.add(key)
            out = index.get(key, {})
            if out.get("exit_recorded"):
                skipped += 1
                continue
            base = {
                "sym": sym,
                "entry_zone": rec["entry_zone"],
                "sl": rec["sl"],
                "tp": rec["tp"],
                "signal_ts": signal_ts,
                "side": rec["side"],
                "confidence": rec.get("confidence", 0.5),
                "metadata": rec.get("metadata", {}),
            }
            if out.get("filled"):
                # Filled position whose exit was never recorded (restart gap).
                # expiry_ts=fill_ts is in the past → check_fills skips it;
                # check_exits() replays the hold window from historical bars.
                PENDING[f"{sym}:{signal_ts}"] = {
                    **base,
                    "expiry_ts": out["fill_ts"],
                    "filled": True,
                    "fill_ts": out["fill_ts"],
                    "fill_price": out["fill_price"],
                    "exit_target_ts": out["fill_ts"] + HOLD_BARS * 15 * 60 * 1000,
                }
                loaded += 1
                continue
            # Same expiry formula as scan_symbol: LIMIT placed at signal-bar
            # close, live for EXPIRY_BARS bars starting next bar open.
            expiry_ts = signal_ts + (EXPIRY_BARS + 1) * 15 * 60 * 1000
            if now_ms > expiry_ts:
                if not out.get("expired"):
                    with FILLS_LOG.open("a") as f:
                        f.write(json.dumps({
                            "ts": expiry_ts,
                            "ts_iso": datetime.fromtimestamp(expiry_ts/1000, tz=timezone.utc).isoformat(),
                            "symbol": sym,
                            "side": rec["side"],
                            "outcome": "EXPIRED",
                            "entry_zone": rec["entry_zone"],
                            "sl": rec["sl"],
                            "tp": rec["tp"],
                            "signal_ts": signal_ts,
                            "confidence": rec.get("confidence", 0.5),
                        }) + "\n")
                    backfilled += 1
                continue
            PENDING[f"{sym}:{signal_ts}"] = {
                **base,
                "expiry_ts": expiry_ts,
                "exit_target_ts": signal_ts + HOLD_BARS * 15 * 60 * 1000,
            }
            loaded += 1
    log.info(f"🔄 Hydrated {loaded} pending signals, backfilled {backfilled} expired, skipped {skipped} already-exited")


# ═══════════════════════════════════════════════════════════════════════════
# LIVE MODE (2026-08-30, owner-approved 2-day demo test)
# Валидированная модель: LIMIT post-only на entry_zone + SL-only (структурный
# SL или 800bps hard-cap, БЕЗ TP) + TIME exit через 4h. Включение: флаг
# dn_sweep_live_enabled=true в trading_mode.json + kill_switch=false.
# Риск: risk_per_trade из конфига ($25), max 1 живая позиция на символ.
# ═══════════════════════════════════════════════════════════════════════════
LIVE_LEDGER = Path("/root/tradingos/patterns/reversal_v1/paper/live_ledger.jsonl")
LIVE_ORDERS = {}  # symbol -> {order_id, entry_zone, sl, qty, signal_ts, placed_ts, filled}


def _live_config() -> tuple:
    """(enabled, kill_switch) из trading_mode.json — fail-closed."""
    try:
        cfg = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
        return (bool(cfg.get("dn_sweep_live_enabled", False)),
                bool(cfg.get("kill_switch", True)))
    except Exception:
        return False, True


def _live_risk() -> float:
    """Риск на сделку из конфига ($25 default)."""
    try:
        cfg = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
        return float(cfg.get("risk_per_trade", 25) or 25)
    except Exception:
        return 25.0


def _live_max_risk() -> float:
    """Абсолютный кэп риска на сделку (audit 2026-08-30): бамп до
    min_qty/minNotional не должен превышать max_position_size_pct × SL-дистанцию.
    Практический кэп = max_risk_usd из конфига (default $1000)."""
    try:
        cfg = json.loads(Path("/root/tradingos/operations/trading_mode.json").read_text())
        return float(cfg.get("max_risk_usd", 1000.0) or 1000.0)
    except Exception:
        return 1000.0


def _live_restore_orders():
    """FIX 2026-08-30 (audit CRITICAL): восстановить LIVE_ORDERS из ledger после
    рестарта. Раньше всё было только в памяти → висящие лимитки-сироты не
    отменялись никогда, позиции без TIME-exit, дубликаты лимиток."""
    if not LIVE_LEDGER.exists():
        return
    restored = 0
    with LIVE_LEDGER.open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            sym = r.get("symbol", "")
            evt = r.get("evt", "")
            if evt == "PLACED":
                # PLACED — рабочая запись (FILLED/TIME_EXIT/EXPIRED перезапишут)
                LIVE_ORDERS[sym] = {
                    "order_id": r.get("order_id", ""),
                    "entry_zone": float(r.get("entry_zone", 0) or 0),
                    "sl": float(r.get("sl", 0) or 0),
                    "qty": float(r.get("qty", 0) or 0),
                    "signal_ts": int(r.get("signal_ts", 0) or 0),
                    "placed_ts": int(_parse_iso_ms(r.get("ts", ""))),
                    "filled": False,
                    "confidence": 0.5,
                    "side": r.get("side", "Buy"),
                }
            elif evt == "FILLED" and sym in LIVE_ORDERS:
                LIVE_ORDERS[sym]["filled"] = True
                LIVE_ORDERS[sym]["fill_ts"] = int(_parse_iso_ms(r.get("ts", "")))
            elif evt in ("TIME_EXIT", "EXPIRED", "CANCELLED"):
                LIVE_ORDERS.pop(sym, None)
    if LIVE_ORDERS:
        log.info(f"🔄 LIVE восстановлено {len(LIVE_ORDERS)} ордер(ов) из ledger")
        for sym, o in LIVE_ORDERS.items():
            log.info(f"   {sym}: side={o.get('side')} filled={o.get('filled')} "
                     f"order_id={o.get('order_id')[:8]}…" if o.get("order_id") else "…")


def _parse_iso_ms(iso: str) -> float:
    try:
        return datetime.fromisoformat(str(iso)).timestamp() * 1000
    except Exception:
        return 0.0


def _live_signed_post(path: str, body: str) -> dict:
    """Raw signed POST (order create/cancel) — тот же паттерн, что ручной контур."""
    import urllib.parse as _up
    end_ts = int(time.time() * 1000)
    ts = str(end_ts)
    payload = f"{ts}{KEY}{5000}{body}"
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    r = httpx.post(f"{BASE}{path}", content=body,
                   headers={"X-BAPI-API-KEY": KEY, "X-BAPI-TIMESTAMP": ts,
                            "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sig,
                            "Content-Type": "application/x-www-form-urlencoded"},
                   timeout=10)
    return r.json()


def _live_has_position(symbol: str) -> bool:
    """Есть ли открытая позиция по символу (live)."""
    q = f"category=linear&symbol={symbol}"
    try:
        d = _signed_get("/v5/position/list", q, timeout=10).json()
        items = (d.get("result") or {}).get("list") or []
        return any(float(p.get("size", 0) or 0) > 0 for p in items)
    except Exception:
        return False


def _live_position_side(symbol: str) -> str:
    """Фактический side позиции ("Buy"/"Sell") или "" для закрытия (audit fix)."""
    q = f"category=linear&symbol={symbol}"
    try:
        d = _signed_get("/v5/position/list", q, timeout=10).json()
        items = (d.get("result") or {}).get("list") or []
        for p in items:
            if float(p.get("size", 0) or 0) > 0:
                return str(p.get("side", "") or "")
    except Exception:
        pass
    return ""


def _live_position_is_long(symbol: str) -> bool:
    return _live_position_side(symbol).upper() == "BUY"


def _live_place(symbol: str, side: str, entry_zone: float, sl: float,
                signal_ts: int, confidence: float) -> dict | None:
    """Поставить post-only лимитку на entry_zone со структурным SL, БЕЗ TP."""
    enabled, kill = _live_config()
    if not enabled:
        return None
    if kill:
        log.warning(f"🛑 LIVE-SKIP {symbol}: kill_switch ON")
        return {"ok": False, "reason": "kill_switch"}
    if symbol in LIVE_ORDERS:
        return {"ok": False, "reason": "live_order_exists"}
    if _live_has_position(symbol):
        log.info(f"⏭️ LIVE-SKIP {symbol}: позиция уже открыта")
        return {"ok": False, "reason": "position_open"}
    # Сайзинг: риск $X / дистанция до SL (минимум 0.8% цены — как в валидации)
    side_map = {"BUY": "Buy", "SELL": "Sell"}
    bybit_side = side_map.get(str(side).upper())
    if not bybit_side:
        return {"ok": False, "reason": f"invalid_side {side!r}"}
    risk = _live_risk()
    sl_dist = abs(entry_zone - sl)
    sl_dist = max(sl_dist, entry_zone * 0.008)  # мин. 800bps
    if sl_dist <= 0:
        return {"ok": False, "reason": "sl_dist_zero"}
    qty_raw = risk / sl_dist
    # Округлить вниз до qtyStep (грубо 1% цены → qty; реальный lotSizeFilter получим)
    try:
        ri = _signed_get("/v5/market/instruments-info",
                         f"category=linear&symbol={symbol}", timeout=10).json()
        lot = ((ri.get("result") or {}).get("list") or [{}])[0].get("lotSizeFilter", {})
        qty_step = float(lot.get("qtyStep", "0.001") or 0.001)
        min_qty = float(lot.get("minOrderQty", "0.001") or 0.001)
        max_qty = float(lot.get("maxOrderQty", "1e12") or "1e12")
        min_notional = float(lot.get("minNotionalValue", "5") or 5)
    except Exception:
        qty_step, min_qty, max_qty, min_notional = 0.001, 0.001, 1e12, 5.0
    qty = int(qty_raw / qty_step) * qty_step
    if qty < min_qty:
        qty = min_qty
    if qty * entry_zone < min_notional:
        qty = (int(min_notional / entry_zone / qty_step) + 1) * qty_step
    # FIX 2026-08-30 (audit HIGH): qty в научной нотации ("1e-05") → Bybit
    # отклоняет ордер. Форматируем как исполнитель manual_signal (.8g через float()).
    qty = float(f"{qty:.8g}")
    # FIX 2026-09-02: cap qty at maxOrderQty (prevents overflow for cheap coins)
    if qty > max_qty:
        qty = max_qty
    # FIX 2026-08-30 (audit MEDIUM): бамп до min_qty/minNotional может поднять
    # риск выше целевого — кэп по max_position_size_pct (1% equity ≈ $1800).
    _r = risk / sl_dist  # фактический риск $ на единицу qty
    if qty * _r > _live_max_risk():
        qty = int(_live_max_risk() / _r / qty_step) * qty_step
        qty = float(f"{qty:.8g}")
        if qty <= 0:
            return {"ok": False, "reason": "risk_cap_below_min_notional"}
    body = "&".join([
        "category=linear", f"symbol={symbol}", f"side={bybit_side}",
        "orderType=Limit", f"qty={qty}", f"price={entry_zone}",
        "timeInForce=PostOnly", f"stopLoss={sl}",
        "tpTriggerBy=LastPrice", "slTriggerBy=LastPrice", "positionIdx=0",
    ])
    res = _live_signed_post("/v5/order/create", body)
    if res.get("retCode") != 0:
        log.warning(f"❌ LIVE-PLACE FAIL {symbol}: {res.get('retMsg', '?')}")
        return {"ok": False, "reason": res.get("retMsg", "?")}
    oid = ((res.get("result") or {}).get("orderId", ""))
    # FIX 2026-08-30 (audit CRITICAL): сохраняем side — TIME-exit раньше всегда
    # брал дефолт "Buy" (side пустой → для LONG шёл reduceOnly Buy = отказ).
    LIVE_ORDERS[symbol] = {
        "order_id": oid, "entry_zone": entry_zone, "sl": sl, "qty": qty,
        "signal_ts": signal_ts, "placed_ts": int(time.time() * 1000),
        "filled": False, "confidence": confidence, "side": bybit_side,
    }
    with LIVE_LEDGER.open("a") as f:
        f.write(json.dumps({
            "evt": "PLACED", "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol, "side": bybit_side, "entry_zone": entry_zone,
            "sl": sl, "qty": qty, "risk_usd": round(qty * sl_dist, 2),
            "order_id": oid, "signal_ts": signal_ts,
        }) + "\n")
    log.info(f"📌 LIVE LIMIT: {symbol} {bybit_side} qty={qty} @ {entry_zone:.6g} SL={sl:.6g}")
    return {"ok": True, "order_id": oid}


def _live_poll_cancel():
    """Отменить неисполненные лимитки после EXPIRY (30 мин).

    FIX 2026-08-30 (audit HIGH): retCode раньше игнорировался — запись
    удалялась даже если ордер УЖЕ исполнился (race: post-only каснулся зоны
    между циклами) → сирота-позиция без TIME-exit. Теперь: при retCode!=0
    (кроме 'order not exists/already filled') проверяем позицию — если
    появилась, переводим ордер в FILLED-состояние вместо удаления."""
    now = int(time.time() * 1000)
    for sym in list(LIVE_ORDERS.keys()):
        o = LIVE_ORDERS[sym]
        if o.get("filled"):
            continue
        if now - o["placed_ts"] <= (EXPIRY_BARS + 1) * 15 * 60 * 1000:
            continue
        body = f"category=linear&symbol={sym}&orderId={o['order_id']}"
        try:
            res = _live_signed_post("/v5/order/cancel", body)
            ret = int(res.get("retCode", -1) or -1)
            with LIVE_LEDGER.open("a") as f:
                f.write(json.dumps({
                    "evt": "EXPIRED" if ret == 0 else f"CANCEL_RET_{ret}",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "symbol": sym, "order_id": o["order_id"],
                    "entry_zone": o["entry_zone"], "ret": ret,
                }) + "\n")
            if ret == 0:
                log.info(f"⏰ LIVE EXPIRED {sym} (лимитка не исполнилась за {EXPIRY_BARS} бар)")
                LIVE_ORDERS.pop(sym, None)
            elif o.get("order_id") and _live_has_position(sym):
                # Отмена не прошла, но позиция есть → это был fill между циклами
                log.warning(f"⚠️ LIVE {sym}: cancel ret={ret}, но позиция есть → FILLED")
                o["filled"] = True
                o["fill_ts"] = now
                o["side"] = o.get("side") or ("Buy" if sym else "Buy")
                with LIVE_LEDGER.open("a") as f:
                    f.write(json.dumps({
                        "evt": "FILLED", "ts": datetime.now(timezone.utc).isoformat(),
                        "symbol": sym, "order_id": o["order_id"],
                        "entry_zone": o["entry_zone"], "sl": o["sl"], "qty": o["qty"],
                    }) + "\n")
            elif ret == 110001:
                # FIX 2026-09-02 (audit): retCode 110001 = "order not exists or
                # too late to cancel" — ордера на бирже НЕТ. Позиции тоже нет
                # (проверено выше). Ордер исполнен и закрыт SL либо фантом —
                # удаляем запись, иначе вечный cancel-loop (6069 повторов
                # по VETUSDT в старом логе).
                log.warning(f"🧹 LIVE {sym}: cancel ret=110001 (order gone, no position) → purge")
                LIVE_ORDERS.pop(sym, None)
            else:
                # cancel не удался и позиции нет — ордер мог исполниться и быть
                # закрытым SL или это фантом; проверяем статус ордера в след. цикле.
                log.warning(f"LIVE-CANCEL {sym} retCode={ret} — оставляю в трекинге, повтор")
        except Exception as e:
            log.warning(f"LIVE-CANCEL ERR {sym}: {e} — оставляю в трекинге")


def _live_check_fills_and_exits():
    """Статус открытых live-позиций: fill detection + TIME exit через 4h.

    FIX 2026-08-30 (audit HIGH+CRITICAL): (1) side теперь сохранён в
    LIVE_ORDERS — LONG закрывался reduceOnly Buy (= отказ); (2) при ошибке
    закрытия запись НЕ удаляется — ретрай в следующем цикле; (3) закрытие
    подтверждается отсутствием позиции."""
    now = int(time.time() * 1000)
    for sym in list(LIVE_ORDERS.keys()):
        o = LIVE_ORDERS[sym]
        # Fill detection: позиция появилась на бирже?
        if not o.get("filled"):
            if _live_has_position(sym):
                o["filled"] = True
                o["fill_ts"] = now
                # FIX (audit HIGH): сохраняем фактический side позиции
                try:
                    _ps = _live_position_side(sym)
                    if _ps:
                        o["side"] = _ps
                except Exception:
                    pass
                with LIVE_LEDGER.open("a") as f:
                    f.write(json.dumps({
                        "evt": "FILLED", "ts": datetime.now(timezone.utc).isoformat(),
                        "symbol": sym, "order_id": o["order_id"],
                        "entry_zone": o["entry_zone"], "sl": o["sl"], "qty": o["qty"],
                        "side": o.get("side"),
                    }) + "\n")
                log.info(f"✅ LIVE FILLED {sym} @ {o['entry_zone']:.6g} side={o.get('side')}")
            continue
        # TIME exit: 4h после fill
        exit_at = o.get("fill_ts", o.get("placed_ts")) + HOLD_BARS * 15 * 60 * 1000
        if now < exit_at:
            continue
        # Закрыть market reduce-only — сторона ПРОТИВ позиции
        pos_side = str(o.get("side", "")).upper()  # "Buy" для LONG
        close_side = "Sell" if pos_side == "BUY" else "Buy"
        if not o.get("side"):
            # fallback: восстановить из позиции
            close_side = "Sell" if _live_position_is_long(sym) else "Buy"
            o["side"] = "Buy" if close_side == "Sell" else "Sell"
        body = "&".join(["category=linear", f"symbol={sym}", f"side={close_side}",
                         "orderType=Market", f"qty={float(f'{o['qty']:.8g}')}",
                         "reduceOnly=true", "positionIdx=0"])
        try:
            res = _live_signed_post("/v5/order/create", body)
            ret = int(res.get("retCode", -1) or -1)
            with LIVE_LEDGER.open("a") as f:
                f.write(json.dumps({
                    "evt": "TIME_EXIT" if ret == 0 else f"TIME_EXIT_RETRY_{ret}",
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "symbol": sym, "order_id": o["order_id"], "ret": ret,
                    "msg": res.get("retMsg", ""), "side": close_side,
                }) + "\n")
            if ret == 0:
                log.info(f"🏁 LIVE TIME-EXIT {sym} (4h hold)")
                LIVE_ORDERS.pop(sym, None)
            else:
                # РЕТРАЙ: не удаляем запись — попробуем в следующем цикле (30с)
                o["exit_attempts"] = o.get("exit_attempts", 0) + 1
                log.warning(f"⚠️ LIVE TIME-EXIT {sym} retCode={ret}: {res.get('retMsg','')} "
                            f"(попытка {o['exit_attempts']})")
                if o["exit_attempts"] >= 10:
                    # 10 неудач ≈ 5мин: падаем в emergency — закрываем без reduceOnly?
                    # НЕТ. Оставляем позицию (у неё стоит SL) и зовём человека.
                    log.error(f"🚨 LIVE TIME-EXIT FAILED 10x {sym} — НУЖНО РУЧНОЕ ВМШАТЕЛЬСТВО. "
                              f"Позиция остаётся с SL={o['sl']}")
                    with LIVE_LEDGER.open("a") as f:
                        f.write(json.dumps({
                            "evt": "TIME_EXIT_MANUAL_NEEDED",
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "symbol": sym, "order_id": o["order_id"],
                        }) + "\n")
                    LIVE_ORDERS.pop(sym, None)
        except Exception as e:
            log.warning(f"LIVE-EXIT ERR {sym}: {e} — ретрай в следующем цикле")
            o["exit_attempts"] = o.get("exit_attempts", 0) + 1


def main_loop():
    log.info(f"Starting DN-sweep live paper detector on {len(WHITELIST)} symbols")
    log.info(f"  HOLD: {HOLD_BARS} bars ({HOLD_BARS*15/60}h)")
    log.info(f"  EXPIRY: {EXPIRY_BARS} bars ({EXPIRY_BARS*15}min)")
    log.info(f"  Whitelist: {WHITELIST}")
    hydrate_from_signals_log()
    _live_restore_orders()  # FIX (audit CRITICAL): восстановление live-ордеров после рестарта
    cycle = 0
    while True:
        try:
            cycle += 1
            if cycle % 5 == 1 or cycle == 1:
                log.info(f"Cycle {cycle}: scanning...")
            for sym in WHITELIST:
                scan_symbol(sym)
            check_fills()
            check_exits()
            cleanup_old()
            # LIVE (2026-08-30): управление реальными лимитками/позициями
            _live_poll_cancel()
            _live_check_fills_and_exits()
        except Exception as e:
            log.error(f"Cycle error: {e}")
            log.exception(e)
        time.sleep(30)


if __name__ == "__main__":
    main_loop()
