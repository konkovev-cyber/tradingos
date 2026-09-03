"""
guardian/opportunity_loss.py
Opportunity Loss Tracker — collects rejected candidates and evaluates them post-hoc.
Does NOT modify strategy, model, or trading logic.
Only collects statistics.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("opportunity_loss")

REJECTED_LOG = Path("/root/tradingos/guardian/rejected_candidates.jsonl")
EVALUATED_LOG = Path("/root/tradingos/guardian/evaluated_rejections.jsonl")


def record_rejection(symbol: str, direction: str, probability: float, score: int,
                     quality: str, reason: str, entry_price: float, entry_time: float,
                     atr: float = 0.0,
                     raw_qty: Optional[float] = None, required_qty: Optional[float] = None,
                     risk_required: Optional[float] = None, qty_step: Optional[float] = None,
                     min_order_qty: Optional[float] = None, min_notional: Optional[float] = None,
                     meta_v1_prob: Optional[float] = None):
    """
    Record a rejected candidate for later evaluation.
    Called from run_observation.py when a candidate passes filters but is rejected
    at the proposal stage, and from trade_executor.py for pre-order SKIPs
    (reason="MIN_ORDER_QTY": qty below exchange lotSizeFilter).

    min_order_qty/min_notional (lotSizeFilter) позволяют классифицировать
    капитальный барьер: MIN_NOTIONAL_BLOCK vs MIN_QTY_BLOCK (см. execution_diagnostics).

    2026-08-27 (meta_v1 shadow): meta_v1_prob carries the model score so
    rejected_candidates.jsonl can be used to monitor OOS filter performance
    on BOTH passed AND rejected signals (the model only sees accepted today).
    """
    record = {
        "symbol": symbol,
        "direction": direction,
        "probability": round(probability, 4),
        "score": score,
        "quality": quality,
        "reason": reason,
        "entry_price": entry_price,
        "entry_time": entry_time,
        "record_time": datetime.now(timezone.utc).isoformat(),
        "atr": round(atr, 8),
    }
    if meta_v1_prob is not None:
        record["meta_v1_prob"] = round(float(meta_v1_prob), 4)
    if raw_qty is not None:
        record["raw_qty"] = raw_qty
    if required_qty is not None:
        record["required_qty"] = required_qty
    if risk_required is not None:
        record["risk_required"] = risk_required
    if qty_step is not None:
        record["qty_step"] = qty_step
    if min_order_qty is not None:
        record["min_order_qty"] = min_order_qty
    if min_notional is not None:
        record["min_notional"] = min_notional
    REJECTED_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(REJECTED_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def evaluate_rejections(hours_lookback: float = 24, min_age_minutes: float = 60):
    """
    Evaluate past rejections: check if price would have hit TP or SL.
    Only evaluates candidates older than min_age_minutes.
    
    Loads rejected_candidates.jsonl, checks current price, 
    and writes results to evaluated_rejections.jsonl.
    
    Does NOT change any trading logic.
    """
    if not REJECTED_LOG.exists():
        return []

    import hmac, hashlib, httpx
    
    # Load creds
    ak, as_ = "", ""
    env_path = "/root/trading_brain_v4/research/execution/.env"
    try:
        with open(env_path) as f:
            for l in f:
                l = l.strip()
                if l and not l.startswith("#") and "=" in l:
                    k, v = l.split("=", 1)
                    if k.strip() == "BYBIT_API_KEY":
                        ak = v.strip()
                    elif k.strip() == "BYBIT_API_SECRET":
                        as_ = v.strip()
    except FileNotFoundError:
        return []

    cutoff = time.time() - hours_lookback * 3600
    min_age = min_age_minutes * 60
    
    results = []
    seen_symbols = set()

    with open(REJECTED_LOG) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Skip recent entries
            age = time.time() - rec.get("entry_time", 0)
            if age < min_age:
                continue
            # Skip old entries
            if rec.get("entry_time", 0) < cutoff:
                continue
            # Deduplicate by symbol+direction+entry_time
            key = f"{rec['symbol']}_{rec['direction']}_{rec['entry_time']}"
            if key in seen_symbols:
                continue
            seen_symbols.add(key)

            symbol = rec["symbol"]
            direction = rec["direction"]
            entry = rec.get("entry_price", 0)
            atr = rec.get("atr", 0.001)
            reason = rec.get("reason", "unknown")

            if entry <= 0:
                continue

            # Fetch current price
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
                r = httpx.get(f"https://api.bybit.com/v5/market/tickers?{q}", headers=headers, timeout=5)
                d = r.json()
                if d.get("retCode") != 0:
                    continue
                current = float(d["result"]["list"][0].get("lastPrice", 0))
            except Exception:
                continue

            # Simulate 2:1 R:R (same as Reality Pilot)
            sl_offset = atr * 2
            tp_offset = atr * 4
            if direction == "BUY":
                sl_price = entry - sl_offset
                tp_price = entry + tp_offset
                mfe = (current - entry) / entry * 100
                mae = (entry - current) / entry * 100
                hit_tp = current >= tp_price
                hit_sl = current <= sl_price
                r = (current - entry) / sl_offset if sl_offset > 0 else 0
            elif direction == "SELL":
                sl_price = entry + sl_offset
                tp_price = entry - tp_offset
                mfe = (entry - current) / entry * 100
                mae = (current - entry) / entry * 100
                hit_tp = current <= tp_price
                hit_sl = current >= sl_price
                r = (entry - current) / sl_offset if sl_offset > 0 else 0
            else:
                continue

            result = {
                "symbol": symbol,
                "direction": direction,
                "entry": entry,
                "current_price": current,
                "rejection_reason": reason,
                "probability": rec.get("probability", 0),
                "score": rec.get("score", 0),
                "sl_price": round(sl_price, 8),
                "tp_price": round(tp_price, 8),
                "atr": atr,
                "current_r": round(r, 3),
                "mfe_pct": round(mfe, 2),
                "mae_pct": round(mae, 2),
                "would_hit_tp": hit_tp,
                "would_hit_sl": hit_sl,
                "would_win": hit_tp and not hit_sl,
                "would_lose": hit_sl and not hit_tp,
                "entry_time": rec.get("entry_time", 0),
                "evaluation_time": time.time(),
                "age_hours": round((time.time() - rec.get("entry_time", 0)) / 3600, 1),
            }
            results.append(result)

    if results:
        EVALUATED_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(EVALUATED_LOG, "a") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")

        # Summary
        total = len(results)
        wins = sum(1 for r in results if r["would_win"])
        losses = sum(1 for r in results if r["would_lose"])
        hit_tp = sum(1 for r in results if r["would_hit_tp"])
        hit_sl = sum(1 for r in results if r["would_hit_sl"])
        
        logger.info(f"📊 Opportunity Loss: evaluated {total} past rejections")
        logger.info(f"   Would have won:   {wins} ({wins/total*100:.1f}% if wins>0 else 0)")
        logger.info(f"   Would have lost:  {losses}")
        logger.info(f"   Hit TP:           {hit_tp}")
        logger.info(f"   Hit SL:           {hit_sl}")
        logger.info(f"   Would have hit TP: {', '.join(r['symbol'] for r in results if r['would_hit_tp'])}")
        logger.info(f"   Would have hit SL: {', '.join(r['symbol'] for r in results if r['would_hit_sl'])}")

    return results


def generate_weekly_report() -> dict:
    """Generate weekly opportunity loss report."""
    if not EVALUATED_LOG.exists():
        return {"error": "No evaluated data yet"}
    
    results = []
    with open(EVALUATED_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    results.append(json.loads(line))
                except:
                    pass

    if not results:
        return {"evaluated": 0}

    total = len(results)
    wins = sum(1 for r in results if r.get("would_win"))
    losses = sum(1 for r in results if r.get("would_lose"))
    
    return {
        "evaluated": total,
        "would_win": wins,
        "would_lose": losses,
        "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
        "avg_prob": round(sum(r["probability"] for r in results) / total, 3) if total > 0 else 0,
        "avg_score": round(sum(r["score"] for r in results) / total, 1) if total > 0 else 0,
        "top_reasons": _top_reasons(results),
    }


def _top_reasons(results: list) -> list:
    from collections import Counter
    reasons = Counter(r.get("rejection_reason", "unknown") for r in results)
    return [{"reason": r, "count": c} for r, c in reasons.most_common(10)]
