#!/usr/bin/env python3
"""
entry_quality.py — Entry-quality research V1 (T4).

Гипотеза (owner 2026-08-29):
    Даже если направление почти непредсказуемо, заранее выбранная LIMIT-зона
    может улучшать экономику сделки за счёт лучшей цены входа.

Протокол (строго counterfactual, НИЧЕГО не торгуем):
  - Кандидаты: M15-бары со scanner-like условиями (направление строгое +
    momentum + дистанция до EMA20), reference = close бара сигнала.
  - Зоны фиксированы ЗАРАНЕЕ, без look-ahead:
      LONG:  ref*(1 - 0.0010/0.0020/0.0030/0.0050)  (+0.00 как MARKET)
      SHORT: ref*(1 + 0.0010/0.0020/0.0030/0.0050)
      ATR-нормированные: 0.25/0.50/0.75/1.00 × ATR14(M15)
  - touch = low ≤ limit (LONG) / high ≥ limit (SHORT) в окне i+1..i+16 (4ч).
  - Внутрибаровое исполнение неизвестно → консервативно: forward считаем
    от ЗАКРЫТИЯ бара касания (занижает преимущество лимитки, честно).
  - Горизонты: 15м / 1ч / 2ч / 4ч (M15-разрешение; секунды — DATA GAP).
  - Экономика: maker 0.02%×2 (лимит), taker 0.055%×2 (маркет) + cost stress ×1.5/×2.
  - Отдельно: price-improvement (лучшая цена) vs selection-bias (коснулись
    только когда рынок шёл против) — NET/all по всем кандидатам.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("/root/tradingos")
CACHE_DIR = ROOT / "replay_cache"
OUT = ROOT / "memory" / "research" / "entry_quality"
OUT.mkdir(parents=True, exist_ok=True)

MAKER_FEE = 0.0002
TAKER_FEE = 0.00055

# Фиксированные зоны дистанции (доля от reference)
PCT_ZONES = (0.0010, 0.0020, 0.0030, 0.0050)
ATR_ZONES = (0.25, 0.50, 0.75, 1.00)

# Горизонты в M15-барах: 1=15м, 4=1ч, 8=2ч, 16=4ч
HORIZONS = (1, 4, 8, 16)


def _rsi_wilder(closes, n=14):
    s = pd.Series(closes, dtype=float)
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_g = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_l = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_g / avg_l.replace(0.0, float("nan"))
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _atr14(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["h"], df["l"], df["c"]
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(14).mean()


def build(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema20"] = d["c"].ewm(span=20, adjust=False).mean()
    d["ema50"] = d["c"].ewm(span=50, adjust=False).mean()
    d["rsi"] = _rsi_wilder(d["c"].tolist())
    d["dist_e20_pct"] = (d["c"] - d["ema20"]) / d["ema20"] * 100
    d["atr"] = _atr14(d)
    d["up"] = (d["c"] > d["ema20"]) & (d["ema20"] > d["ema50"])
    d["down"] = (d["c"] < d["ema20"]) & (d["ema20"] < d["ema50"])
    d["mom_up"] = d["rsi"] > 50
    # scanner-like: строгое направление + momentum согласован + дистанция ≤0.35%
    d["cand_long"] = d["up"] & d["mom_up"] & (d["dist_e20_pct"].abs() <= 0.35)
    d["cand_short"] = d["down"] & (~d["mom_up"]) & (d["dist_e20_pct"].abs() <= 0.35)
    return d


def analyze(data: pd.DataFrame) -> list[dict]:
    """Основной анализ: для каждого кандидата и каждой зоны — touch / forward."""
    n = len(data)
    c = data["c"].values
    l = data["l"].values
    h = data["h"].values
    ts = data["ts"].values
    sym = data["symbol"].values
    atr_v = data["atr"].values
    cand_long = data["cand_long"].values
    cand_short = data["cand_short"].values
    rows: list[dict] = []
    for pos in range(n):
        ref = c[pos]
        atr = float(atr_v[pos]) if atr_v[pos] == atr_v[pos] else 0.0  # nan→0
        side = None
        if cand_long[pos]:
            side = "LONG"
        elif cand_short[pos]:
            side = "SHORT"
        if side is None:
            continue
        if pos + 1 > min(pos + 16, n - 1):
            continue

        zones = {}
        if side == "LONG":
            zones["market"] = ref
            for d_pct in PCT_ZONES:
                zones[f"pct_{d_pct:.4f}"] = ref * (1 - d_pct)
            for a_in in ATR_ZONES:
                if atr > 0:
                    zones[f"atr_{a_in:.2f}"] = ref - atr * a_in
        else:
            zones["market"] = ref
            for d_pct in PCT_ZONES:
                zones[f"pct_{d_pct:.4f}"] = ref * (1 + d_pct)
            for a_in in ATR_ZONES:
                if atr > 0:
                    zones[f"atr_{a_in:.2f}"] = ref + atr * a_in

        win_hi = min(pos + 16, n - 1)
        for zname, zpx in zones.items():
            touch = False
            t2t = None
            fpos = None
            if zname != "market":
                for j in range(pos + 1, win_hi + 1):
                    if side == "LONG" and l[j] <= zpx:
                        touch, t2t, fpos = True, j - pos, j
                        break
                    if side == "SHORT" and h[j] >= zpx:
                        touch, t2t, fpos = True, j - pos, j
                        break
            market_fwd = {}
            for hz2 in HORIZONS:
                if pos + hz2 < n:
                    market_fwd[hz2] = c[pos + hz2] / ref - 1.0
                else:
                    market_fwd[hz2] = None
            touch_fwd = {}
            if touch and fpos is not None:
                for hz2 in HORIZONS:
                    if fpos + hz2 < n:
                        touch_fwd[hz2] = c[fpos + hz2] / zpx - 1.0
                    else:
                        touch_fwd[hz2] = None
            rows.append({
                "symbol": sym[pos],
                "ts": ts[pos],
                "side": side,
                "ref": ref,
                "atr": atr,
                "zone": zname,
                "zpx": zpx,
                "touch": touch,
                "t2t": t2t,
                "market_fwd": market_fwd,
                "touch_fwd": touch_fwd,
            })
    return rows


def net(pnl_gross: float, notional: float, fee: float) -> float:
    return pnl_gross - notional * 2 * fee


def _fwd_get(d: dict, hz: int):
    """Взять forward из dict (ключи могут быть int или str)."""
    if not d:
        return None
    return d.get(hz) if d.get(hz) is not None else d.get(str(hz))


def _pnl_zone(r: dict, hz: int, fee_mult: float, use_touch_fwd: bool) -> float | None:
    """gross-NET сделки по зоне: market → от ref (taker); limit → от zpx (maker).
    PnL нормализуется по стороне (SHORT инвертирован). Возвращает None если
    forward недоступен или |fwd| > 0.5 (артефакт данных)."""
    is_long = r["side"] == "LONG"
    if use_touch_fwd and r["zone"] != "market":
        fwd = _fwd_get(r.get("touch_fwd") or {}, hz)
        if fwd is None:
            return None
        fwd = float(fwd)
        if abs(fwd) > 0.5:
            return None
        sgn = 1.0 if is_long else -1.0
        return sgn * fwd - 2 * MAKER_FEE * fee_mult
    fwd = _fwd_get(r.get("market_fwd") or {}, hz)
    if fwd is None:
        return None
    fwd = float(fwd)
    if abs(fwd) > 0.5:
        return None
    if r["zone"] == "market":
        sgn = 1.0 if is_long else -1.0
        return sgn * fwd - 2 * TAKER_FEE * fee_mult
    if not r["touch"]:
        return None
    sgn = 1.0 if is_long else -1.0
    return sgn * fwd - 2 * MAKER_FEE * fee_mult


def summarize(rows: list[dict], fee_mult: float = 1.0) -> str:
    """Агрегировать rows в отчёт."""
    if not rows:
        return "no candidates"
    df = pd.DataFrame(rows)

    hz = 4  # 1ч горизонт для основной метрики
    lines = [
        f"{'='*90}",
        "ENTRY-QUALITY V1 — aggregated",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Candidates: {len(df)} | symbols: {df['symbol'].nunique()}",
        f"Cost stress: fees ×{fee_mult} ({MAKER_FEE*2*fee_mult*100:.3f}% / {TAKER_FEE*2*fee_mult*100:.3f}% round-trip)",
        "=" * 90,
    ]

    # 1. Touch-depth curve (по % зонам)
    lines.append("\n1. TOUCH-DEPTH CURVE (1h horizon, LONG+SHORT)")
    lines.append(f"{'zone':<14}{'n':>7}{'touch%':>8}{'NET/all':>11}{'NET/touch':>12}{'imp%':>8}")
    for zname in ["market"] + [f"pct_{d:.4f}" for d in PCT_ZONES]:
        sub = df[df["zone"] == zname]
        n = len(sub)
        if n == 0:
            continue
        touch_n = int(sub["touch"].sum())
        touch_pct = touch_n / n * 100
        all_pnls = []
        touch_pnls = []
        for r in sub.itertuples():
            rr = r._asdict()
            # market: всегда
            if zname == "market":
                p = _pnl_zone(rr, hz, fee_mult, use_touch_fwd=False)
                if p is not None:
                    all_pnls.append(p)
            else:
                if rr["touch"]:
                    p = _pnl_zone(rr, hz, fee_mult, use_touch_fwd=True)
                    if p is not None:
                        all_pnls.append(p)
                        touch_pnls.append(p)
                else:
                    all_pnls.append(0.0)  # нет позиции
        net_all = sum(all_pnls) / max(len(all_pnls), 1) * 100
        net_touch = sum(touch_pnls) / max(len(touch_pnls), 1) * 100
        # price improvement: mean (ref - zpx)/ref * 100 среди touch
        imp = 0.0
        if zname != "market":
            imps = [(rr["ref"] - rr["zpx"]) / rr["ref"] * 100
                    for rr in (r._asdict() for r in sub.itertuples()) if rr["touch"]]
            if imps:
                imp = sum(imps) / len(imps)
        lines.append(f"{zname:<14}{n:>7}{touch_pct:>7.1f}%{net_all:>10.4f}%{net_touch:>11.4f}%{imp:>7.3f}%")

    lines.append("\n1b. NET BY HORIZON (15m/1h/2h/4h, market vs L0.20%)")
    lines.append(f"{'variant':<14}{'h15m':>10}{'h1h':>10}{'h2h':>10}{'h4h':>10}")
    for zname in ["market", "pct_0.0020"]:
        sub = df[df["zone"] == zname]
        row_vals = []
        for hz2 in HORIZONS:
            pnls = []
            for r in sub.itertuples():
                rr = r._asdict()
                if zname == "market":
                    p = _pnl_zone(rr, hz2, fee_mult, use_touch_fwd=False)
                    if p is not None:
                        pnls.append(p)
                else:
                    if rr["touch"]:
                        p = _pnl_zone(rr, hz2, fee_mult, use_touch_fwd=True)
                        if p is not None:
                            pnls.append(p)
                    else:
                        pnls.append(0.0)
            row_vals.append(sum(pnls) / max(len(pnls), 1) * 100 if pnls else float("nan"))
        lines.append(f"{zname:<14}" + "".join(f"{v:>10.4f}%" for v in row_vals))

    # 2. ATR-нормированные
    lines.append("\n2. ATR-NORMALIZED (1h horizon)")
    lines.append(f"{'zone':<14}{'n':>7}{'touch%':>8}{'NET/all':>11}{'NET/touch':>12}")
    for zname2 in [f"atr_{a:.2f}" for a in ATR_ZONES]:
        sub = df[df["zone"] == zname2]
        n = len(sub)
        if n == 0:
            continue
        touch_n = int(sub["touch"].sum())
        touch_pct = touch_n / n * 100
        all_pnls, touch_pnls = [], []
        for r in sub.itertuples():
            rr = r._asdict()
            if rr["touch"]:
                p = _pnl_zone(rr, hz, fee_mult, use_touch_fwd=True)
                if p is not None:
                    all_pnls.append(p)
                    touch_pnls.append(p)
            else:
                all_pnls.append(0.0)
        net_all = sum(all_pnls) / max(len(all_pnls), 1) * 100
        net_touch = sum(touch_pnls) / max(len(touch_pnls), 1) * 100
        lines.append(f"{zname2:<14}{n:>7}{touch_pct:>7.1f}%{net_all:>10.4f}%{net_touch:>11.4f}%")

    # 3. Missed opportunity: non-touch (L0.20%)
    sub = df[df["zone"] == "pct_0.0020"]
    non = sub[~sub["touch"]]
    if len(non):
        missed = []
        for _, row_n in non.iterrows():
            rr = row_n.to_dict()
            p = _pnl_zone(rr, hz, fee_mult, use_touch_fwd=False)  # market-гипотетика
            if p is not None:
                missed.append(p)
        if missed:
            mean = sum(missed) / len(missed) * 100
            wr = sum(1 for m in missed if m > 0) / len(missed) * 100
            lines.append(f"\n3. MISSED OPPORTUNITY (L0.20% non-touch, 1h): n={len(missed)}")
            lines.append(f"   NET if market-entered (side-adjusted): {mean:+.4f}% | WR {wr:.0f}%")

    # 4. LONG vs SHORT (L0.20%)
    lines.append("\n4. LONG vs SHORT (zone L0.20%, 1h)")
    for side in ("LONG", "SHORT"):
        sub2 = df[(df["zone"] == "pct_0.0020") & (df["side"] == side)]
        if not len(sub2):
            continue
        touch_n = int(sub2["touch"].sum())
        all_pnls, touch_pnls = [], []
        for r in sub2.itertuples():
            rr = r._asdict()
            if rr["touch"]:
                p = _pnl_zone(rr, hz, fee_mult, use_touch_fwd=True)
                if p is not None:
                    all_pnls.append(p)
                    touch_pnls.append(p)
            else:
                all_pnls.append(0.0)
        net_all = sum(all_pnls) / max(len(all_pnls), 1) * 100
        net_touch = sum(touch_pnls) / max(len(touch_pnls), 1) * 100
        lines.append(f"   {side}: n={len(sub2)} touch={touch_n} ({touch_n/len(sub2)*100:.0f}%) "
                     f"NET/all={net_all:+.4f}% NET/touch={net_touch:+.4f}%")

    return "\n".join(lines)


def main() -> None:
    frames = []
    for cache in sorted(CACHE_DIR.glob("*USDT_M15full.parquet")):
        sym = cache.name.replace("_M15full.parquet", "")
        df = pd.read_parquet(cache)
        if len(df) < 4000:
            continue
        d = build(df)
        d["symbol"] = sym
        frames.append(d)
    if not frames:
        print("Нет M15full кэша — сначала запусти m15_edge.py")
        sys.exit(1)
    data = pd.concat(frames, ignore_index=True)
    print(f"Загружено: {len(data)} баров M15, {data['symbol'].nunique()} символов")

    rows = analyze(data)
    print(f"Кандидатов (scanner-like): {len(set((r['ts'], r['symbol']) for r in rows))}")
    if len(rows) < 500:
        print(f"⚠️ Кандидатов < 500: INSUFFICIENT SAMPLE ({len(rows)})")

    # Сохранить сырые данные (JSONL: dict-ключи int)
    OUT.mkdir(parents=True, exist_ok=True)
    import json as _json
    with (OUT / "raw_rows.jsonl").open("w") as _f:
        for r in rows:
            r2 = dict(r)
            r2["market_fwd"] = {str(k): v for k, v in r["market_fwd"].items()}
            r2["touch_fwd"] = {str(k): v for k, v in (r["touch_fwd"] or {}).items()}
            _f.write(_json.dumps(r2, ensure_ascii=False, default=str) + "\n")

    for mult in (1.0, 1.5, 2.0):
        print(summarize(rows, mult))
        if mult == 1.0:
            print("\n" + "=" * 90)
        else:
            print("\n" + "=" * 90)


if __name__ == "__main__":
    main()