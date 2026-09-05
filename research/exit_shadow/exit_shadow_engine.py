#!/usr/bin/env python3
"""
Exit-Shadow Engine — live shadow профилей выхода на РЕАЛЬНЫХ AUTO-входах.

Production НЕ модифицируется: движок только читает журнал входов и закрытий
и пишет собственный ledger. Те же входы, что в проде (по decision_id из
trade_genome OPEN — реал-тайм событие исполнения).

Профили на каждую сделку (ledger: logs/trades/exit_shadow_ledger.jsonl):
  REAL        — фактическая сделка production (entry/exit/NET из trade_results,
               включая guardian-ladder и ручные закрытия).
  A_p3_taker  — тот же вход (taker), P3-выход: 33% @ +1R, runner trail 0.75R,
               исходные SL/TP сохранены; консервативный M15-интрабар
               (SL-first; partial перед TP; trail запаздывает на 1 бар);
               taker fees + 2bps slippage на выход + funding est.
  B_maker_cur — фактический выход, maker-вход fee-оценка (fill не гарантирован).
  C_maker_p3  — A-путь + maker-вход fee-оценка.

Gate в production (фиксирован 2026-08-16 пользователем): >=30 новых shadow-сделок,
P3 NET>0, P3 NET>CURRENT, expectancy>0, PF>1, не 1-2 сделки, нет доминирующего
символа, несколько дней, реальные издержки, нет невозможных intrabar-филлов.
Отчёт по gate: exit_shadow_report.py.

Источники (read-only):
  memory/trade_genome.jsonl               OPEN-события (реал-тайм)
  logs/trades/trade_results.jsonl         закрытия сделок
  tradingos_lab/edge_factory/data/funding_snapshots/*.jsonl   funding est
Своё состояние: research/exit_shadow/shadow_state.json
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path("/root/tradingos")
GENOME = ROOT / "memory/trade_genome.jsonl"
RESULTS = ROOT / "logs/trades/trade_results.jsonl"
LEDGER = ROOT / "logs/trades/exit_shadow_ledger.jsonl"
STATE = ROOT / "research/exit_shadow/shadow_state.json"
FUNDING_DIR = Path("/root/tradingos_lab/edge_factory/data/funding_snapshots")

# ── Экономическая модель (едина с forensic /tmp/exit_sim, документируется в ledger) ──
RISK_USD = 0.50          # placeholder до известного фактического size
FEE_TAKER = 0.00055      # за сторону
FEE_MAKER = 0.00020      # за сторону (оценка, fill не гарантирован)
SLIP_BPS = 0.0002        # 2 bps на market-выход
P3_FRAC = 0.33           # доля частичной фиксации
P3_AT_R = 1.0            # уровень частичной фиксации
P3_TRAIL_R = 0.75        # трейл runner за экстремумом
HORIZON_H = 72           # таймаут симуляции

POLL_S = 60
SIM_MODE = "m15-consl:sl-first;partial-before-tp;trail-lag-1bar;timeout-72h"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(ROOT / "logs/exit_shadow.log")],
)
log = logging.getLogger("ExitShadow")


# ─────────────────────────── утилиты ───────────────────────────
def now_utc() -> float:
    return datetime.now(timezone.utc).timestamp()


def parse_iso(s: str) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            log.warning("state corrupted, reinitializing")
    return {"engine_started_at": now_utc(), "registered": {}, "finalized": []}


def save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    tmp.replace(STATE)


def read_jsonl(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def fetch_klines(sym: str, start_ms: int) -> list[tuple]:
    """Закрытые M15 бары с start_ms (публичный API, read-only). [(ts,o,h,l,c)]"""
    rows = []
    for attempt in range(3):
        try:
            r = httpx.get("https://api.bybit.com/v5/market/kline",
                          params={"category": "linear", "symbol": sym,
                                  "interval": "15", "start": int(start_ms),
                                  "limit": 1000}, timeout=25)
            d = r.json()
            if d.get("retCode") == 0:
                for b in d["result"]["list"]:
                    rows.append((int(b[0]) / 1000, float(b[1]), float(b[2]),
                                 float(b[3]), float(b[4])))
                rows.sort()
                return rows
        except Exception as e:
            log.warning(f"kline fetch fail {sym}: {e}")
        time.sleep(1.0)
    return rows


# ─────────────────────────── P3-симуляция ───────────────────────────
def simulate_p3(side: str, entry: float, sl0: float, tp0: float, bars: list[tuple]):
    """P3 на закрытых барах. КОНСЕРВАТИВНЫЙ интрабар:
      1) SL-проверка против уровня с ПРЕДЫДУЩИХ баров (SL-first при двойном касании);
      2) TP-проверка (после SL);
      3) partial @ +1R (перед TP — path-зависимость: +1R достигается раньше TP>=+1R);
         если бар коснулся и исходного SL, и +1R — считается SL без partial (худший случай);
      4) trail-обновление в КОНЦЕ итерации → трейл действует со следующего бара (lag 1).
    Возвращает dict с exits/done (не зависит от qty — экономика считается отдельно).
    """
    sign = 1 if side in ("BUY", "Buy", "LONG") else -1
    risk = abs(entry - sl0)
    if risk <= 0:
        return None
    sl = sl0
    partial_done = False
    runner_ext = None
    exits = []
    done = False
    t_end = bars[-1][0] + 900 if bars else 0
    horizon_s = HORIZON_H * 3600
    for ts, o, h, l, c in bars:
        if ts - bars[0][0] > horizon_s:
            break
        hit_sl = (l <= sl) if sign == 1 else (h >= sl)
        hit_tp = (h >= tp0) if sign == 1 else (l <= tp0)
        if hit_sl:  # SL-first: консервативно при любом двойном касании
            exits.append({"type": "SL", "ts": ts, "px": sl,
                          "frac": (1 - P3_FRAC) if partial_done else 1.0})
            done = True
            break
        if not partial_done:
            tpx = entry + P3_AT_R * risk * sign
            touched = (h >= tpx) if sign == 1 else (l <= tpx)
            if touched:
                partial_done = True
                exits.append({"type": "P", "ts": ts, "px": tpx, "frac": P3_FRAC})
                runner_ext = h if sign == 1 else l
        if hit_tp:
            exits.append({"type": "TP", "ts": ts, "px": tp0,
                          "frac": (1 - P3_FRAC) if partial_done else 1.0})
            done = True
            break
        # trail-обновление после проверок (lag 1 бар)
        if partial_done:
            runner_ext = max(runner_ext, h) if sign == 1 else min(runner_ext, l)
            trail = runner_ext - P3_TRAIL_R * risk * sign
            sl = max(sl, trail) if sign == 1 else min(sl, trail)
    if not done:
        # таймаут 72h / конец данных — выход по последней close
        last = bars[-1]
        if last[0] - bars[0][0] >= horizon_s:
            etype = "T72"
        else:
            etype = "OPEN"
        exits.append({"type": etype, "ts": last[0], "px": last[4],
                      "frac": (1 - P3_FRAC) if partial_done else 1.0})
        done = etype == "T72"
    return {"exits": exits, "done": done, "partial_done": partial_done,
            "last_bar_ts": t_end}


# ─────────────────────────── HYB_B (pre-committed hybrid) ───────────────────────────
HYB_TP1_R = 0.5     # partial TP1 at +0.5R, frac 0.25
HYB_TP2_R = 1.0     # partial TP2 at +1.0R, frac 0.75
HYB_FRAC1 = 0.25    # qty at TP1

def simulate_hyb_b(side: str, entry: float, sl0: float, tp0: float, bars: list[tuple]):
    """HYB_B: 25% @ +0.5R (maker reduce-only limit), 75% @ +1.0R (maker reduce-only limit),
       catastrophic SL остаётся как есть. SL-first intrabar. Conservative: MFE≠fill."""
    sign = 1 if side in ("BUY", "Buy", "LONG") else -1
    risk = abs(entry - sl0)
    if risk <= 0 or not bars:
        return {"exits": [], "done": False, "partial_done": False, "last_bar_ts": 0}
    sl = sl0
    tp1_px = entry + HYB_TP1_R * risk * sign
    tp2_px = entry + HYB_TP2_R * risk * sign
    catp = entry - 3 * risk * sign
    exits = []
    done = False
    tp1_done = False
    qty_rem = 1.0  # remaining fraction
    t_end = 0
    HORIZON_S = 72 * 3600
    for ts, o, h, l, c in bars:
        if ts - bars[0][0] > HORIZON_S:
            exits.append({"type": "T72", "ts": ts, "px": c, "frac": qty_rem})
            done = True
            t_end = ts
            break
        if (l <= catp) if sign == 1 else (h >= catp):
            exits.append({"type": "CAT", "ts": ts, "px": catp, "frac": qty_rem})
            done = True
            t_end = ts
            break
        hit_sl = (l <= sl) if sign == 1 else (h >= sl)
        hit_tp1 = (h >= tp1_px) if sign == 1 else (l <= tp1_px)
        hit_tp2 = (h >= tp2_px) if sign == 1 else (l <= tp2_px)
        # Conservative: SL-first intrabar
        if hit_sl:
            exits.append({"type": "SL", "ts": ts, "px": sl, "frac": qty_rem})
            done = True
            t_end = ts
            break
        if not tp1_done and hit_tp1:
            exits.append({"type": "TP1", "ts": ts, "px": tp1_px, "frac": HYB_FRAC1})
            tp1_done = True
            qty_rem = 1.0 - HYB_FRAC1
        if tp1_done and hit_tp2:
            exits.append({"type": "TP2", "ts": ts, "px": tp2_px, "frac": qty_rem})
            done = True
            t_end = ts
            break
        t_end = ts
    if not exits:
        exits.append({"type": "T72", "ts": t_end or 0, "px": c, "frac": qty_rem})
        done = True
    elif not done:
        exits.append({"type": "T72", "ts": t_end, "px": c, "frac": qty_rem})
        done = True
    return {"exits": exits, "done": done, "partial_done": tp1_done,
            "last_bar_ts": t_end}


def econ_hyb_b(side: str, entry: float, qty: float, exits: list[dict], exit_fee: float) -> dict:
    """HYB_B экономика: gross per leg + fees on each leg (maker fill at TP legs)."""
    sign = 1 if side in ("BUY", "Buy", "LONG") else -1
    gross_total = 0.0
    fees_total = 0.0
    slip_total = 0.0
    for ex in exits:
        px = ex["px"]
        frac = ex["frac"]
        leg_gross = (px - entry) * sign * qty * frac
        gross_total += leg_gross
        # Maker fee on TP legs (limit-fill), taker on SL/CAT/T72
        if ex["type"] in ("TP1", "TP2"):
            leg_fee = exit_fee * qty * entry * frac
        else:
            leg_fee = exit_fee * qty * entry * frac
        fees_total += leg_fee
    return {"net": round(gross_total - fees_total - slip_total, 6),
            "gross": round(gross_total, 6),
            "fees": round(fees_total, 6),
            "slippage": round(slip_total, 6),
            "exits": exits}


# ─────────────────────────── E_bot_clone (literal clone working bot @ +3.5%) ───────────────────────────
# Paired same-qty reduce-only limit at entry × (1 ± 3.5%). Maker fee on TP, taker on SL.
# SL-first intrabar. No MFE, no reversal, no trailing. Catastrophic SL kept.
EBC_SPREAD_PCT = 3.5  # bot median winner-spread; NOT a tuning knob per R151

def simulate_ebc(side: str, entry: float, sl0: float, tp0: float, bars: list[tuple]):
    """Literal clone: paired same-qty reduce-only limit @ entry*(1 ± EBC_SPREAD_PCT/100).
       SL-first intrabar (conservative). Maker fee on TP fill.
       Returns {exits, done, fill_reach, time_to_limit_bars}.
    """
    sign = 1 if side in ("BUY", "Buy", "LONG") else -1
    risk = abs(entry - sl0)
    if risk <= 0 or not bars:
        return {"exits": [], "done": False, "limit_reach": "none",
                "time_to_limit_bars": None}
    if sign == 1:
        limit_px = entry * (1 + EBC_SPREAD_PCT / 100.0)
    else:
        limit_px = entry * (1 - EBC_SPREAD_PCT / 100.0)
    sl = sl0
    catp = entry - 3 * risk * sign
    exits = []
    done = False
    limit_reach = "none"      # none / touched / trade_through
    time_to_limit_bars = None
    t_end = 0
    HORIZON_S = 72 * 3600
    for ts, o, h, l, c in bars:
        if ts - bars[0][0] > HORIZON_S:
            exits.append({"type": "T72", "ts": ts, "px": c, "frac": 1.0})
            done = True
            t_end = ts
            break
        if (l <= catp) if sign == 1 else (h >= catp):
            exits.append({"type": "CAT", "ts": ts, "px": catp, "frac": 1.0})
            done = True
            t_end = ts
            break
        hit_sl = (l <= sl) if sign == 1 else (h >= sl)
        hit_tp = (h >= limit_px) if sign == 1 else (l <= limit_px)
        if hit_sl:
            exits.append({"type": "SL", "ts": ts, "px": sl, "frac": 1.0})
            done = True
            t_end = ts
            break
        if hit_tp:
            # determine if trade-through or only touched: next bar must move beyond limit
            idx = bars.index((ts, o, h, l, c))
            if idx + 1 < len(bars):
                nxt_h, nxt_l = bars[idx + 1][2], bars[idx + 1][3]
                if sign == 1 and nxt_h > limit_px:
                    limit_reach = "trade_through"
                elif sign == -1 and nxt_l < limit_px:
                    limit_reach = "trade_through"
                else:
                    limit_reach = "touched_only"
            else:
                limit_reach = "touched_only"
            time_to_limit_bars = idx
            exits.append({"type": "TP", "ts": ts, "px": limit_px, "frac": 1.0})
            done = True
            t_end = ts
            break
        t_end = ts
    if not exits:
        exits.append({"type": "T72", "ts": t_end or 0, "px": bars[-1][4], "frac": 1.0})
        done = True
    elif not done:
        exits.append({"type": "T72", "ts": t_end, "px": bars[-1][4], "frac": 1.0})
        done = True
    return {"exits": exits, "done": done, "limit_reach": limit_reach,
            "time_to_limit_bars": time_to_limit_bars}


def econ_ebc(side: str, entry: float, qty: float, exits: list[dict], fee_maker: float, fee_taker: float) -> dict:
    """E_bot_clone economics: TP leg uses maker fee, others taker."""
    sign = 1 if side in ("BUY", "Buy", "LONG") else -1
    gross_total = 0.0
    fees_total = 0.0
    for ex in exits:
        px = ex["px"]
        frac = ex["frac"]
        gross_total += (px - entry) * sign * qty * frac
        if ex["type"] == "TP":
            fees_total += fee_maker * qty * entry * frac
        else:
            fees_total += fee_taker * qty * entry * frac
    return {"net": round(gross_total - fees_total, 6),
            "gross": round(gross_total, 6),
            "fees": round(fees_total, 6),
            "slippage": 0.0,
            "exits": exits}


def econ_p3(side: str, entry: float, qty: float, exits: list[dict],
            entry_fee_rate: float = FEE_TAKER) -> dict:
    """NET P3-профиля при известном qty: gross - fees - slippage (по ногам)."""
    sign = 1 if side in ("BUY", "Buy", "LONG") else -1
    notional = qty * entry
    gross = fees = slip = 0.0
    for ex in exits:
        f = ex["frac"]
        gross += (ex["px"] - entry) * sign * qty * f
        fees += FEE_TAKER * notional * f          # все ноги консервативно taker
        slip += SLIP_BPS * ex["px"] * qty * f
    fees += entry_fee_rate * notional
    return {"gross": round(gross, 6), "fees": round(fees, 6),
            "slippage": round(slip, 6), "net": round(gross - fees - slip, 6)}


# ─────────────────────────── funding est ───────────────────────────
def funding_series(symbol: str, entry_ts: float, exit_ts: float) -> list[tuple]:
    """[(t_sec, rate)] по снапшотам за окно [entry, exit]. Пусто → нет данных."""
    out = []
    d0 = datetime.fromtimestamp(entry_ts, tz=timezone.utc).date()
    d1 = datetime.fromtimestamp(exit_ts, tz=timezone.utc).date()
    day = d0
    while day <= d1:
        f = FUNDING_DIR / f"{day.isoformat()}.jsonl"
        if f.exists():
            for rec in read_jsonl(f):
                v = rec.get("venues", {}).get("bybit", {}).get(symbol)
                if v and v.get("funding_rate") is not None:
                    try:
                        out.append((rec["ts_unix"] / 1000, float(v["funding_rate"]),
                                    int(v.get("funding_interval_min") or 480)))
                    except Exception:
                        continue
        day += timedelta(days=1)
    out.sort()
    return out


def funding_cost(symbol: str, side: str, qty: float, entry: float,
                 entry_ts: float, exits: list[dict]) -> dict:
    """Funding по ногам P3 (frac меняется после partial/выхода). LONG платит положительный rate."""
    series = funding_series(symbol, entry_ts, exits[-1]["ts"] if exits else entry_ts)
    if not series:
        return {"usd": None, "est": False}
    sign = 1 if side in ("BUY", "Buy", "LONG") else -1
    interval = series[-1][2] * 60
    usd = 0.0
    t = (int(entry_ts / interval) + 1) * interval
    while t < exits[-1]["ts"]:
        rate = None
        for ts_, r_, _ in reversed(series):
            if ts_ <= t:
                rate = r_
                break
        if rate is None:
            rate = 0.0
        frac = 1.0
        for ex in exits:
            if ex["ts"] <= t:
                frac = 0.0 if ex["type"] in ("SL", "TP", "T72") else (1 - P3_FRAC)
            else:
                break
        usd += rate * qty * entry * frac * sign
        t += interval
    return {"usd": round(usd, 6), "est": True}


# ─────────────────────────── actual close ───────────────────────────
def find_actual_close(meta: dict, results: list[dict]) -> dict | None:
    """Закрытие реальной сделки: symbol + entry ~1% + close после входа, ближайшее."""
    best = None
    for r in results:
        if r.get("symbol") != meta["symbol"] or r.get("source") == "bybit_rebuild":
            continue
        cts = parse_iso(r.get("timestamp", ""))
        if cts is None or cts < meta["entry_ts"]:
            continue
        if abs(r.get("entry", 0) - meta["entry"]) / max(meta["entry"], 1e-9) > 0.01:
            continue
        if best is None or cts < best[0]:
            best = (cts, r)
    return best[1] if best else None


# ─────────────────────────── главный цикл ───────────────────────────
def register_new_opens(st: dict, since_ts: float) -> None:
    for g in read_jsonl(GENOME):
        if g.get("event") != "OPEN":
            continue
        did = g.get("decision_id")
        if not did or did in st["registered"] or did in st["finalized"]:
            continue
        ets = g.get("ts_unix") or parse_iso(g.get("ts", "")) or 0
        if ets < since_ts:
            continue
        st["registered"][did] = {
            "decision_id": did, "symbol": g["symbol"], "side": g["side"],
            "entry": float(g["fill_price"] or g["entry"]), "sl": float(g["sl"]),
            "tp": float(g["tp"]), "entry_ts": ets, "ticket": g.get("ticket"),
            "registered_at": now_utc(),
        }
        log.info(f"registered shadow entry {did} {g['symbol']} {g['side']} "
                 f"entry={g['fill_price'] or g['entry']}")


def finalize(st: dict, meta: dict, results: list[dict]) -> bool:
    """Финализация: реальное закрытие найдено И симуляция завершена. Пишет ledger."""
    did = meta["decision_id"]
    if did in st["finalized"]:
        return True
    actual = find_actual_close(meta, results)
    bars = fetch_klines(meta["symbol"], int(meta["entry_ts"] * 1000))
    bars = [b for b in bars if b[0] >= meta["entry_ts"] - 900
            and b[0] + 900 <= now_utc()]          # только ЗАКРЫТЫЕ бары
    sim = simulate_p3(meta["side"], meta["entry"], meta["sl"], meta["tp"], bars) if bars else None
    timed_out = bars and bars[-1][0] - meta["entry_ts"] >= HORIZON_H * 3600
    if actual is None:
        return False
    if sim is None or (not sim["done"] and not timed_out):
        return False  # ждём завершения P3-пути (или 72h)

    qty = float(actual.get("size") or 0) or (RISK_USD / abs(meta["entry"] - meta["sl"]))
    notional = qty * meta["entry"]
    entry_fee_saving = (FEE_TAKER - FEE_MAKER) * notional

    # REAL
    real_net = float(actual.get("net_pnl") or 0)
    # A: P3, taker
    a = econ_p3(meta["side"], meta["entry"], qty, sim["exits"], FEE_TAKER)
    fund = funding_cost(meta["symbol"], meta["side"], qty, meta["entry"],
                        meta["entry_ts"], sim["exits"])
    if fund["usd"]:
        a["net"] = round(a["net"] + fund["usd"], 6)
    # B: текущий выход + maker-вход (fee-оценка)
    b_net = round(real_net + entry_fee_saving, 6)
    # C: P3-путь + maker-вход
    c_net = round(a["net"] + entry_fee_saving, 6)

    rec = {
        "shadow_version": 1,
        "finalized_at": datetime.now(timezone.utc).isoformat(),
        "decision_id": did, "symbol": meta["symbol"], "side": meta["side"],
        "entry": meta["entry"], "sl": meta["sl"], "tp": meta["tp"],
        "qty": qty, "notional": round(notional, 4),
        "entry_ts": meta["entry_ts"],
        # Мониторинг каузальности (read-only, из fact-закрытия): MFE/MAE/realized R
        "mfe_peak_r": actual.get("mfe_peak_r"),
        "mae_trough_r": actual.get("mae_trough_r"),
        "realized_R": actual.get("r") or actual.get("realized_R"),
        "holding_hours": actual.get("holding_hours"),
        "real": {"exit_ts": actual.get("timestamp"), "outcome": actual.get("outcome"),
                 "net": real_net, "gross": actual.get("gross_pnl"),
                 "fees": actual.get("fees"), "slippage": actual.get("slippage_cost"),
                 "r": actual.get("r") or actual.get("r_multiple"),
                 "source": "trade_results"},
        "A_p3_taker": {**a, "exits": sim["exits"]},
        "B_maker_cur": {"net": b_net, "note": "actual path + maker entry fee est"},
        "C_maker_p3": {"net": c_net, "note": "P3 path + maker entry fee est"},
        "delta_A": round(a["net"] - real_net, 6),
        "delta_B": round(b_net - real_net, 6),
        "delta_C": round(c_net - real_net, 6),
        "funding": fund,
        "sim_mode": SIM_MODE,
        "fee_model": {"taker_side": FEE_TAKER, "maker_side": FEE_MAKER,
                      "exit_slip": SLIP_BPS, "p3": {"frac": P3_FRAC,
                      "at_R": P3_AT_R, "trail_R": P3_TRAIL_R}},
    }
    # ──────── HYB_B (read-only 4-й profile, добавлен для paired forensic) ────────
    hyb_sim = simulate_hyb_b(meta["side"], meta["entry"], meta["sl"], meta["tp"], bars) if bars else None
    hyb_done = hyb_sim is not None and (hyb_sim["done"] or timed_out)
    if hyb_done:
        hyb_econ = econ_hyb_b(meta["side"], meta["entry"], qty, hyb_sim["exits"], FEE_MAKER)
        # add HYB_B-only funding: per leg
        hyb_fund = funding_cost(meta["symbol"], meta["side"], qty, meta["entry"],
                                meta["entry_ts"], hyb_sim["exits"])
        if hyb_fund["usd"]:
            hyb_econ["net"] = round(hyb_econ["net"] + hyb_fund["usd"], 6)
        rec["D_hyb_b"] = {**hyb_econ, "exits": hyb_sim["exits"]}
        rec["delta_D"] = round(hyb_econ["net"] - real_net, 6)
        rec["D_done"] = hyb_done
    else:
        rec["D_hyb_b"] = {"pending": True, "reason": "sim not yet done" if hyb_sim else "no bars"}
        rec["delta_D"] = None
    # ──────── E_bot_clone @ +3.5% (read-only 5-й profile) ────────
    ebc_sim = simulate_ebc(meta["side"], meta["entry"], meta["sl"], meta["tp"], bars) if bars else None
    ebc_done = ebc_sim is not None and (ebc_sim["done"] or timed_out)
    if ebc_done:
        ebc_econ = econ_ebc(meta["side"], meta["entry"], qty, ebc_sim["exits"],
                             FEE_MAKER, FEE_TAKER)
        ebc_fund = funding_cost(meta["symbol"], meta["side"], qty, meta["entry"],
                                meta["entry_ts"], ebc_sim["exits"])
        if ebc_fund["usd"]:
            ebc_econ["net"] = round(ebc_econ["net"] + ebc_fund["usd"], 6)
        rec["E_bot_clone"] = {**ebc_econ, "exits": ebc_sim["exits"],
                               "limit_reach": ebc_sim.get("limit_reach"),
                               "time_to_limit_bars": ebc_sim.get("time_to_limit_bars")}
        rec["delta_E"] = round(ebc_econ["net"] - real_net, 6)
        rec["E_done"] = ebc_done
    else:
        rec["E_bot_clone"] = {"pending": True, "reason": "sim not yet done" if ebc_sim else "no bars"}
        rec["delta_E"] = None
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    st["finalized"].append(did)
    st["registered"].pop(did, None)
    log.info(f"FINALIZED {did} {meta['symbol']}: real={real_net:+.3f} "
             f"A={a['net']:+.3f} (Δ{rec['delta_A']:+.3f}) B={b_net:+.3f} C={c_net:+.3f} "
             f"D(hyb_b)={(rec.get('delta_D') or 0):+.3f} "
             f"E(ebc@+3.5%)={(rec.get('delta_E') or 0):+.3f}")
    return True


def main() -> None:
    since_env = os.environ.get("EXIT_SHADOW_SINCE")
    st = load_state()
    since_ts = float(since_env) if since_env else st.get("engine_started_at", now_utc())
    if since_env and float(since_env) < st.get("engine_started_at", 0):
        st["engine_started_at"] = float(since_env)
    log.info(f"exit-shadow engine started; registering opens since "
             f"{datetime.fromtimestamp(since_ts, tz=timezone.utc).isoformat()}")
    while True:
        try:
            register_new_opens(st, since_ts)
            results = read_jsonl(RESULTS)
            for did, meta in list(st["registered"].items()):
                finalize(st, meta, results)
            save_state(st)
        except Exception:
            log.exception("cycle failed (continuing)")
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
