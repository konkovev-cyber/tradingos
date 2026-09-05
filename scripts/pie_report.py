#!/usr/bin/env python3
"""
PIE Analytics Report — анализ собранных данных по позициям.

Запуск:
  python scripts/pie_report.py
  python scripts/pie_report.py --last 50
  python scripts/pie_report.py --symbol BTCUSDT
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "tradingos_data.db"


def get_position_events(db: sqlite3.Connection, limit: int = 100):
    """Получить события позиций."""
    cursor = db.execute("""
        SELECT * FROM position_events
        ORDER BY timestamp_utc DESC
        LIMIT ?
    """, (limit,))
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_position_summaries(db: sqlite3.Connection, limit: int = 50):
    """Получить сводки по закрытым позициям."""
    cursor = db.execute("""
        SELECT * FROM position_summary
        ORDER BY closed_at DESC
        LIMIT ?
    """, (limit,))
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def analyze_profit_retracement(summaries):
    """Анализ отдачи прибыли."""
    if not summaries:
        return {}
    
    retracements = [s["profit_retracement_final"] or 0 for s in summaries]
    avg_retrace = sum(retracements) / len(retracements)
    
    # Категории
    low_retrace = [r for r in retracements if r < 0.3]
    mid_retrace = [r for r in retracements if 0.3 <= r < 0.6]
    high_retrace = [r for r in retracements if r >= 0.6]
    
    return {
        "count": len(retracements),
        "avg_retracement": avg_retrace,
        "low_retrace_pct": len(low_retrace) / len(retracements) if retracements else 0,
        "mid_retrace_pct": len(mid_retrace) / len(retracements) if retracements else 0,
        "high_retrace_pct": len(high_retrace) / len(retracements) if retracements else 0,
        "max_retracement": max(retracements) if retracements else 0,
    }


def analyze_mfe_mae(summaries):
    """Анализ MFE/MAE."""
    if not summaries:
        return {}
    
    mfe_values = [s["max_profit_seen"] for s in summaries]
    mae_values = [s["max_loss_seen"] for s in summaries]
    
    # Win/Loss по MFE
    mfe_wins = [m for m in mfe_values if m > 0.01]  # MFE > 1%
    
    return {
        "avg_mfe": sum(mfe_values) / len(mfe_values),
        "avg_mae": sum(mae_values) / len(mae_values),
        "mfe_mae_ratio": (sum(mfe_values) / len(mfe_values)) / max((sum(mae_values) / len(mae_values)), 0.001),
        "mfe_gt_1pct": len(mfe_wins) / len(mfe_values) if mfe_values else 0,
        "max_mfe": max(mfe_values) if mfe_values else 0,
        "max_mae": max(mae_values) if mae_values else 0,
    }


def analyze_timing(summaries):
    """Анализ времени в позиции."""
    if not summaries:
        return {}
    
    times = [s["time_in_position"] or 0 for s in summaries]
    bars = [s["bars_held"] or 0 for s in summaries]
    
    return {
        "avg_time_hours": sum(times) / len(times) / 3600 if times else 0,
        "avg_bars": sum(bars) / len(bars) if bars else 0,
        "max_time_hours": max(times) / 3600 if times else 0,
        "min_time_hours": min(times) / 3600 if times else 0,
    }


def analyze_pce(summaries):
    """
    Profit Capture Efficiency Analysis.

    PCE = реализованная прибыль / MFE
    Показывает, сколько от максимально доступной прибыли мы смогли сохранить.

    100% — оставили всё движение
    50%  — отдали половину
    0%   — прибыль была, но мы её потеряли
    """
    if not summaries:
        return {}

    pce_values = [s.get("profit_capture_efficiency") for s in summaries
                  if s.get("profit_capture_efficiency") is not None]

    if not pce_values:
        return {"message": "No PCE data yet (positions need to close first)"}

    avg_pce = sum(pce_values) / len(pce_values)

    # Распределение
    excellent = [p for p in pce_values if p >= 0.75]  # 75%+ — отлично
    good = [p for p in pce_values if 0.5 <= p < 0.75]  # 50-75% — хорошо
    poor = [p for p in pce_values if 0.25 <= p < 0.5]  # 25-50% — плохо
    critical = [p for p in pce_values if p < 0.25]     # <25% — критично

    return {
        "count": len(pce_values),
        "avg_pce": avg_pce,
        "excellent_pct": len(excellent) / len(pce_values) * 100,
        "good_pct": len(good) / len(pce_values) * 100,
        "poor_pct": len(poor) / len(pce_values) * 100,
        "critical_pct": len(critical) / len(pce_values) * 100,
        "excellent_count": len(excellent),
        "critical_count": len(critical),
    }


def analyze_states(events):
    """Анализ состояний позиций."""
    if not events:
        return {}
    
    states = [e["state"] for e in events]
    state_counts = {}
    for s in states:
        state_counts[s] = state_counts.get(s, 0) + 1
    
    return {
        "total_events": len(events),
        "state_distribution": state_counts,
    }


def analyze_recommendations(events):
    """Анализ рекомендаций."""
    if not events:
        return {}
    
    recs = [e["recommendation"] for e in events]
    rec_counts = {}
    for r in recs:
        rec_counts[r] = rec_counts.get(r, 0) + 1
    
    return {
        "total_recommendations": len(recs),
        "recommendation_distribution": rec_counts,
    }


def print_report(events, summaries):
    """Вывести отчёт."""
    print()
    print("=" * 80)
    print("  PIE v1.1 POSITION INTELLIGENCE REPORT")
    print("=" * 80)
    
    # Общая статистика
    print(f"\n  Total events: {len(events)}")
    print(f"  Total closed positions: {len(summaries)}")
    
    if summaries:
        # PnL
        pnls = [s["final_pnl_pct"] or 0 for s in summaries]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        
        print(f"\n  PnL Summary:")
        print(f"    Win rate: {len(wins)/len(pnls)*100:.1f}%")
        print(f"    Avg PnL: {sum(pnls)/len(pnls)*100:+.2f}%")
        print(f"    Avg Win: {sum(wins)/len(wins)*100:+.2f}%" if wins else "    Avg Win: N/A")
        print(f"    Avg Loss: {sum(losses)/len(losses)*100:+.2f}%" if losses else "    Avg Loss: N/A")
        print(f"    Total PnL: {sum(pnls)*100:+.2f}%")
        
        # Profit Retracement
        retrace = analyze_profit_retracement(summaries)
        print(f"\n  Profit Retracement Analysis:")
        print(f"    Avg retrace: {retrace.get('avg_retracement', 0)*100:.1f}%")
        print(f"    Low (<30%): {retrace.get('low_retrace_pct', 0)*100:.1f}%")
        print(f"    Mid (30-60%): {retrace.get('mid_retrace_pct', 0)*100:.1f}%")
        print(f"    High (>60%): {retrace.get('high_retrace_pct', 0)*100:.1f}%")
        print(f"    Max retrace: {retrace.get('max_retracement', 0)*100:.1f}%")
        
        # MFE/MAE
        mfe_mae = analyze_mfe_mae(summaries)
        print(f"\n  MFE/MAE Analysis:")
        print(f"    Avg MFE: {mfe_mae.get('avg_mfe', 0)*100:+.2f}%")
        print(f"    Avg MAE: {mfe_mae.get('avg_mae', 0)*100:+.2f}%")
        print(f"    MFE/MAE ratio: {mfe_mae.get('mfe_mae_ratio', 0):.2f}")
        print(f"    MFE > 1%: {mfe_mae.get('mfe_gt_1pct', 0)*100:.1f}%")
        print(f"    Max MFE: {mfe_mae.get('max_mfe', 0)*100:+.2f}%")
        print(f"    Max MAE: {mfe_mae.get('max_mae', 0)*100:+.2f}%")
        
        # Timing
        timing = analyze_timing(summaries)
        print(f"\n  Timing Analysis:")
        print(f"    Avg hold time: {timing.get('avg_time_hours', 0):.1f}h")
        print(f"    Avg bars held: {timing.get('avg_bars', 0):.1f}")
        print(f"    Max hold time: {timing.get('max_time_hours', 0):.1f}h")
        print(f"    Min hold time: {timing.get('min_time_hours', 0):.1f}h")
        
        # Profit Capture Efficiency
        pce = analyze_pce(summaries)
        if pce.get("avg_pce") is not None:
            print(f"\n  ★ Profit Capture Efficiency (PCE):")
            print(f"    Avg PCE: {pce.get('avg_pce', 0)*100:.1f}%")
            print(f"    Excellent (75%+): {pce.get('excellent_pct', 0):.0f}% ({pce.get('excellent_count', 0)} trades)")
            print(f"    Good (50-75%):   {pce.get('good_pct', 0):.0f}%")
            print(f"    Poor (25-50%):   {pce.get('poor_pct', 0):.0f}%")
            print(f"    Critical (<25%): {pce.get('critical_pct', 0):.0f}% ({pce.get('critical_count', 0)} trades)")
            print(f"    " + "-" * 40)
            if pce.get('avg_pce', 0) < 0.5:
                print(f"    ⚠️  Вывод: система теряет >50% доступной прибыли!")
                print(f"    Приоритет: защита позиций (MOVE_SL_BE)")
            elif pce.get('avg_pce', 0) < 0.75:
                print(f"    ✅ Средний уровень. Есть куда расти.")
            else:
                print(f"    🟢 Отличный показатель. Система сохраняет большую часть движения.")
        else:
            print(f"\n  Profit Capture Efficiency: {pce.get('message', 'N/A')}")
        
        # Exit Reasons
        exit_reasons = {}
        for s in summaries:
            reason = s.get("exit_reason", "unknown")
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        
        print(f"\n  Exit Reasons:")
        for reason, count in sorted(exit_reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {count} ({count/len(summaries)*100:.1f}%)")
        
        # Last 5 trades
        print(f"\n  Last 5 Closed Positions:")
        for s in summaries[:5]:
            pnl = s.get("final_pnl_pct", 0) or 0
            mfe = s.get("max_profit_seen", 0) or 0
            mae = s.get("max_loss_seen", 0) or 0
            retrace = s.get("profit_retracement_final", 0) or 0
            print(f"    {s['symbol']} {s['side']} entry={s['entry_price']:.1f} "
                  f"pnl={pnl*100:+.2f}% MFE={mfe*100:+.2f}% MAE={mae*100:+.2f}% "
                  f"retrace={retrace*100:.0f}% reason={s.get('exit_reason', '?')}")
    
    # State Analysis
    state_analysis = analyze_states(events)
    if state_analysis.get("state_distribution"):
        print(f"\n  State Distribution:")
        for state, count in sorted(state_analysis["state_distribution"].items(), 
                                   key=lambda x: -x[1]):
            print(f"    {state}: {count}")
    
    # Recommendation Analysis
    rec_analysis = analyze_recommendations(events)
    if rec_analysis.get("recommendation_distribution"):
        print(f"\n  Recommendation Distribution:")
        for rec, count in sorted(rec_analysis["recommendation_distribution"].items(),
                                key=lambda x: -x[1]):
            print(f"    {rec}: {count}")
    
    print()
    print("=" * 80)
    print()


def main():
    parser = argparse.ArgumentParser(description="PIE Analytics Report")
    parser.add_argument("--last", type=int, default=100, help="Last N events/positions")
    parser.add_argument("--symbol", help="Filter by symbol")
    parser.add_argument("--db", default=str(DB_PATH), help="Database path")
    
    args = parser.parse_args()
    
    if not Path(args.db).exists():
        print(f"Database not found: {args.db}")
        sys.exit(1)
    
    db = sqlite3.connect(args.db)
    
    events = get_position_events(db, args.last)
    summaries = get_position_summaries(db, args.last)
    
    if args.symbol:
        events = [e for e in events if e["symbol"] == args.symbol]
        summaries = [s for s in summaries if s["symbol"] == args.symbol]
    
    print_report(events, summaries)
    
    db.close()


if __name__ == "__main__":
    main()
