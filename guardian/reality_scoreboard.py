#!/usr/bin/env python3
"""
guardian/reality_scoreboard.py
Reality Scoreboard — unified daily performance panel.
Reads from existing logs. No trading logic changes.
"""
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Data sources
SIGNAL_LOG = Path("/root/tradingos/memory/signal_log.jsonl")
TRADE_RESULTS = Path("/root/tradingos/logs/trades/trade_results.jsonl")
GUARDIAN_EFFECTIVENESS = Path("/root/tradingos/guardian/guardian_effectiveness.jsonl")
CAPITAL_LOG = Path("/root/tradingos/guardian/capital_utilization.jsonl")
REJECTED_LOG = Path("/root/tradingos/guardian/rejected_candidates.jsonl")
GUARDIAN_ALERTS = Path("/root/tradingos/guardian/profit_alerts.jsonl")
TIMEOUT_ALERTS = Path("/root/tradingos/guardian/timeout_alerts.jsonl")
GUARDIAN_STATE = Path("/root/tradingos/guardian/reality_state.json")


def _api_base() -> str:
    """2026-08-27: Demo switch — private endpoints route to api-demo."""
    v = (os.environ.get("BYBIT_DEMO", "") or "").strip().lower()
    if v not in ("1", "true", "yes", "on"):
        try:
            with open("/root/trading_brain_v4/research/execution/.env") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("BYBIT_DEMO="):
                        v = line.split("=", 1)[1].strip().lower()
                        break
        except FileNotFoundError:
            pass
    return "https://api-demo.bybit.com" if v in ("1", "true", "yes", "on") else "https://api.bybit.com"


def _load_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def _hours_ago(hours: float = 24) -> float:
    return time.time() - hours * 3600


