#!/usr/bin/env python3
"""
innovation_scan.py — сегмент innovation-монет Bybit: кто есть, funding, OI, ликвидность,
возраст листинга. READ-ONLY.

Для ответа: «на малоликвидных монетах есть всплески — можно ли на них заработать?»
Собирает по ВСЕМ linear-инструментам с symbolType=innovation:
  - turnover24h (потенциальная ёмкость)
  - fundingRate (текущая, 8ч)
  - openInterest
  - возраст (listTime от сегодня)
  - цена/изменение
"""
from __future__ import annotations

import json
import time
import httpx
from datetime import datetime, timezone
from pathlib import Path

OUT = Path("/root/tradingos/memory/research/innovation_segment")
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    with httpx.Client(timeout=20) as c:
        # 1. Все linear-инструменты
        r = c.get("https://api.bybit.com/v5/market/instruments-info",
                  params={"category": "linear", "limit": 1000})
        d = r.json()
        inst = (d.get("result") or {}).get("list") or []
        print(f"Всего linear-инструментов: {len(inst)}")

        innov = [i for i in inst if i.get("symbolType") == "innovation"]
        print(f"innovation: {len(innov)}")
        t = int(time.time() * 1000)

        # 2. Текущие тикеры для funding/OI/turnover
        rows = []
        for i in innov:
            sym = i["symbol"]
            try:
                r2 = c.get("https://api.bybit.com/v5/market/tickers",
                           params={"category": "linear", "symbol": sym})
                tk = (((r2.json().get("result") or {}).get("list") or [{}])[0])
                rows.append({
                    "symbol": sym,
                    "last": float(tk.get("lastPrice") or 0),
                    "turnover24h": float(tk.get("turnover24h") or 0),
                    "fundingRate_bps8h": float(tk.get("fundingRate") or 0) * 10000,
                    "openInterest": float(tk.get("openInterest") or 0),
                    "index": float(tk.get("indexPrice") or 0),
                })
            except Exception:
                continue

        rows.sort(key=lambda x: -x["turnover24h"])
        print(f"\nТоп-40 innovation по обороту 24h:")
        print(f"{'symbol':<12}{'turnover24h':>13}{'funding bps':>12}{'OI':>13}{'listAge':>10}")
        now = t
        for x in rows[:40]:
            sym = x["symbol"]
            lt = next((int(i.get("listTime") or 0) for i in innov if i["symbol"] == sym), 0)
            age_days = (now - lt) / 86400000 if lt else 0
            print(f"{sym:<12}{x['turnover24h']:>13,.0f}{x['fundingRate_bps8h']:>12.3f}"
                  f"{x['openInterest']:>13,.0f}{age_days:>9.1f}d")

        # 3. Монеты с большим funding (|f| ≥ 0.01% за 8ч = 100bps/8h? нет: 0.0001*10000=1bp)
        print("\nInnovation с |funding| ≥ 2 bps за 8ч (кандидаты funding-capture):")
        big_f = [x for x in rows if abs(x["fundingRate_bps8h"]) >= 2.0]
        big_f.sort(key=lambda x: -abs(x["fundingRate_bps8h"]))
        for x in big_f[:25]:
            lt = next((int(i.get("listTime") or 0) for i in innov if i["symbol"] == x["symbol"]), 0)
            age = (now - lt) / 86400000 if lt else 0
            print(f"{x['symbol']:<12}f={x['fundingRate_bps8h']:+.3f}bps turnover={x['turnover24h']:>11,.0f} age={age:.0f}d")

        # 4. Возраст: свежие листинги (последние 45 дней) с приличным оборотом
        print("\nСвежие листинги (≤45 дней) с turnover ≥ $100k:")
        fresh = []
        for i in innov:
            lt = int(i.get("listTime") or 0)
            age = (now - lt) / 86400000 if lt else 999
            if age <= 45:
                fresh.append(i)
        for i in sorted(fresh, key=lambda x: -int(x.get("listTime") or 0))[:25]:
            sym = i["symbol"]
            turn = next((x["turnover24h"] for x in rows if x["symbol"] == sym), 0)
            fund = next((x["fundingRate_bps8h"] for x in rows if x["symbol"] == sym), 0)
            age = (now - int(i.get("listTime") or 0)) / 86400000
            print(f"{sym:<12}age={age:>6.1f}d turnover={turn:>12,.0f} funding={fund:+.3f}bps")

        (OUT / "innovation_snapshot.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()