#!/usr/bin/env python3
"""
Execution Diagnostics — агрегированная диагностика исполнения (НЕ фильтр).

Сводная статистика по всем proposal/fill за окно (24h и всё время):

  Signal → Proposal   avg/median/p95, с
  Proposal → Fill     avg/median/p95, с (только исполнившиеся)
  Fill → Exit         avg/median, ч (только закрытые)
  доля proposal, дошедших до Fill
  доля не исполнившихся (cancelled)
  причины отказов (rejected_candidates.jsonl)

Источники: funnel_events.jsonl, signal_log.jsonl, trade_lifecycle.jsonl,
rejected_candidates.jsonl. Ничего не меняет. Позволяет заметить деградацию
исполнения ДО того, как она повлияет на PF.

Выход: печать + reports/execution_diagnostics.json
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
FUNNEL = ROOT / "memory/funnel_events.jsonl"
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"
LIFECYCLE = ROOT / "logs/trades/trade_lifecycle.jsonl"
REJECTED = ROOT / "guardian/rejected_candidates.jsonl"
REPORT = ROOT / "reports/execution_diagnostics.json"

SIGNAL_LOOKBACK = 300.0
DAY = 86400.0


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.open("r"):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(round(p / 100 * (len(s) - 1)))))
    return s[k]


def _agg(xs: list[float]) -> dict:
    return {
        "n": len(xs),
        "avg": round(sum(xs) / len(xs), 1) if xs else None,
        "median": round(_pct(xs, 50), 1) if xs else None,
        "p95": round(_pct(xs, 95), 1) if xs else None,
    }


def _signal_index(symbols: set[str]) -> dict:
    idx: dict[tuple, list] = defaultdict(list)
    if not SIGNAL_LOG.exists():
        return idx
    with SIGNAL_LOG.open("r") as f:
        for line in f:
            if not any(sym in line for sym in symbols):
                continue
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            d = r.get("direction")
            if d in ("BUY", "SELL") and (r.get("final_probability") or 0) >= 0.50:
                try:
                    ts = datetime.fromisoformat(r["timestamp"]).timestamp()
                except Exception:
                    continue
                idx[(r["symbol"], d)].append(ts)
    for k in idx:
        idx[k].sort()
    return idx


def _signal_ts(idx: dict, symbol: str, direction: str, proposal_ts: float) -> float | None:
    arr = idx.get((symbol, direction))
    if not arr:
        return None
    for ts in arr:
        if proposal_ts - SIGNAL_LOOKBACK <= ts <= proposal_ts + 5:
            return ts
    return None


def _classify_block(r: dict) -> str:
    """Капитальный барьер: какой именно лимит биржи не дал открыть позицию.

    MIN_NOTIONAL_BLOCK — требуется qty для minNotional (обычно $5) больше raw;
    MIN_QTY_BLOCK — minOrderQty сам по себе больше raw;
    RISK_TOO_SMALL — иначе (риск не дотягивает даже до меньшего лимита).
    Для старых записей без min_notional используется фолбэк $5.
    """
    entry = r.get("entry_price") or 0
    req = r.get("required_qty") or 0
    step = r.get("qty_step") or 1.0
    if step <= 0:
        step = 1.0
    mq = r.get("min_order_qty")
    mn = r.get("min_notional")
    if mq is None or mn is None or entry <= 0:
        # старые записи: инференс по мин. ноционалу $5
        mq = 1.0
        mn = 5.0
    notional_qty = math.ceil((mn / entry) / step) * step if (mn > 0 and entry > 0) else 0.0
    if req > 0 and abs(req - notional_qty) < max(step, req * 0.01):
        return "MIN_NOTIONAL_BLOCK"
    if req > 0 and abs(req - mq) < max(step, req * 0.01):
        return "MIN_QTY_BLOCK"
    return "RISK_TOO_SMALL"


def main() -> int:
    funnel = _load_jsonl(FUNNEL)
    now = datetime.now(timezone.utc).timestamp()

    proposals: dict[str, dict] = {}
    opened: dict[str, dict] = {}
    for ev in funnel:
        did = ev.get("decision_id", "")
        if not did:
            continue
        if ev.get("event") == "proposal":
            proposals[did] = ev
        elif ev.get("event") == "opened":
            opened[did] = ev

    symbol_set = {p.get("symbol") for p in proposals.values()}
    sig_idx = _signal_index(symbol_set)

    # per-proposal deltas
    sig2prop: list[float] = []
    prop2fill: list[float] = []
    prop2fill_24h: list[float] = []
    proposals_24h = 0
    filled_24h = 0
    for did, p in proposals.items():
        p_ts = p.get("ts") or 0
        if now - p_ts <= DAY:
            proposals_24h += 1
        t_sig = _signal_ts(sig_idx, p.get("symbol", ""), p.get("side", ""), p_ts)
        if t_sig:
            sig2prop.append(p_ts - t_sig)
        o = opened.get(did)
        if o:
            o_ts = o.get("ts") or 0
            prop2fill.append(o_ts - p_ts)
            if now - p_ts <= DAY:
                prop2fill_24h.append(o_ts - p_ts)
                filled_24h += 1

    # holding: Fill → Exit из lifecycle (CLOSED)
    holding: list[float] = []
    for row in _load_jsonl(LIFECYCLE):
        h = row.get("holding_hours")
        if row.get("state") == "CLOSED" and h is not None:
            holding.append(h)

    # причины отказов
    reasons_all: Counter = Counter()
    reasons_24h: Counter = Counter()
    block_reason_all: Counter = Counter()
    block_reason_24h: Counter = Counter()
    for r in _load_jsonl(REJECTED):
        reason = r.get("reason", "?")
        reasons_all[reason] += 1
        rt = r.get("entry_time") or 0
        if now - rt <= DAY:
            reasons_24h[reason] += 1
        # классификация капитального барьера для MIN_ORDER_QTY
        if reason == "MIN_ORDER_QTY":
            br = _classify_block(r)
            block_reason_all[br] += 1
            if now - rt <= DAY:
                block_reason_24h[br] += 1

    n_proposals = len(proposals)
    n_filled = len(opened)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"last_24h": True, "all_time": True},
        "proposals_total": n_proposals,
        "filled_total": n_filled,
        "fill_rate_pct": round(n_filled / n_proposals * 100, 1) if n_proposals else 0.0,
        "cancelled_rate_pct": round((n_proposals - n_filled) / n_proposals * 100, 1) if n_proposals else 0.0,
        "proposals_24h": proposals_24h,
        "filled_24h": filled_24h,
        "timing": {
            "signal_to_proposal_s": _agg(sig2prop),
            "proposal_to_fill_s": _agg(prop2fill),
            "proposal_to_fill_s_24h": _agg(prop2fill_24h),
            "fill_to_exit_hours": _agg(holding),
        },
        "rejection_reasons_all": dict(reasons_all.most_common()),
        "rejection_reasons_24h": dict(reasons_24h.most_common()),
        "order_block_reason_all": dict(block_reason_all.most_common()),
        "order_block_reason_24h": dict(block_reason_24h.most_common()),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False))

    print("\n=== EXECUTION DIAGNOSTICS ===")
    print(f"proposals: {n_proposals} (24h: {proposals_24h}) | filled: {n_filled} (24h: {filled_24h})")
    print(f"fill_rate: {report['fill_rate_pct']}% | cancelled: {report['cancelled_rate_pct']}%")
    t = report["timing"]
    print(f"signal→proposal: {t['signal_to_proposal_s']}")
    print(f"proposal→fill:   {t['proposal_to_fill_s']}  (24h: {t['proposal_to_fill_s_24h']})")
    print(f"fill→exit:       {t['fill_to_exit_hours']}")
    print("rejections all:  ", report["rejection_reasons_all"])
    print("rejections 24h:  ", report["rejection_reasons_24h"])
    print("order_block all: ", report["order_block_reason_all"])
    print("order_block 24h: ", report["order_block_reason_24h"])
    print(f"\nReport: {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
