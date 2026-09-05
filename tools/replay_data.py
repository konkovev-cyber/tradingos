#!/usr/bin/env python3
"""
replay_data.py — PHASE A: preload данных в persistent cache (parquet).

Принцип: HTTP вызывается ТОЛЬКО здесь (вне цикла сигналов). После preload
replay-движок работает строго на локальных данных.

Формат кэша:
  replay_cache/{SYM}/{TF}.parquet
  replay_cache/manifest.json  → { "SYM_TF": {"start","end","rows","status","fetched_at"} }

Запуск:
  python3 tools/replay_data.py [--symbols a,b,c] [--force]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"
MANIFEST = CACHE_DIR / "manifest.json"

# Bybit kline: limit max 1000 на запрос; 15m баров за ~10 дней ≈ 960 — один запрос
# Берём с запасом: 12 дней до первого сигнала (28.07) → старт 16.07
START = "2026-07-16T00:00:00Z"
END = "2026-08-09T00:00:00Z"
TIMEFRAMES = {"M15": "15"}

MAX_WORKERS = 8  # bounded concurrency
RATE_LIMIT_SLEEP = 0.15  # ~6-7 rps между батчами


def _load_manifest() -> dict:
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except Exception:
            return {}
    return {}


def _save_manifest(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, indent=2, ensure_ascii=False))


def fetch_kline(client: httpx.Client, symbol: str, interval: str,
                start_ms: int, end_ms: int) -> list[dict]:
    """Fetch kline в сыром виде (новые сверху), возвращает хронологический список."""
    all_rows = []
    cursor = None
    while True:
        params = {"category": "linear", "symbol": symbol, "interval": interval,
                  "start": start_ms, "end": end_ms, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        r = client.get("https://api.bybit.com/v5/market/kline",
                       params=params, timeout=20)
        d = r.json()
        if d.get("retCode") != 0:
            raise RuntimeError(f"{symbol}: retCode {d.get('retCode')} {d.get('retMsg')}")
        rows = d.get("result", {}).get("list", [])
        if not rows:
            break
        all_rows.extend(rows)
        cursor = d.get("result", {}).get("nextPageCursor")
        if not cursor:
            break
    # по одному запросу достаточно для 12 дней 15m (~1200 баров, 2 страницы)
    out = []
    for b in all_rows:
        ts = int(b[0])
        out.append({"ts": ts, "o": float(b[1]), "h": float(b[2]),
                    "l": float(b[3]), "c": float(b[4]), "v": float(b[5])})
    out.sort(key=lambda x: x["ts"])
    # дедупликация по ts (Bybit иногда отдаёт дубликаты на границах страниц)
    seen = set()
    dedup = []
    for b in out:
        if b["ts"] not in seen:
            seen.add(b["ts"])
            dedup.append(b)
    return dedup


def build_cache(symbols: list[str], force: bool = False) -> dict:
    manifest = {} if force else _load_manifest()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    start_ms = int(datetime.fromisoformat(START.replace("Z", "+00:00")).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(END.replace("Z", "+00:00")).timestamp() * 1000)

    def fetch_one(sym: str) -> tuple[str, str, str | None]:
        key = f"{sym}_M15"
        if not force and manifest.get(key, {}).get("status") == "READY":
            return (sym, "SKIP_CACHED", None)
        try:
            with httpx.Client(timeout=25) as client:
                rows = fetch_kline(client, sym, "15", start_ms, end_ms)
            if len(rows) < 50:
                manifest[key] = {"status": "EMPTY", "rows": len(rows),
                                 "fetched_at": datetime.now(timezone.utc).isoformat()}
                return (sym, "EMPTY", None)
            df = pd.DataFrame(rows)
            path = CACHE_DIR / f"{sym}_M15.parquet"
            df.to_parquet(path, index=False)
            manifest[key] = {"status": "READY", "rows": len(df),
                             "start": int(df["ts"].iloc[0]), "end": int(df["ts"].iloc[-1]),
                             "fetched_at": datetime.now(timezone.utc).isoformat()}
            return (sym, "OK", str(len(df)))
        except Exception as e:
            manifest[key] = {"status": "ERROR", "error": str(e)[:120],
                             "fetched_at": datetime.now(timezone.utc).isoformat()}
            return (sym, "ERROR", str(e)[:80])

    results = {"ok": 0, "cached": 0, "empty": 0, "error": 0}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = [ex.submit(fetch_one, s) for s in symbols]
        done = 0
        for fut in as_completed(futs):
            sym, status, extra = fut.result()
            results[status.lower()] = results.get(status.lower(), 0) + 1
            done += 1
            if done % 10 == 0 or status == "ERROR":
                print(f"  [{done}/{len(symbols)}] {sym}: {status} {extra or ''}", flush=True)
            time.sleep(RATE_LIMIT_SLEEP)

    _save_manifest(manifest)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=str, default="",
                    help="csv список символов; пусто = все из signal_log")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        rows = [json.loads(l) for l in (ROOT / "memory/signal_log.jsonl").open()]
        strong = [r for r in rows if r.get("direction") in ("BUY", "SELL")
                  and (r.get("final_probability") or 0) >= 0.55]
        symbols = sorted({r["symbol"] for r in strong})

    print(f"Preload: {len(symbols)} символов, {START} → {END}, tf=M15")
    res = build_cache(symbols, force=args.force)
    print(f"\nCACHE BUILD: OK={res.get('ok',0)} CACHED={res.get('cached',0)} "
          f"EMPTY={res.get('empty',0)} ERROR={res.get('error',0)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