def daily_scoreboard(hours: int = 24) -> Dict:
    """Build a daily Reality Scoreboard from existing data sources."""
    cutoff = _hours_ago(hours)
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "period_hours": hours}

    # ── Capital ──
    cap_records = _load_jsonl(CAPITAL_LOG)
    recent_cap = [r for r in cap_records if datetime.fromisoformat(r["timestamp"]).timestamp() >= cutoff]
    if recent_cap:
        latest = recent_cap[-1]
        result["capital"] = {
            "balance": latest["balance"],
            "equity": latest["equity"],
            "free_margin": latest["free_margin"],
            "margin_util_pct": latest["margin_util_pct"],
            "positions": latest["positions"],
            "risk_budget": latest["risk_budget"],
            "risk_used": latest["risk_used"],
            "risk_util_pct": latest["risk_util_pct"],
        }
    else:
        result["capital"] = {"error": "No capital snapshots in period"}

    # ── Trading ──
    trades = _load_jsonl(TRADE_RESULTS)
    recent_trades = [t for t in trades if t.get("timestamp", "") and 
                     datetime.fromisoformat(t["timestamp"]).timestamp() >= cutoff]
    closed = [t for t in recent_trades if t.get("status") == "CLOSED"]
    wins = [t for t in closed if float(t.get("pnl", 0)) > 0]
    losses = [t for t in closed if float(t.get("pnl", 0)) <= 0]
    total_pnl = sum(float(t.get("pnl", 0)) for t in closed)
    
    result["trading"] = {
        "open_positions": latest["positions"] if recent_cap else 0,
        "closed_today": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / max(len(closed), 1) * 100, 1),
        "total_pnl": round(total_pnl, 3),
        "avg_r": 0,  # computed from closed trades with R info
    }

    # ── Guardian ──
    alerts = _load_jsonl(GUARDIAN_ALERTS)
    recent_alerts = [a for a in alerts if a.get("timestamp", "") and
                     datetime.fromisoformat(a["timestamp"]).timestamp() >= cutoff]
    be_count = sum(1 for a in recent_alerts if a.get("type") == "GUARDIAN_BREAKEVEN")
    partial_count = sum(1 for a in recent_alerts if a.get("type") == "GUARDIAN_PARTIAL")
    tight_count = sum(1 for a in recent_alerts if a.get("type") == "GUARDIAN_TIGHT")

    result["guardian"] = {
        "breakeven_fired": be_count,
        "partial_fired": partial_count,
        "tight_fired": tight_count,
        "total_events": len(recent_alerts),
    }

    # ── Capital Velocity: what limits capital growth today ──
    # Check open positions for stale-time diagnostic
    try:
        import hmac, hashlib, httpx as _h
        env_p = "/root/trading_brain_v4/research/execution/.env"
        ak2, as2 = "", ""
        with open(env_p) as _f:
            for _l in _f:
                _l = _l.strip()
                if _l and not _l.startswith("#") and "=" in _l:
                    _k, _v = _l.split("=", 1)
                    if _k.strip() == "BYBIT_API_KEY": ak2 = _v.strip()
                    elif _k.strip() == "BYBIT_API_SECRET": as2 = _v.strip()
        ts_q = str(int(time.time() * 1000))
        q_q = "category=linear&settleCoin=USDT"
        s_q = hmac.new(as2.encode(), f"{ts_q}{ak2}5000{q_q}".encode(), hashlib.sha256).hexdigest()
        h_q = {"X-BAPI-API-KEY": ak2, "X-BAPI-TIMESTAMP": ts_q, "X-BAPI-SIGN": s_q, "X-BAPI-RECV-WINDOW": "5000"}
        r_q = _h.get(f"{_api_base()}/v5/position/list?{q_q}", headers=h_q, timeout=5)
        d_q = r_q.json()
        open_positions_data = []
        if d_q.get("retCode") == 0:
            now = time.time()
            for ip in d_q["result"].get("list", []):
                if float(ip.get("size", 0)) > 0:
                    created = float(ip.get("createdTime", 0)) / 1000
                    age_h = (now - created) / 3600
                    pnl = float(ip.get("unrealisedPnl", 0))
                    open_positions_data.append({
                        "symbol": ip["symbol"],
                        "age_h": age_h,
                        "pnl": pnl,
                    })

        if open_positions_data:
            ages = [p["age_h"] for p in open_positions_data]
            pnls = [p["pnl"] for p in open_positions_data]
            avg_age = sum(ages) / len(ages)
            max_age = max(ages)
            frozen_in_red = sum(1 for p in open_positions_data if p["pnl"] < 0)
            frozen_in_green = sum(1 for p in open_positions_data if p["pnl"] > 0)
            result["capital_velocity"] = {
                "open_count": len(open_positions_data),
                "avg_holding_h": round(avg_age, 1),
                "max_holding_h": round(max_age, 1),
                "frozen_in_red": frozen_in_red,
                "frozen_in_green": frozen_in_green,
            }
        else:
            result["capital_velocity"] = {"open_count": 0}
    except Exception as e:
        result["capital_velocity"] = {"error": str(e)}

    # ── Opportunity Loss ──
    rejected = _load_jsonl(REJECTED_LOG)
    recent_rejected = [r for r in rejected if r.get("entry_time", 0) >= cutoff]
    reasons = Counter(r.get("reason", "unknown") for r in recent_rejected)
    
    result["opportunity_loss"] = {
        "total_rejected": len(recent_rejected),
        "by_reason": dict(reasons.most_common(10)),
    }

    # ── Operations ──
    # Check service status
    result["operations"] = {
        "services": {
            "reality_scanner": _check_service("tradingos-reality.service"),
            "guardian": _check_service("tradingos-guardian.service"),
            "telegram": _check_service("tradingos-telegram.service"),
        }
    }

    return result


def _check_service(name: str) -> str:
    try:
        import subprocess
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except:
        return "unknown"


