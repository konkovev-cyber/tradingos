"""
tools/tradable_universe_report.py
Hourly report: how many Bybit linear symbols are PHYSICALLY tradeable at the
current risk_per_trade ($0.25 by default)?

Answers the question: "are we waiting for a signal, or is the risk budget too
small for the market?" — using the SAME math as _execute_reality:

    raw_qty      = risk / (2 * ATR)
    qty          = floor(raw_qty / qtyStep) * qtyStep
    required     = max(minOrderQty, ceil(minNotional / price) rounded up to step)
    tradeable    = qty >= required AND qty * price <= 20% of equity (notional cap)

ATR: real ATR-14 from the observation FeatureStore when available (watched
symbols), otherwise the price*2% proxy used by reality_universe.py.
Report does NOT change any trading logic. Pure telemetry.

Run standalone:
    python3 -m tradingos.tools.tradable_universe_report
"""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from tradingos.data.reality_universe import classify_instrument

REPORT_PATH = Path("/root/tradingos/tradable_universe_report.json")

# Same proxy as reality_universe.py ("General risk estimate: use 2% of price as ATR proxy")
ATR_PROXY_PCT = 0.02
MAX_NOTIONAL_PCT = 0.20  # mirror of _execute_reality

SIGNAL_LOG = Path("/root/tradingos/memory/signal_log.jsonl")
FUNNEL_EVENTS = Path("/root/tradingos/memory/funnel_events.jsonl")

# Candidate thresholds — same values as run_observation.py / SignalGenerator
PROB_THRESHOLD = 0.55
QUALITY_OK = ("MEDIUM", "GOOD", "EXCELLENT")
SCORE_THRESHOLD = 55
ADX_THRESHOLD = 20


def _iter_signal_rows(signal_log: Path = SIGNAL_LOG):
    """Stream signal_log rows (append-only)."""
    if not signal_log.exists():
        return
    with signal_log.open("r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def build_funnels(tradeable_symbols: set[str], signal_log: Path | None = None,
                  funnel_events: Path | None = None) -> dict:
    """1h and 24h pipeline funnels in a single pass over the logs.

    Pure telemetry — reads signal_log + funnel_events, changes nothing.
    Stages follow the REALITY execution path:
      checks → signals → prob>=0.55 → quality_ok → passed_adx → tradeable_qty
      → proposal → opened
    """
    signal_log = signal_log or SIGNAL_LOG
    funnel_events = funnel_events or FUNNEL_EVENTS
    now = time.time()
    bounds = {"1h": now - 3600, "24h": now - 86400}
    stage_names = ("checks", "signals", "prob_ge_0.55", "quality_ok",
                   "passed_adx", "tradeable_qty", "proposal", "opened")
    funnel = {w: dict.fromkeys(stage_names, 0) for w in bounds}

    def bump(t: float, idx: int):
        # Cumulative funnel: a row reaching stage N counts in every stage <= N.
        for w, bound in bounds.items():
            if t >= bound:
                for i in range(idx + 1):
                    funnel[w][stage_names[i]] += 1

    for r in _iter_signal_rows(signal_log):
        ts = r.get("timestamp", "")
        try:
            t = datetime.fromisoformat(ts).timestamp()
        except Exception:
            continue
        idx = 0  # checks
        if r.get("direction") not in ("BUY", "SELL"):
            bump(t, idx)
            continue
        idx = 1  # signals
        if (r.get("final_probability") or 0) < PROB_THRESHOLD:
            bump(t, idx)
            continue
        idx = 2  # prob_ge_0.55
        if r.get("quality") not in QUALITY_OK or (r.get("score") or 0) < SCORE_THRESHOLD:
            bump(t, idx)
            continue
        idx = 3  # quality_ok
        if (r.get("adx") or 0) < ADX_THRESHOLD:
            bump(t, idx)
            continue
        idx = 4  # passed_adx
        if r.get("symbol") in tradeable_symbols:
            idx = 5  # tradeable_qty
        bump(t, idx)

    if funnel_events.exists():
        with funnel_events.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                idx = 7 if ev.get("event") == "opened" else 6
                bump(float(ev.get("ts", 0)), idx)
    return funnel


