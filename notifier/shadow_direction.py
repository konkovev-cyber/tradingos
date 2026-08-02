"""
notifier/shadow_direction.py
Shadow Direction Validation v3 — BUY ONLY.

Reality Pilot v3: SELL direction disabled (negative edge).
- Не отправляет ордера.
- Только логирует каждое BUY решение.
- SELL всегда BLOCK (decision: 2026-08-01).
"""
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger("ShadowDirection")

# BUY-only shadow log
SHADOW_LOG = Path("/root/tradingos/logs/buy_only_shadow_events.jsonl")
SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)


def evaluate_shadow_direction(symbol, price, ema50, ema200, atr_pct=None,
                              direction_ema_only="BUY"):
    """v3: SELL = BLOCK always (BUY only validation).
    
    Returns dict with old, new, ema_cross, ema_dist_pct, reasons.
    """
    ema_cross = "BEARISH" if ema50 > ema200 else "BULLISH"
    ema_dist_pct = (price - ema50) / ema50 * 100 if ema50 else 0
    
    old = direction_ema_only
    new = direction_ema_only
    reasons = []
    
    # v3 RULE: SELL = BLOCK always
    if old == "SELL":
        new = "BLOCK"
        reasons.append("v3 rule: SELL disabled (v1 PF 0.14, negative edge)")
    
    return {
        "old": old,
        "new": new,
        "ema_cross": ema_cross,
        "ema_dist_pct": round(ema_dist_pct, 3),
        "reasons": reasons,
    }


def log_shadow_event(symbol, price, ema50, ema200, atr_pct,
                     direction_ema_only, market_context=None):
    """Записать shadow решение в лог."""
    decision = evaluate_shadow_direction(
        symbol, price, ema50, ema200, atr_pct, direction_ema_only
    )
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "v3_buy_only",
        "symbol": symbol,
        "price": price,
        "ema50": ema50,
        "ema200": ema200,
        "ema_cross": decision["ema_cross"],
        "ema_dist_pct": decision["ema_dist_pct"],
        "atr_pct": atr_pct,
        "old_direction": decision["old"],
        "new_direction": decision["new"],
        "reasons": decision["reasons"],
        "market_context": market_context or {},
    }
    with open(SHADOW_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")
    log.info(
        f"SHADOW v3 {symbol}: old={decision['old']} new={decision['new']} "
        f"reasons={decision['reasons']}"
    )
    return event


def get_shadow_stats():
    """Получить статистику по shadow решениям."""
    if not SHADOW_LOG.exists():
        return {"total": 0}
    with open(SHADOW_LOG) as f:
        events = [json.loads(l) for l in f if l.strip()]
    
    total = len(events)
    if total == 0:
        return {"total": 0}
    
    old_counts = {}
    new_counts = {}
    blocked = []
    for e in events:
        old_counts[e["old_direction"]] = old_counts.get(e["old_direction"], 0) + 1
        new_counts[e["new_direction"]] = new_counts.get(e["new_direction"], 0) + 1
        if e["new_direction"] == "BLOCK":
            blocked.append(e)
    
    return {
        "version": "v3_buy_only",
        "total": total,
        "old_counts": old_counts,
        "new_counts": new_counts,
        "blocked_count": len(blocked),
        "blocked_reasons": [b["reasons"] for b in blocked],
    }