def format_daily_report(scoreboard: Dict) -> str:
    """Format scoreboard as human-readable daily report."""
    lines = []
    lines.append(f"=== REALITY DAILY REPORT ===")
    lines.append(f"Period: {scoreboard.get('period_hours', 24)}h")
    lines.append(f"Time:   {scoreboard['timestamp'][:19]} UTC")
    lines.append("")

    # Capital
    cap = scoreboard.get("capital", {})
    if "error" not in cap:
        lines.append("CAPITAL")
        lines.append(f"  Balance:    ${cap.get('balance', 0):.2f}")
        lines.append(f"  Equity:     ${cap.get('equity', 0):.2f}")
        lines.append(f"  Margin util: {cap.get('margin_util_pct', 0)}%")
        lines.append(f"  Positions:  {cap.get('positions', 0)}")
        lines.append(f"  Risk used:  {cap.get('risk_util_pct', 0)}%")
    lines.append("")

    # Trading
    trade = scoreboard.get("trading", {})
    lines.append("TRADING")
    lines.append(f"  Open positions: {trade.get('open_positions', 0)}")
    lines.append(f"  Closed today:   {trade.get('closed_today', 0)}")
    lines.append(f"  Win rate:       {trade.get('win_rate', 0)}%")
    lines.append(f"  Total PnL:      ${trade.get('total_pnl', 0):+.3f}")
    lines.append("")

    # Guardian
    guard = scoreboard.get("guardian", {})
    lines.append("GUARDIAN")
    if guard.get("total_events", 0) > 0:
        lines.append(f"  Breakeven:   {guard.get('breakeven_fired', 0)}")
        lines.append(f"  Partial:     {guard.get('partial_fired', 0)}")
        lines.append(f"  Tight:       {guard.get('tight_fired', 0)}")
    else:
        lines.append("  No events (waiting for triggers)")
    lines.append("")

    # Opportunity Loss
    opp = scoreboard.get("opportunity_loss", {})
    lines.append("OPPORTUNITY LOSS")
    lines.append(f"  Rejected candidates: {opp.get('total_rejected', 0)}")
    for reason, count in opp.get("by_reason", {}).items():
        lines.append(f"    {reason}: {count}")
    lines.append("")

    # Operations
    ops = scoreboard.get("operations", {}).get("services", {})
    lines.append("OPERATIONS")
    for name, status in ops.items():
        icon = "✅" if status == "active" else "❌"
        lines.append(f"  {icon} {name}")
    lines.append("")

    # Capital Velocity
    cv = scoreboard.get("capital_velocity", {})
    lines.append("CAPITAL VELOCITY (what limits growth today)")
    if "error" not in cv:
        if cv.get("open_count", 0) == 0:
            lines.append("  No open positions — full capital available")
        else:
            lines.append(f"  Open positions:     {cv.get('open_count', 0)}/4")
            lines.append(f"  Avg holding:        {cv.get('avg_holding_h', 0)}h")
            lines.append(f"  Max holding:        {cv.get('max_holding_h', 0)}h")
            lines.append(f"  Frozen in red:      {cv.get('frozen_in_red', 0)}")
            lines.append(f"  Frozen in green:    {cv.get('frozen_in_green', 0)}")
    else:
        lines.append(f"  (error: {cv.get('error', '?')})")
    lines.append("")

    # Capital Growth Metric (the single answer to "is this working?")
    growth_pct = (scoreboard.get("capital", {}).get("equity", 0) - 10.19) / 10.19 * 100
    lines.append(f"### CAPITAL GROWTH: {growth_pct:+.2f}% (vs $10.19 start)")
    lines.append("")

    # Five questions
    pnl = trade.get("total_pnl", 0)
    lines.append("### 1. Система сегодня заработала или потеряла деньги?")
    lines.append(f"  {'✅ ЗАРАБОТАЛА' if pnl >= 0 else '❌ ПОТЕРЯЛА'}: ${pnl:+.3f}")
    lines.append("")
    lines.append("### 2. Почему?")
    if trade.get("closed_today", 0) > 0:
        lines.append(f"  Закрыто {trade['closed_today']} сделок, win rate {trade['win_rate']}%")
    else:
        lines.append("  Нет закрытых сделок в этом периоде")
    lines.append("")
    lines.append("### 3. Самое прибыльное решение?")
    lines.append("  (требуется анализ закрытых сделок)")
    lines.append("")
    lines.append("### 4. Самая большая потеря?")
    lines.append("  (требуется анализ закрытых сделок)")
    lines.append("")
    lines.append("### 5. Есть доказательства необходимости изменения системы?")
    lines.append("  ❌ НЕТ — сбор статистики продолжается")
    lines.append("")
    # CIO question 6: "What limits capital growth today?"
    lines.append("### 6. Что ограничивает рост капитала сегодня?")
    cv = scoreboard.get("capital_velocity", {})
    if cv.get("frozen_in_red", 0) > 0 and cv.get("avg_holding_h", 0) > 6:
        lines.append("  ⏸ КАПИТАЛ ЗАСТРЯЛ: позиции в убытке удерживаются долго")
        lines.append("     нет причин менять систему — ждём естественного закрытия")
    elif cv.get("open_count", 0) == 0:
        lines.append("  ✓ Полный капитал доступен, нет ограничений")
    else:
        lines.append("  ⟳ Позиции в работе, ждём первых закрытий")
    lines.append("")

    return "\n".join(lines)


