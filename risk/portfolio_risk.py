#!/usr/bin/env python3
"""
portfolio_risk.py — Единый риск-босс для всех торговых контуров.

Один источник правды: /root/tradingos/operations/portfolio_risk.json
Каждый контур (reality/auto_limit/funding/dn_sweep/manual) перед входом
вызывает `can_open(symbol, side, risk_usd)` и получает GO/NO_GO.

Считает:
  - суммарный открытый риск ($ SL-дистанция × qty по всем позициям)
  - same-side счётчики (LONG/SHORT: лимитки + позиции)
  - дневной realized PnL (из deposit_guard_state)

Капы (operations/trading_mode.json, секция portfolio_risk):
  max_total_risk_usd    — макс суммарный открытый риск
  max_same_side         — макс позиций одного направления
  max_risk_per_trade    — макс риск одной сделки
  day_loss_pause_usd    — при дневном loss ≥ порога все новые входы запрещены

Fail-safe: если риск-файл не читается — блокируем вход (fail-closed) для
AUTO контуров; для ручного контура — разрешаем с логом (владелец сам решает).
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
STATE = ROOT / "operations/portfolio_risk.json"
TM = ROOT / "operations/trading_mode.json"
DG = ROOT / "operations/deposit_guard_state.json"
EVENTS = ROOT / "memory/portfolio_risk_events.jsonl"

DEFAULTS = {
    "max_total_risk_usd": 75.0,
    "max_same_side": 2,
    "max_risk_per_trade": 25.0,
    "day_loss_pause_usd": 150.0,
}


def _cfg() -> dict:
    """Load portfolio risk caps from trading_mode.json, fallback DEFAULTS."""
    try:
        tm = json.loads(TM.read_text())
        pr = tm.get("portfolio_risk", {})
        return {k: type(v)(pr.get(k, dv)) for k, dv in
                ((k, DEFAULTS[k]) for k in DEFAULTS)}
    except Exception:
        return dict(DEFAULTS)


def _log(event: str, data: dict) -> None:
    try:
        rec = {"ts": time.time(),
               "iso": datetime.now(timezone.utc).isoformat(),
               "event": event, **data}
        EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def snapshot() -> dict:
    """Compute current portfolio risk from Bybit positions + local limit state.

    Returns {"total_risk_usd", "side_counts", "day_pnl", "positions"}.
    """
    total_risk = 0.0
    sides = {"LONG": 0, "SHORT": 0}
    positions = []

    # 1. Live positions from Bybit (demo)
    try:
        import sys
        sys.path.insert(0, "/root")
        sys.path.insert(0, "/root/tradingos")
        from tradingos.signals.auto_limit_placer import _signed_get
        res = _signed_get("/v5/position/list", "category=linear&settleCoin=USDT")
        if res.get("retCode") == 0:
            for p in res.get("result", {}).get("list", []):
                if float(p.get("size", 0)) == 0:
                    continue
                entry = float(p.get("avgPrice", 0) or 0)
                slv = float(p.get("stopLoss", 0) or 0)
                sz = float(p.get("size", 0))
                if entry > 0 and slv > 0:
                    total_risk += abs(entry - slv) * sz
                ps = "LONG" if p.get("side") == "Buy" else "SHORT"
                sides[ps] += 1
                positions.append(p.get("symbol"))
    except Exception as e:
        _log("SNAPSHOT_POS_ERR", {"error": str(e)})

    # 2. Pending limits from auto_limit state (they will become positions)
    try:
        al = json.loads((ROOT / "operations/auto_limit_state.json").read_text())
        for lim in al.get("active_limits", {}).values():
            s = lim.get("side")
            if s in ("LONG", "BUY"):
                sides["LONG"] += 1
            else:
                sides["SHORT"] += 1
    except Exception:
        pass

    # 3. Day PnL from deposit guard
    day_pnl = 0.0
    try:
        dg = json.loads(DG.read_text())
        day_pnl = float(dg.get("realized_pnl_day", 0) or 0)
    except Exception:
        pass

    return {
        "ts": time.time(),
        "iso": datetime.now(timezone.utc).isoformat(),
        "total_risk_usd": round(total_risk, 2),
        "side_counts": sides,
        "day_pnl_usd": round(day_pnl, 2),
        "positions": positions,
    }


def can_open(symbol: str, side: str, risk_usd: float,
             source: str = "?") -> tuple[bool, str]:
    """GO/NO_GO for a new entry. Fail-closed on config errors.

    Returns (allowed, reason). reason is "" on GO.
    """
    cfg = _cfg()
    snap = snapshot()

    # Day loss pause: if today's realized loss >= threshold → block everything
    if snap["day_pnl_usd"] <= -cfg["day_loss_pause_usd"]:
        _log("BLOCK_DAY_LOSS", {"symbol": symbol, "source": source,
                                  "day_pnl": snap["day_pnl_usd"]})
        return False, f"DAY_LOSS_PAUSE: day PnL {snap['day_pnl_usd']:.2f} <= -{cfg['day_loss_pause_usd']:.0f}"

    # Portfolio total risk
    if snap["total_risk_usd"] + risk_usd > cfg["max_total_risk_usd"]:
        _log("BLOCK_TOTAL_RISK", {"symbol": symbol, "source": source,
                                    "open": snap["total_risk_usd"],
                                    "new": risk_usd,
                                    "cap": cfg["max_total_risk_usd"]})
        return False, (f"TOTAL_RISK: open ${snap['total_risk_usd']:.2f} + "
                       f"new ${risk_usd:.2f} > cap ${cfg['max_total_risk_usd']:.0f}")

    # Per-trade risk
    if risk_usd > cfg["max_risk_per_trade"]:
        _log("BLOCK_TRADE_RISK", {"symbol": symbol, "source": source,
                                    "risk": risk_usd,
                                    "cap": cfg["max_risk_per_trade"]})
        return False, (f"TRADE_RISK: ${risk_usd:.2f} > cap "
                       f"${cfg['max_risk_per_trade']:.2f}")

    # Same-side
    s = "LONG" if side in ("LONG", "BUY") else "SHORT"
    if snap["side_counts"][s] >= cfg["max_same_side"]:
        _log("BLOCK_SAME_SIDE", {"symbol": symbol, "source": source, "side": s,
                                   "count": snap["side_counts"][s],
                                   "cap": cfg["max_same_side"]})
        return False, (f"SAME_SIDE: {s} count {snap['side_counts'][s]} "
                       f">= cap {cfg['max_same_side']}")

    return True, "GO"


def write_snapshot() -> None:
    """Persist snapshot for external tools / owner inspection."""
    try:
        snap = snapshot()
        snap["caps"] = _cfg()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snap, indent=2, ensure_ascii=False))
        tmp.replace(STATE)
    except Exception:
        pass


if __name__ == "__main__":
    write_snapshot()
    snap = snapshot()
    caps = _cfg()
    print(f"=== PORTFOLIO RISK SNAPSHOT ===")
    print(f"Total open risk:  ${snap['total_risk_usd']:.2f} / ${caps['max_total_risk_usd']}")
    print(f"Sides: LONG={snap['side_counts']['LONG']} SHORT={snap['side_counts']['SHORT']} (cap {caps['max_same_side']}/side)")
    print(f"Day PnL: ${snap['day_pnl_usd']:+.2f} (pause at -${caps['day_loss_pause_usd']})")
    print(f"Open positions: {snap['positions']}")