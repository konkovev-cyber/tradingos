#!/usr/bin/env python3
"""CRYPTO Entry Funnel counter (observation-only, R148 read-only).

Считает воронку AUTO-контура из journald reality + журналов:
    raw candidates → REALITY CANDIDATE → ADX SKIP / cooldown SKIP →
    REALITY RANK (executable) → entry_gate ALLOW → PROPOSAL → CORRELATION → OPEN

Ничего не меняет. Источники:
  - journalctl -u tradingos-reality (строки REALITY CANDIDATE / SKIP / RANK / PROPOSAL)
  - memory/entry_gate.jsonl (SKIP/ALLOW по символам)
  - memory/funnel_events.jsonl (proposal/opened)
  - memory/trade_genome.jsonl (OPEN)

Запуск:
  python3 /root/tradingos/research/exit_shadow/crypto_funnel.py [часы_окна] [--since "2026-08-17 14:00"]
"""
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

ROOT = "/root/tradingos"
HOURS = 24
SINCE = None

for a in sys.argv[1:]:
    if a.startswith("--since"):
        SINCE = a.split("=", 1)[1]
    elif a.isdigit():
        HOURS = int(a)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def journal_lines(since: str) -> list[str]:
    r = subprocess.run(
        ["journalctl", "-u", "tradingos-reality", "--since", since, "--no-pager"],
        capture_output=True, text=True, timeout=90, errors="replace",
    )
    return r.stdout.splitlines()


CAND_RE = re.compile(r"REALITY CANDIDATE: (\w+USDT) (BUY|SELL)")
SKIP_RE = re.compile(r"REALITY SKIP: (\w+USDT) (.*)")
RANK_RE = re.compile(r"REALITY RANK: selected (\w+USDT) (BUY|SELL)")
PROP_RE = re.compile(r"REALITY PROPOSAL: (\w+USDT) (BUY|SELL)")

ADX_RE = re.compile(r"ADX=([\d.]+) < 20")
COOLDOWN_RE = re.compile(r"cooldown ([\d.]+)s remaining")


def main() -> int:
    since = SINCE or (datetime.now(timezone.utc) - timedelta(hours=HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    lines = journal_lines(since)
    print(f"window: {since} → {now_iso()}Z  (journal lines: {len(lines)})\n")

    cand_syms = set()
    adx_skip = 0
    cooldown_skip = 0
    other_skip = 0
    skip_reasons = Counter()
    rank_syms = set()
    prop_syms = set()
    per_sym = defaultdict(lambda: {"cand": 0, "skip": 0, "rank": 0})

    for ln in lines:
        m = CAND_RE.search(ln)
        if m:
            sym = m.group(1)
            cand_syms.add(sym)
            per_sym[sym]["cand"] += 1
            continue
        m = SKIP_RE.search(ln)
        if m:
            sym, reason = m.group(1), m.group(2)
            skip_reasons[reason] += 1
            per_sym[sym]["skip"] += 1
            if ADX_RE.search(reason):
                adx_skip += 1
            elif COOLDOWN_RE.search(reason):
                cooldown_skip += 1
            else:
                other_skip += 1
            continue
        m = RANK_RE.search(ln)
        if m:
            rank_syms.add(m.group(1))
            per_sym[m.group(1)]["rank"] += 1

    # entry_gate
    gate_allow = 0
    gate_skip = 0
    gate_skip_reasons = Counter()
    try:
        for line in open(f"{ROOT}/memory/entry_gate.jsonl"):
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            ts = e.get("timestamp", 0)
            if ts < (datetime.now(timezone.utc) - timedelta(hours=HOURS)).timestamp():
                continue
            if e.get("decision") == "ALLOW":
                gate_allow += 1
            else:
                gate_skip += 1
                gate_skip_reasons[e.get("reason", "?")] += 1
    except FileNotFoundError:
        pass

    # funnel proposals + opened
    proposals = 0
    opened = 0
    try:
        for line in open(f"{ROOT}/memory/funnel_events.jsonl"):
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e.get("ts", 0) < (datetime.now(timezone.utc) - timedelta(hours=HOURS)).timestamp():
                continue
            if e.get("event") == "proposal":
                proposals += 1
            elif e.get("event") == "opened":
                opened += 1
    except FileNotFoundError:
        pass
    # OPEN из genome (кросс-проверка)
    try:
        for line in open(f"{ROOT}/memory/trade_genome.jsonl"):
            line = line.strip()
            if not line:
                continue
            g = json.loads(line)
            if g.get("event") == "OPEN" and g.get("ts_unix", 0) >= (datetime.now(timezone.utc) - timedelta(hours=HOURS)).timestamp():
                opened = max(opened, 1)  # флаг наличия
    except FileNotFoundError:
        pass

    # correlation: proposals появились, но opened == 0 → где-то потеря
    print("=== CRYPTO ENTRY FUNNEL (unique symbols, observation-only) ===")
    print(f"  raw candidates (unique syms): {len(cand_syms)}")
    print(f"  REALITY RANK (executable):     {len(rank_syms)}  (доля от кандидатов: "
          f"{len(rank_syms)/len(cand_syms)*100:.0f}% если cand_syms)")
    print(f"\n=== SKIP причины (journal) ===")
    print(f"  ADX < 20 (no trend):   {adx_skip}")
    print(f"  cooldown:              {cooldown_skip}")
    print(f"  другие:                {other_skip}")
    for r, c in skip_reasons.most_common(8):
        print(f"    - {c:4d}  {r}")

    print(f"\n=== entry_gate (за окно) ===")
    print(f"  ALLOW: {gate_allow}   SKIP: {gate_skip}")
    for r, c in gate_skip_reasons.most_common(5):
        print(f"    - {c:4d}  {r}")

    print(f"\n=== executor ===")
    print(f"  PROPOSAL (funnel): {proposals}")
    print(f"  OPEN:              {opened}")

    # главный вопрос: % потерь на ADX
    total_skip = adx_skip + cooldown_skip + other_skip
    if total_skip:
        print(f"\n=== ADX FOCUS ===")
        print(f"  ADX-потери: {adx_skip}/{total_skip} скипов ({adx_skip/total_skip*100:.0f}%)")
    if len(cand_syms):
        print(f"  % кандидатов, умерших на ADX (уник. символы): "
              f"{sum(1 for s in per_sym if per_sym[s]['skip']>0 and per_sym[s]['rank']==0)/len(cand_syms)*100:.0f}% "
              f"({sum(1 for s in per_sym if per_sym[s]['skip']>0 and per_sym[s]['rank']==0)}/{len(cand_syms)})")
    print("\nвердикт-маркер: "
          f"{'PROPOSALS_PRESENT_OPEN_ZERO' if proposals and not opened else ('NO_PROPOSALS_YET' if not proposals else 'FLOW_OK')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