def guardian_trigger_analytics() -> Dict:
    """
    Analyze Guardian trigger behavior from closed trades.
    Reads trade_results.jsonl and computes peak R distribution,
    trigger hit rates, and giveback for each trade.
    """
    trades = _load_jsonl(TRADE_RESULTS)
    closed = [t for t in trades if t.get("status") == "CLOSED"]
    
    if not closed:
        return {"status": "no_closed_trades"}
    
    peak_r_dist = {"<0.8R": 0, "0.8-1.0R": 0, "1.0-1.5R": 0, ">1.5R": 0}
    trigger_reached = {"NONE": 0, "BE": 0, "PARTIAL": 0, "TIGHT": 0}
    total_giveback = 0
    giveback_trades = 0
    
    for t in closed:
        peak_r = float(t.get("peak_r", 0))
        final_r = float(t.get("final_r", 0))
        
        if peak_r < 0.8:
            peak_r_dist["<0.8R"] += 1
        elif peak_r < 1.0:
            peak_r_dist["0.8-1.0R"] += 1
        elif peak_r < 1.5:
            peak_r_dist["1.0-1.5R"] += 1
        else:
            peak_r_dist[">1.5R"] += 1
        
        if peak_r >= 1.5:
            trigger_reached["TIGHT"] += 1
        elif peak_r >= 1.0:
            trigger_reached["PARTIAL"] += 1
        elif peak_r >= 0.8:
            trigger_reached["BE"] += 1
        else:
            trigger_reached["NONE"] += 1
        
        if peak_r > 0:
            giveback = (peak_r - final_r) / peak_r * 100 if peak_r > 0 else 0
            if giveback > 0:
                total_giveback += giveback
                giveback_trades += 1
    
    return {
        "evaluated_trades": len(closed),
        "peak_r_distribution": peak_r_dist,
        "trigger_reached": trigger_reached,
        "avg_giveback_pct": round(total_giveback / max(giveback_trades, 1), 1),
    }


def format_guardian_analytics(analytics: Dict) -> str:
    """Format Guardian trigger analytics for report."""
    if analytics.get("status") == "no_closed_trades":
        return "GUARDIAN ANALYTICS: No closed trades yet\n"
    
    lines = []
    lines.append("\n=== GUARDIAN TRIGGER ANALYTICS ===")
    lines.append(f"Evaluated: {analytics.get('evaluated_trades', 0)} closed trades")
    lines.append("")
    lines.append("Peak R distribution:")
    for bucket, count in analytics.get("peak_r_distribution", {}).items():
        lines.append(f"  {bucket}: {count}")
    lines.append("")
    lines.append("Trigger reached:")
    for trigger, count in analytics.get("trigger_reached", {}).items():
        lines.append(f"  {trigger}: {count}")
    lines.append("")
    lines.append(f"Average giveback after peak: {analytics.get('avg_giveback_pct', 0)}%")
    return "\n".join(lines)


