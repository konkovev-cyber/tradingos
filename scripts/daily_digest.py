#!/usr/bin/env python3
"""Ежедневный Telegram-дайджест TradingOS (09:00 МСК).

Одно сообщение: NET за сутки по контурам, owner-квота, night_ban-блокировки,
degradation-статус, серия лузов. Данные — только из логов, торговлю не трогает.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
TRADES = ROOT / "logs/trades/trade_results.jsonl"
BLOCKED = ROOT / "logs/live/long_limit_ledger.jsonl"
LIMIT_STATE = ROOT / "operations/auto_limit_state.json"
HEALTH = ROOT / "operations/contour_health.json"
DAILY_LOSS_PCT = 1.5  # соответствует max_daily_loss_pct


def _load(path, days=1):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = []
    if not Path(path).exists():
        return rows
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts_raw = str(d.get("ts") or d.get("timestamp") or "")
            try:
                dt = datetime.fromisoformat(ts_raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if dt >= cutoff:
                rows.append(d)
    return rows


def _equity() -> float:
    try:
        st = json.loads((ROOT / "operations/deposit_guard_state.json").read_text())
        return float(st.get("last_equity") or 0)
    except Exception:
        return 0.0


def build_digest() -> str:
    now = datetime.now(timezone.utc)
    trades = _load(TRADES, days=1)
    #昨晚 = сутки с последней полуночи UTC? Берём последние 24ч.
    by_contour = defaultdict(lambda: [0, 0.0])
    wins = 0
    for t in trades:
        c = t.get("contour") or "unknown"
        pnl = float(t.get("net_pnl") or 0)
        by_contour[c][0] += 1
        by_contour[c][1] += pnl
        if pnl > 0:
            wins += 1
    net = sum(v[1] for v in by_contour.values())
    n_total = sum(v[0] for v in by_contour.values())

    lines = [f"☀️ <b>TradingOS — дайджест за сутки</b> ({now.strftime('%d.%m %H:%M')} UTC)\n"]
    eq = _equity()
    if eq:
        lines.append(f"Депо (demo): ${eq:,.0f}")
    lines.append(f"NET 24ч: <b>{net:+,.2f}$</b> | сделок {n_total} | WR {wins/max(n_total,1)*100:.0f}%\n")
    if by_contour:
        lines.append("<b>По контурам:</b>")
        for c, (n, pnl) in sorted(by_contour.items(), key=lambda x: -x[1][1]):
            emoji = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")
            lines.append(f"  {emoji} {c}: {n} сд, {pnl:+,.2f}$")
    else:
        lines.append("Сделок за сутки нет.")
    # owner-квота
    try:
        st = json.loads(LIMIT_STATE.read_text())
        owner_risk = sum(
            abs(l.get("l1_price", 0) - l.get("sl", 0)) * float(l.get("qty", 0) or 0)
            for l in st.get("active_limits", {}).values()
            if l.get("owner_bet") and l.get("sl") and l.get("l1_price"))
        n_owner = sum(1 for l in st.get("active_limits", {}).values() if l.get("owner_bet"))
        cap = 500.0
        try:
            sys.path.insert(0, str(ROOT))
            from telegram_control.bet_wizard import OWNER_RISK_CAP
            cap = OWNER_RISK_CAP
        except Exception:
            pass
        lines.append(f"\n🎯 Owner-ставки: {n_owner} шт | риск ${owner_risk:,.0f} / ${cap:,.0f}")
        if owner_risk > cap * 0.8:
            lines.append("  ⚠️ близко к квоте")
    except Exception:
        pass
    # night_ban / blocked
    try:
        blocked = [r for r in _load(BLOCKED, days=1) if r.get("event") == "RISK_BLOCKED"]
        if blocked:
            lines.append(f"🛑 Заблокировано страховками: {len(blocked)}")
            for b in blocked[:3]:
                lines.append(f"   {b.get('symbol')} {b.get('note','')[:40]}")
    except Exception:
        pass
    # degradation
    try:
        h = json.loads(HEALTH.read_text())
        alerts = [(c, v) for c, v in h.items() if v.get("pf_below") or v.get("silent_alerted")]
        if alerts:
            lines.append("🩺 Деградация: " + ", ".join(c for c, _ in alerts))
        else:
            lines.append("🩺 Деградация: контуры здоровы")
    except Exception:
        pass
    # серия лузов
    if trades:
        trades_sorted = sorted(trades, key=lambda t: t.get("timestamp", ""))
        cur = 0
        for t in trades_sorted:
            cur = cur + 1 if (t.get("net_pnl", 0) or 0) < 0 else 0
        if cur >= 4:
            lines.append(f"⚠️ Серия лузов: {cur} подряд — adaptive sizing уже режет риск")
    # дневной стоп-чек
    eq = eq or 0
    if eq and net < 0 and abs(net) / eq * 100 >= DAILY_LOSS_PCT * 0.7:
        lines.append(f"🚨 Дневная просадка {abs(net)/eq*100:.1f}% — близко к стопу {DAILY_LOSS_PCT}%")
    return "\n".join(lines)


def _send(text: str):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("TG creds missing:\n" + text)
        return
    import httpx
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                         "disable_web_page_preview": True}, timeout=15)
    except Exception as e:
        print(f"send failed: {e}\n{text}")


if __name__ == "__main__":
    _send(build_digest())
    print("digest sent")
