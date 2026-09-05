#!/usr/bin/env python3
"""
shadow_engine.py — SHADOW-ONLY runner для Trade Engine v2.

Читает поток signal_log.jsonl (хвост), для каждого сильного кандидата
(prob>=0.55) считает NEW-решение (engine_v2.decide) и логирует
CURRENT vs NEW + TradePlan в shadow_engine.jsonl.

НИЧЕГО не исполняет. НЕ меняет AUTO/executor/guardian/risk.
Параллельный журнал для последующего сравнения.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
SIGNAL_LOG = ROOT / "memory/signal_log.jsonl"
SHADOW_LOG = ROOT / "memory/shadow_engine.jsonl"
CACHE = ROOT / "replay_cache"

# Последний обработанный оффсет (для tail)
_last_offset = 0


def strong(candidate: dict) -> bool:
    return (candidate.get("direction") in ("BUY", "SELL")
            and (candidate.get("final_probability") or 0) >= 0.55)


def process_line(line: str, cache: dict) -> dict | None:
    global _last_offset
    try:
        cand = json.loads(line)
    except Exception:
        return None
    if not strong(cand):
        return None
    symbol = cand["symbol"]
    ts = datetime.fromisoformat(cand["timestamp"].replace("Z", "+00:00")).timestamp()
    # P1-fix: свежий data path — кэш stale/нет → живой Bybit API. NO_DATA только
    # если источник реально недоступен.
    if symbol not in cache or cache.get(symbol) is None:
        from trade.entry_quality_gate import load_candles as eq_load
        _df, _meta = eq_load(symbol)
        cache[symbol] = _df
    from trade.engine_v2 import decide
    decision = decide({
        "symbol": symbol, "side": cand["direction"], "ts": ts,
    }, cache[symbol])
    # ENTRY QUALITY GATE v1 (read-only, shadow): категорический запрет плохих входов
    from trade.entry_quality_gate import gate
    _meta = {"source": "shadow_cache"}
    gate_result = gate(symbol, cand["direction"], ts, cache[symbol], log=False, meta=_meta)
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "signal_ts": cand["timestamp"],
        "symbol": symbol,
        "signal_direction": cand["direction"],
        "signal_prob": cand.get("final_probability"),
        "signal_score": cand.get("score"),
        "current": "OPEN",  # AUTO по-прежнему открывает; сравнение в анализе
        "new": decision,
        "gate": gate_result,
    }
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="обработать текущий лог один раз и выйти")
    args = ap.parse_args()

    cache: dict = {}
    SHADOW_LOG.parent.mkdir(parents=True, exist_ok=True)
    processed = 0

    def scan():
        nonlocal processed
        lines = SIGNAL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        global _last_offset
        batch = lines[_last_offset:] if _last_offset else lines[-8000:]
        for line in batch:
            rec = process_line(line, cache)
            if rec:
                with SHADOW_LOG.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                processed += 1
        _last_offset = len(lines)

    scan()
    print(f"Shadow: обработано {processed} кандидатов, журнал: {SHADOW_LOG}", flush=True)
    if args.once:
        return 0

    # tail-режим
    while True:
        time.sleep(30)
        scan()
        print(f"[{datetime.now(timezone.utc):%H:%M:%S}] processed {processed} total", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