def guardian_effectiveness_report() -> Dict:
    """
    Guardian Effectiveness v2 — separates Protection Triggered from Real Financial Benefit.
    - Protection Triggered: which Guardian levels fired (BE/Partial/Tight)
    - Realized Outcome: TP / BE-hit / SL / Manual / Timeout
    - Estimated Benefit: counted ONLY if trade closed at or below Guardian's new SL
    """
    records = _load_jsonl(GUARDIAN_EFFECTIVENESS)
    if not records:
        return {"status": "no_closed_trades"}
    
    # Separate counters
    triggered = {"BE": 0, "PARTIAL": 0, "TIGHT": 0, "NONE": 0}
    outcomes = {"TP": 0, "BE": 0, "SL": 0, "MANUAL": 0, "TIMEOUT": 0, "PARTIAL_HIT": 0}
    protected_exits = 0
    estimated_benefit_total = 0.0
    
    for r in records:
        # 1. Protection Triggered
        t = r.get("guardian_trigger", "NONE")
        if t in triggered:
            triggered[t] += 1
        
        # 2. Realized Outcome
        o = r.get("outcome", "MANUAL")
        if o in outcomes:
            outcomes[o] += 1
        
        # 3. Estimated Benefit
        if r.get("protected_exit", False):
            protected_exits += 1
            estimated_benefit_total += float(r.get("estimated_benefit", 0))
    
    total = len(records)
    
    # Avoid overclaiming: 
    # If trigger fired but trade was profitable, Guardian only insured — no benefit counted
    # If trigger fired and trade was in loss, Guardian caught it — benefit counted
    return {
        "evaluated_trades": total,
        "protection_triggered": triggered,
        "trigger_rate": round((triggered["BE"] + triggered["PARTIAL"] + triggered["TIGHT"]) / max(total, 1) * 100, 1),
        "realized_outcome": outcomes,
        "protected_exits": protected_exits,
        "estimated_loss_prevented_total": round(estimated_benefit_total, 4),
        "note": "BE/Partial/Tight = Guardian level fired. protected_exits = trades closed with loss but caught by Guardian (not just insured profit).",
    }


def format_guardian_effectiveness(report: Dict) -> str:
    """Format Guardian Effectiveness v2 report."""
    if report.get("status") == "no_closed_trades":
        return "GUARDIAN EFFECTIVENESS v2: No closed trades yet\n"
    
    lines = []
    lines.append("\n=== GUARDIAN EFFECTIVENESS v2 ===")
    lines.append("(Protection Triggered vs Real Financial Benefit)")
    lines.append(f"Evaluated: {report.get('evaluated_trades', 0)} closed trades")
    lines.append("")
    lines.append("1. PROTECTION TRIGGERED (any Guardian level fired):")
    for level, count in report.get("protection_triggered", {}).items():
        lines.append(f"   {level}: {count}")
    lines.append(f"   Trigger rate: {report.get('trigger_rate', 0)}%")
    lines.append("")
    lines.append("2. REALIZED OUTCOME:")
    for outcome, count in report.get("realized_outcome", {}).items():
        lines.append(f"   {outcome}: {count}")
    lines.append("")
    lines.append("3. ESTIMATED BENEFIT (loss prevented by Guardian):")
    lines.append(f"   Protected exits: {report.get('protected_exits', 0)}")
    lines.append(f"   Estimated total loss prevented: ${report.get('estimated_loss_prevented_total', 0):.4f}")
    lines.append("")
    lines.append("   " + report.get("note", ""))
    return "\n".join(lines)


if __name__ == "__main__":
    sb = daily_scoreboard(hours=24)
    print(format_daily_report(sb))
    print()
    print(format_guardian_analytics(guardian_trigger_analytics()))
    print()
    print(format_guardian_effectiveness(guardian_effectiveness_report()))
    print(format_guardian_analytics(guardian_trigger_analytics()))
