#!/usr/bin/env python3
"""
Trade Lifecycle — автоматический таймлайн каждой сделки.

После закрытия сделки (или пока она открыта — state=OPEN) сохраняет полный
жизненный цикл с таймштампами:

    Signal → Proposal → Execution → (Guardian) → Exit → Genome joined

Источники (только чтение):
  memory/funnel_events.jsonl    proposal/opened (decision_id, ts)
  memory/trade_genome.jsonl     OPEN-запись (entry features, expected_R)
  memory/signal_log.jsonl       первый сигнал (t_signal)
  logs/trades/trade_results.jsonl  закрытие (outcome, MFE/MAE, pnl)
  tradingos_data.db position_events — число событий guardian за время позиции

Выход: logs/trades/trade_lifecycle.jsonl (по строке на сделку, idempotent
по decision_id, файл переписывается целиком при каждом запуске).

Расписание: systemd timer (раз в 30 мин). НЕ трогает контур.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
FUNNEL = ROOT / "memory/funnel_events.jsonl"
GENOME = ROOT / "memory/trade_genome.jsonl"
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"
RESULTS = ROOT / "logs/trades/trade_results.jsonl"
LIFECYCLE = ROOT / "logs/trades/trade_lifecycle.jsonl"

ENTRY_TOLERANCE = 0.01
MAX_HOURS_OPEN = 14 * 24
SIGNAL_LOOKBACK = 300.0   # ищем первый сигнал в [proposal-300s, proposal+5s]


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


def load_signal_index(symbols: set[str]) -> dict:
    """(symbol, direction) -> sorted [(ts, prob)] — для поиска t_signal.

    Предфильтр по сырой строке (substring symbol) до json.loads — сканирование
    signal_log (сотни тыс. строк) без полного парсинга.
    """
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
                idx[(r["symbol"], d)].append((ts, r.get("final_probability") or 0))
    for k in idx:
        idx[k].sort()
    return idx


def find_signal_ts(idx: dict, symbol: str, direction: str, proposal_ts: float) -> float | None:
    arr = idx.get((symbol, direction))
    if not arr:
        return None
    # первый (самый ранний) с ts >= proposal_ts - lookback
    lo, hi = 0, len(arr) - 1
    target = proposal_ts - SIGNAL_LOOKBACK
    best = None
    for ts, _ in arr:
        if target <= ts <= proposal_ts + 5:
            best = ts
            break
    return best


def load_funnel_map() -> dict:
    """decision_id -> {proposal_ts, opened_ts, ...}"""
    out: dict[str, dict] = {}
    for ev in _load_jsonl(FUNNEL):
        did = ev.get("decision_id", "")
        if not did:
            continue
        rec = out.setdefault(did, {})
        if ev.get("event") == "proposal":
            rec["proposal_ts"] = ev.get("ts")
        elif ev.get("event") == "opened":
            rec["opened_ts"] = ev.get("ts")
            rec["ticket"] = ev.get("ticket")
            rec["fill_price"] = ev.get("price")
            for k in ("prob", "score", "quality", "adx", "atr", "rsi"):
                if ev.get(k) is not None:
                    rec[k] = ev[k]
    return out


def load_genome_map() -> dict:
    out = {}
    for g in _load_jsonl(GENOME):
        if g.get("event") != "OPEN":
            continue
        did = g.get("decision_id", "")
        if did:
            out[did] = g
    return out


def join_close(o: dict, closes: list[dict]) -> dict | None:
    entry = o.get("entry") or 0
    if entry <= 0:
        return None
    o_ts = o.get("ts_unix") or 0
    side = "BUY" if str(o.get("side", "")).upper() == "BUY" else "SELL"
    best_c, best_gap = None, None
    for c in closes:
        if c["symbol"] != o.get("symbol") or c["_side"] != side:
            continue
        if abs(c["entry"] - entry) / entry > ENTRY_TOLERANCE:
            continue
        if c["_ts"] <= o_ts or c["_ts"] - o_ts > MAX_HOURS_OPEN * 3600:
            continue
        gap = c["_ts"] - o_ts
        if best_c is None or gap < best_gap:
            best_c, best_gap = c, gap
    return best_c


def main() -> int:
    funnel_map = load_funnel_map()
    genome_map = load_genome_map()
    closes_raw = _load_jsonl(RESULTS)
    closes = []
    for c in closes_raw:
        if c.get("status") != "CLOSED":
            continue
        try:
            c["_ts"] = datetime.fromisoformat(c["timestamp"]).timestamp()
        except Exception:
            continue
        c["_side"] = "BUY" if str(c.get("side", "")).lower() == "buy" else "SELL"
        closes.append(c)

    relevant_symbols = {g.get("symbol") for g in genome_map.values()}
    sig_idx = load_signal_index(relevant_symbols)

    existing: dict[str, dict] = {}
    for line in _load_jsonl(LIFECYCLE):
        if line.get("decision_id"):
            existing[line["decision_id"]] = line

    # процесс в порядке открытия
    opens = sorted(genome_map.values(), key=lambda g: g.get("ts_unix") or 0)
    rows: list[dict] = []
    for n, o in enumerate(opens, start=1):
        did = o["decision_id"]
        f = funnel_map.get(did, {})
        proposal_ts = f.get("proposal_ts")
        exec_ts = f.get("opened_ts") or o.get("ts_unix")
        close = join_close(o, closes)

        t_signal = None
        if proposal_ts:
            t_signal = find_signal_ts(sig_idx, o["symbol"], o["side"], proposal_ts)

        rec = {
            "trade_number": n,
            "decision_id": did,
            "symbol": o["symbol"],
            "side": o["side"],
            "state": "CLOSED" if close else "OPEN",
            "t_signal": datetime.fromtimestamp(t_signal, tz=timezone.utc).isoformat() if t_signal else None,
            "t_proposal": datetime.fromtimestamp(proposal_ts, tz=timezone.utc).isoformat() if proposal_ts else None,
            "t_execution": datetime.fromtimestamp(exec_ts, tz=timezone.utc).isoformat() if exec_ts else None,
            "t_exit": close["timestamp"] if close else None,
            "dt_signal_to_proposal_s": round(proposal_ts - t_signal, 1) if (proposal_ts and t_signal) else None,
            "dt_proposal_to_execution_s": round(exec_ts - proposal_ts, 1) if (exec_ts and proposal_ts) else None,
            "holding_hours": round((close["_ts"] - exec_ts) / 3600, 2) if close else None,
            "entry": o.get("entry"),
            "sl": o.get("sl"),
            "tp": o.get("tp"),
            "fill_price": f.get("fill_price") or o.get("fill_price"),
            "ticket": f.get("ticket") or o.get("ticket"),
            "prob": f.get("prob") or o.get("prob"),
            "score": f.get("score") or o.get("score"),
            "quality": f.get("quality") or o.get("quality"),
            "adx": f.get("adx") or o.get("adx"),
            "atr": f.get("atr") or o.get("atr"),
            "rsi": f.get("rsi") or o.get("rsi"),
            "expected_R": o.get("expected_R"),
            "realized_R": None,
            "bias_R": None,
            "outcome": None,
            "net_pnl": None,
            "mfe_peak_r": None,
            "mae_trough_r": None,
            "exit_price": None,
            "guardian_trigger": None,
            "be_fired": None,
            "partial_fired": None,
            "tight_fired": None,
            "protected_exit": None,
        }
        if close:
            entry = o.get("entry") or 0
            sl = o.get("sl") or 0
            size = close.get("size") or 0
            risk = abs(entry - sl) * size if (entry and sl and size) else 0
            r_r = (close.get("net_pnl") or 0) / risk if risk > 0 else None
            rec.update({
                "realized_R": round(r_r, 3) if r_r is not None else None,
                "bias_R": round(r_r - (o.get("expected_R") or 0), 3) if r_r is not None else None,
                "outcome": close.get("outcome"),
                "net_pnl": close.get("net_pnl"),
                "mfe_peak_r": close.get("mfe_peak_r"),
                "mae_trough_r": close.get("mae_trough_r"),
                "exit_price": close.get("close_price"),
                # guardian summary приходит прямо из close-записи reality-guardian
                "guardian_trigger": close.get("guardian_trigger"),
                "be_fired": close.get("be_fired"),
                "partial_fired": close.get("partial_fired"),
                "tight_fired": close.get("tight_fired"),
                "protected_exit": close.get("protected_exit"),
            })
        rows.append(rec)

    LIFECYCLE.parent.mkdir(parents=True, exist_ok=True)
    with LIFECYCLE.open("w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"[trade_lifecycle] trades={len(rows)} "
          f"closed={sum(1 for r in rows if r['state']=='CLOSED')} "
          f"open={sum(1 for r in rows if r['state']=='OPEN')}")
    for r in rows:
        print(f"  #{r['trade_number']} {r['symbol']:<12} {r['side']:<4} {r['state']:<7} "
              f"sig→prop {r['dt_signal_to_proposal_s']}s  prop→exec {r['dt_proposal_to_execution_s']}s  "
              f"outcome={r['outcome']}  bias_R={r['bias_R']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