def _atr_for(symbol: str, price: float, feature_store=None) -> tuple[float, str]:
    """Real ATR-14 if the store has candles for this symbol, else price*2% proxy."""
    if feature_store is not None:
        try:
            from tradingos.data.indicators import IndicatorCalculator
            candles = feature_store.get_candles(symbol, "1h")
            if candles and len(candles) >= 14:
                atr = IndicatorCalculator().atr(candles, 14)
                if atr and atr > 0:
                    return atr, "store_atr14"
        except Exception:
            pass
    return price * ATR_PROXY_PCT, "proxy_price_2pct"


def generate_tradable_universe_report(feature_store=None,
                                      report_path: Path | str = REPORT_PATH,
                                      allowed: tuple = ("crypto_perp",),
                                      signal_log: Path | None = None,
                                      funnel_events: Path | None = None,
                                      config_path: Path | str = "/root/tradingos/operations/trading_mode.json") -> dict:
    """Scan the linear universe and write/return the tradability report.

    allowed: instrument classes to include (see classify_instrument):
      ("crypto_perp",) for the crypto circuit, or
      ("tokenized_stock", "commodity_token") for the tradFi circuit.
    """
    t0 = time.time()

    # Risk budget from config (same source as the circuit's executor)
    risk = 0.25
    try:
        cfg = json.loads(Path(config_path).read_text())
        risk = float(cfg.get("risk_per_trade", 0.25))
    except Exception:
        pass

    # Equity for the notional cap (signed request; 0 means cap unknown)
    equity = 0.0
    try:
        from tradingos.strategies.trade_executor import _get_balance_or_zero
        equity = _get_balance_or_zero()
    except Exception:
        pass

    inst_resp = httpx.get("https://api.bybit.com/v5/market/instruments-info",
                          params={"category": "linear", "limit": 1000}, timeout=30)
    inst_data = inst_resp.json()
    if inst_data.get("retCode") != 0:
        raise RuntimeError(f"instruments-info failed: {inst_data.get('retMsg')}")

    tick_resp = httpx.get("https://api.bybit.com/v5/market/tickers",
                          params={"category": "linear"}, timeout=30)
    tick_map = {}
    tick_data = tick_resp.json()
    if tick_data.get("retCode") == 0:
        tick_map = {t["symbol"]: t for t in tick_data["result"].get("list", [])}

    counters = {
        "symbols_checked": 0,
        "tradable_with_risk_0.25": 0,
        "blocked_by_min_qty": 0,
        "blocked_by_notional_cap": 0,
        "blocked_no_price": 0,
    }
    tradable_list: list[dict] = []
    blocked_list: list[dict] = []

    for inst in inst_data["result"].get("list", []):
        symbol = inst.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue
        # Circuit universe filter (exchange taxonomy)
        cls = classify_instrument(symbol, inst.get("symbolType", ""))
        if cls not in allowed:
            counters[f"skipped_{cls}"] = counters.get(f"skipped_{cls}", 0) + 1
            continue
        tick = tick_map.get(symbol)
        if not tick:
            continue
        price = float(tick.get("lastPrice", 0) or 0)
        volume_24h = float(tick.get("volume24h", 0) or 0)
        if price <= 0:
            counters["blocked_no_price"] += 1
            continue

        lot = inst.get("lotSizeFilter", {})
        min_qty = float(lot.get("minOrderQty", 1) or 1)
        qty_step = float(lot.get("qtyStep", 1) or 1)
        if qty_step <= 0:
            qty_step = 1.0
        min_notional = float(lot.get("minNotionalValue", 0) or 0)

        atr, atr_source = _atr_for(symbol, price, feature_store)
        raw_qty = risk / (2 * max(atr, 0.0001))
        quantity = math.floor(raw_qty / qty_step) * qty_step

        notional_qty = 0.0
        if min_notional > 0:
            notional_qty = math.ceil((min_notional / price) / qty_step) * qty_step
        required_qty = max(min_qty, notional_qty)

        cap_reason = None
        if equity > 0:
            max_qty = math.floor(equity * MAX_NOTIONAL_PCT / price / qty_step) * qty_step
            if quantity > max_qty:
                quantity = max_qty
                cap_reason = "notional_cap"

        counters["symbols_checked"] += 1
        entry = {
            "symbol": symbol,
            "price": price,
            "atr_source": atr_source,
            "raw_qty": round(raw_qty, 4),
            "tradeable_qty": round(quantity, 4),
            "required_qty": round(required_qty, 4),
            "min_order_qty": min_qty,
            "qty_step": qty_step,
            "min_notional": min_notional,
            "risk_required_usd": round(required_qty * 2 * atr, 2),
            "volume_24h": round(volume_24h, 0),
        }
        if quantity < required_qty:
            counters["blocked_by_min_qty"] += 1
            entry["block_reason"] = "min_qty"
            blocked_list.append(entry)
        elif cap_reason:
            counters["blocked_by_notional_cap"] += 1
            entry["block_reason"] = "notional_cap"
            blocked_list.append(entry)
        else:
            counters["tradable_with_risk_0.25"] += 1
            tradable_list.append(entry)

    tradeable_symbols = {t["symbol"] for t in tradable_list}
    funnels = build_funnels(tradeable_symbols, signal_log=signal_log, funnel_events=funnel_events)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe_class": "+".join(sorted(allowed)),
        "risk_per_trade_usd": risk,
        "equity_usd": equity,
        "atr_proxy_pct": ATR_PROXY_PCT * 100,
        "notional_cap_pct": MAX_NOTIONAL_PCT * 100,
        "counters": counters,
        "funnel_1h": funnels["1h"],
        "funnel_24h": funnels["24h"],
        "top_tradable": sorted(tradable_list, key=lambda x: x["volume_24h"], reverse=True)[:15],
        "top_blocked_by_min_qty": sorted(
            [b for b in blocked_list if b["block_reason"] == "min_qty"],
            key=lambda x: x["risk_required_usd"], reverse=True)[:15],
        "top_blocked_by_notional_cap": sorted(
            [b for b in blocked_list if b["block_reason"] == "notional_cap"],
            key=lambda x: x["risk_required_usd"], reverse=True)[:15],
        "scan_seconds": round(time.time() - t0, 2),
    }
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def _print_summary(report: dict) -> None:
    c = report["counters"]
    print(f"\n=== TRADABLE UNIVERSE REPORT @ {report['generated_at']} ===")
    print(f"universe: {report.get('universe_class', '?')}  "
          f"risk=$ {report['risk_per_trade_usd']:.2f}  equity=${report['equity_usd']:.2f}  "
          f"(ATR proxy {report['atr_proxy_pct']:.0f}% of price, notional cap {report['notional_cap_pct']:.0f}%)")
    print(f"symbols_checked:            {c['symbols_checked']}")
    print(f"tradable_with_risk_0.25:    {c['tradable_with_risk_0.25']}")
    print(f"blocked_by_min_qty:         {c['blocked_by_min_qty']}")
    print(f"blocked_by_notional_cap:    {c['blocked_by_notional_cap']}")
    print(f"blocked_no_price:           {c['blocked_no_price']}")
    for k in sorted(c):
        if k.startswith("skipped_"):
            print(f"{k:<28} {c[k]}")
    f1 = report.get("funnel_1h", {})
    f24 = report.get("funnel_24h", {})
    print(f"\nFunnel (1h / 24h):")
    for stage in ("checks", "signals", "prob_ge_0.55", "quality_ok", "passed_adx",
                  "tradeable_qty", "proposal", "opened"):
        print(f"  {stage:<16} {f1.get(stage, 0):>7} / {f24.get(stage, 0):>8}")
    print(f"\nTop tradable:")
    for t in report["top_tradable"]:
        print(f"  {t['symbol']:<14} price={t['price']:<12} qty={t['tradeable_qty']:<10} "
              f"risk_required=${t['risk_required_usd']:<6} vol={t['volume_24h']:.0f}")
    print(f"\nTop blocked by min qty:")
    for t in report["top_blocked_by_min_qty"]:
        print(f"  {t['symbol']:<14} required={t['required_qty']:<10} "
              f"risk_required=${t['risk_required_usd']:<6} (minOrderQty={t['min_order_qty']} "
              f"minNotional=${t['min_notional']})")


if __name__ == "__main__":
    rep = generate_tradable_universe_report()
    _print_summary(rep)
    print(f"\nReport: {REPORT_PATH}")
