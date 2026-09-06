#!/usr/bin/env python3
"""TG-команды /pnl и /readiness — per-contour PnL и Live Readiness Gate.

Только чтение логов. Регистрируются в manual_bot.py.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
TRADES = ROOT / "logs/trades/trade_results.jsonl"
HEALTH = ROOT / "operations/contour_health.json"
LIMIT_STATE = ROOT / "operations/auto_limit_state.json"
EXEC_LOG = ROOT / "logs/executed_contours.jsonl"


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _load_trades(days: int):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out = []
    with open(TRADES) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            try:
                dt = datetime.fromisoformat(d["timestamp"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt >= cutoff:
                out.append(d)
    return out


def _contour_of(t):
    return t.get("contour") or "unknown"


def cmd_pnl(update, context):
    """Пер-контурный PnL за 7 и 30 дней + slippage."""
    try:
        lines = ["📊 <b>PnL по контурам</b>\n"]
        for days, label in ((7, "7 дней"), (30, "30 дней")):
            trades = _load_trades(days)
            by = defaultdict(lambda: [0, 0.0, 0])
            for t in trades:
                c = _contour_of(t)
                pnl = float(t.get("net_pnl") or 0)
                by[c][0] += 1
                by[c][1] += pnl
                if pnl > 0:
                    by[c][2] += 1
            total = sum(v[1] for v in by.values())
            lines.append(f"\n<b>{label}</b> ({len(trades)} сд, NET {total:+,.0f}$)")
            for c, (n, pnl, w) in sorted(by.items(), key=lambda x: -x[1][1]):
                wr = w / n * 100 if n else 0
                lines.append(f"  • {_esc(c)}: {n} сд | {pnl:+,.0f}$ | WR {wr:.0f}%")
        # slippage из executed_contours
        slips = []
        if EXEC_LOG.exists():
            with open(EXEC_LOG) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    ee, ff = r.get("expected_entry"), r.get("fill_price")
                    if ee and ff and ee > 0:
                        slips.append(abs(ff - ee) / ee * 100)
        if slips:
            avg_slip = sum(slips) / len(slips)
            lines.append(f"\n📐 Slippage входа (n={len(slips)}): {avg_slip:.3f}% медианный по сделкам")
        else:
            lines.append("\n📐 Slippage: данных пока нет")
        update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
    except Exception as e:
        update.effective_message.reply_text(f"❌ pnl error: {_esc(e)}", parse_mode="HTML")


def cmd_readiness(update, context):
    """Live Readiness Gate — чеклист перехода на реальный счёт."""
    checks = []
    # 1. Эдж за 14 дней
    try:
        trades = _load_trades(14)
        rs = []
        for t in trades:
            e, sl, sz = (t.get("entry", 0) or 0, t.get("sl", 0) or 0, t.get("size", 0) or 0)
            if e and sl and sz and abs(e - sl) > 0:
                rs.append((t.get("net_pnl", 0) + (t.get("fees", 0) or 0)) / (abs(e - sl) * sz))
        avg_r = sum(rs) / len(rs) if rs else 0
        ok1 = len(rs) >= 100 and avg_r >= 0.10
        checks.append(("Эдж 14д ≥ +0.10R (n≥100)", f"+{avg_r:.2f}R (n={len(rs)})", ok1))
    except Exception as e:
        checks.append(("Эдж 14д", f"err {e}", False))
    # 2. Degradation alerts за неделю
    try:
        h = json.loads(HEALTH.read_text())
        bad = sum(1 for v in h.values() if v.get("pf_below") or v.get("silent_alerted"))
        checks.append(("0 degradation-алертов", f"{bad} активных", bad == 0))
    except Exception:
        checks.append(("0 degradation-алертов", "нет данных", False))
    # 3. Slippage < 0.1R (≈0.08% при SL 0.75%)
    try:
        slips = []
        if EXEC_LOG.exists():
            with open(EXEC_LOG) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    ee, ff = r.get("expected_entry"), r.get("fill_price")
                    if ee and ff:
                        slips.append(abs(ff - ee) / ee * 100)
        med = sorted(slips)[len(slips)//2] if slips else None
        ok = med is not None and med < 0.08
        checks.append(("Slippage < 0.08%", f"n={len(slips)}" + (f", {med:.3f}%" if med else ""), ok))
    except Exception:
        checks.append(("Slippage", "err", False))
    # 4. Fill-rate лимиток (owner-ставки filled/total за 14 дней)
    try:
        st = json.loads(LIMIT_STATE.read_text())
        filled = len(st.get("filled", {}))
        active = len(st.get("active_limits", {}))
        total = filled + active
        fr = filled / total * 100 if total else 0
        ok = total >= 20 and fr >= 40
        checks.append((f"Fill-rate ≥ 40% (n≥20)", f"{filled}/{total} = {fr:.0f}%", ok))
    except Exception:
        checks.append(("Fill-rate", "err", False))
    # 5. MaxDD < 8R за 14 дней
    try:
        trades = _load_trades(14)
        trades.sort(key=lambda t: t.get("timestamp", ""))
        eq = 0.0; peak = 0.0; dd = 0.0
        for t in trades:
            eq += t.get("net_pnl", 0) or 0
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        # в R: средний риск ≈ 25
        dd_r = dd / 25.0
        checks.append(("MaxDD 14д < 8R", f"{dd_r:.1f}R (${dd:+.0f})", dd_r < 8))
    except Exception:
        checks.append(("MaxDD", "err", False))

    passed = sum(1 for _, _, ok in checks if ok)
    icon = lambda ok: "✅" if ok else "❌"
    lines = [f"🚦 <b>Live Readiness Gate</b>  [{passed}/{len(checks)}]\n"]
    for name, val, ok in checks:
        lines.append(f"{icon(ok)} {name}\n     <i>{_esc(val)}</i>")
    if passed == len(checks):
        lines.append("\n🟢 <b>ВСЕ ПОРОГИ ПРОЙДЕНЫ — реал разрешён</b>\nСтарт: $500–1000, риск 0.5%.")
    else:
        left = len(checks) - passed
        lines.append(f"\n🔴 Осталось порогов: {left}. Gate пересчитывается по команде /readiness.")
    update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
