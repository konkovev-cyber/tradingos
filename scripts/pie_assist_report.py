#!/usr/bin/env python3
"""
PIE Live Assist Report — анализ качества рекомендаций.

Запуск:
  python3 scripts/pie_assist_report.py
  python3 scripts/pie_assist_report.py --last 50
"""

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "tradingos_data.db"


def get_assist_log(db: sqlite3.Connection, limit: int = 100):
    """Получить лог LIVE_ASSIST рекомендаций."""
    cursor = db.execute("""
        SELECT * FROM live_assist_log
        ORDER BY id DESC
        LIMIT ?
    """, (limit,))
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def print_report(logs):
    """Вывести отчёт."""
    print()
    print("=" * 80)
    print("  PIE LIVE_ASSIST — RECOMMENDATION QUALITY REPORT")
    print("=" * 80)
    
    if not logs:
        print("\n  No LIVE_ASSIST recommendations yet.")
        print("  Start PIE with --mode live_assist to generate recommendations.")
        print()
        print("=" * 80)
        return
    
    print(f"\n  Total recommendations: {len(logs)}")
    
    # ── Overview ──
    qualities = [l["decision_quality"] for l in logs if l["decision_quality"]]
    if qualities:
        correct = qualities.count("correct")
        early = qualities.count("early")
        wrong = qualities.count("wrong")
        total_q = len(qualities)
        print(f"\n  Decision Quality (after 60min):")
        print(f"    Correct: {correct}/{total_q} ({correct/max(total_q,1)*100:.0f}%)")
        print(f"    Early:   {early}/{total_q} ({early/max(total_q,1)*100:.0f}%)  — had more profit available")
        print(f"    Wrong:   {wrong}/{total_q} ({wrong/max(total_q,1)*100:.0f}%)")
    
    # Missed Opportunity
    missed_values = []
    for log in logs:
        if log["pnl_after_60m"] is not None and log["pnl_at_recommendation"] is not None:
            missed_values.append(log["pnl_after_60m"] - log["pnl_at_recommendation"])
    
    if missed_values:
        avg_missed = sum(missed_values) / len(missed_values)
        total_missed = sum(missed_values)
        print(f"\n  ⭐ Missed Opportunity Analysis:")
        print(f"  " + "-" * 60)
        print(f"    Avg missed opportunity: {avg_missed*100:+.2f}%")
        print(f"    Total opportunity:      {total_missed*100:+.2f}%")
        if avg_missed > 0:
            print(f"    → В среднем PIE закрывал раньше, чем надо. Прибыль продолжала расти.")
        elif avg_missed < 0:
            print(f"    → PIE правильно защищал: позиции уходили в минус.")

    # ── By recommendation type ──
    by_type = {}
    for log in logs:
        rec = log["recommendation"]
        if rec not in by_type:
            by_type[rec] = []
        by_type[rec].append(log)
    
    for rec_type, items in by_type.items():
        print(f"\n  {rec_type}: {len(items)} recommendations")
        print("  " + "-" * 60)
        
        # Stats
        pnls_at_rec = [i["pnl_at_recommendation"] for i in items if i["pnl_at_recommendation"] is not None]
        pnls_15m = [i["pnl_after_15m"] for i in items if i["pnl_after_15m"] is not None]
        pnls_30m = [i["pnl_after_30m"] for i in items if i["pnl_after_30m"] is not None]
        pnls_60m = [i["pnl_after_60m"] for i in items if i["pnl_after_60m"] is not None]
        
        if pnls_at_rec:
            print(f"    Avg PnL at recommendation: {sum(pnls_at_rec)/len(pnls_at_rec)*100:+.2f}%")
        if pnls_15m:
            print(f"    Avg PnL after 15 min:      {sum(pnls_15m)/len(pnls_15m)*100:+.2f}%")
        if pnls_30m:
            print(f"    Avg PnL after 30 min:      {sum(pnls_30m)/len(pnls_30m)*100:+.2f}%")
        if pnls_60m:
            print(f"    Avg PnL after 60 min:      {sum(pnls_60m)/len(pnls_60m)*100:+.2f}%")
        
        # Missed opportunity for this type
        missed_this = []
        for item in items:
            if item["pnl_after_60m"] is not None and item["pnl_at_recommendation"] is not None:
                missed_this.append(item["pnl_after_60m"] - item["pnl_at_recommendation"])
        if missed_this:
            avg_miss = sum(missed_this)/len(missed_this)
            print(f"    Missed opportunity:         {avg_miss*100:+.2f}%")
        
        # Quality breakdown
        qs = [i["decision_quality"] for i in items if i["decision_quality"]]
        if qs:
            print(f"    Quality: correct={qs.count('correct')} early={qs.count('early')} wrong={qs.count('wrong')}")
        
        # Show last 3 examples
        print(f"\n    Last {min(3, len(items))} examples:")
        for item in items[:3]:
            print(f"      {item['symbol']} {item['side']} @ {item['price_at_recommendation']:.4f}")
            print(f"        PnL at rec: {item['pnl_at_recommendation']*100:+.2f}%  "
                  f"15m: {item['pnl_after_15m']*100 if item['pnl_after_15m'] is not None else '?':+.2f}%  "
                  f"30m: {item['pnl_after_30m']*100 if item['pnl_after_30m'] is not None else '?':+.2f}%  "
                  f"60m: {item['pnl_after_60m']*100 if item['pnl_after_60m'] is not None else '?':+.2f}%")
            print(f"        Reason: {item['reason']}")
            if item.get("decision_quality"):
                print(f"        Quality: {item['decision_quality']} — {item.get('quality_notes', '')}")
    
    # MOVE_SL_BE analysis
    if "MOVE_SL_BE" in by_type:
        move_sl = by_type["MOVE_SL_BE"]
        improved = [i for i in move_sl if i["pnl_after_15m"] is not None and i["pnl_after_15m"] > i["pnl_at_recommendation"]]
        worsened = [i for i in move_sl if i["pnl_after_15m"] is not None and i["pnl_after_15m"] < i["pnl_at_recommendation"]]
        
        print(f"\n  MOVE_SL_BE Quality Analysis:")
        print(f"  " + "-" * 60)
        print(f"    Price improved after 15m (should have held): {len(improved)}/{len(move_sl)} ({len(improved)/max(len(move_sl),1)*100:.0f}%)")
        print(f"    Price worsened after 15m (correct protect):  {len(worsened)}/{len(move_sl)} ({len(worsened)/max(len(move_sl),1)*100:.0f}%)")
    
    # TAKE_PARTIAL analysis
    if "TAKE_PARTIAL" in by_type:
        tp = by_type["TAKE_PARTIAL"]
        saved = [i for i in tp if i["pnl_after_60m"] is not None and i["pnl_after_60m"] < i["pnl_at_recommendation"]]
        
        print(f"\n  TAKE_PARTIAL Quality Analysis:")
        print(f"  " + "-" * 60)
        print(f"    Price dropped after 60m (saved profit): {len(saved)}/{len(tp)} ({len(saved)/max(len(tp),1)*100:.0f}%)")
    
    # ── What If Summary ──
    print(f"\n  ═══ WHAT-IF: PIE VS CURRENT SYSTEM ═══")
    print(f"  " + "-" * 60)
    if missed_values:
        print(f"    Если бы PIE управлял на момент рекомендаций:")
        print(f"      Текущий результат:         0.00% (ничего не делали)")
        print(f"      Результат если бы PIE:     {avg_missed*100:+.2f}%")
        print(f"      Разница:                   {avg_missed*100:+.2f}%")
        if avg_missed < 0:
            print(f"      → PIE спас бы {abs(avg_missed)*100:.2f}% на каждой рекомендации")
        else:
            print(f"      → PIE потерял бы {avg_missed*100:.2f}% (слишком рано)")
        print()
        print(f"      Однако текущие позиции ЕЩЁ ОТКРЫТЫ.")
        print(f"      Окончательный результат будет известен после закрытия.")
    
    print()
    print("=" * 80)
    print()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PIE Live Assist Report")
    parser.add_argument("--last", type=int, default=100, help="Last N recommendations")
    parser.add_argument("--db", default=str(DB_PATH), help="Database path")
    
    args = parser.parse_args()
    
    if not Path(args.db).exists():
        print(f"Database not found: {args.db}")
        sys.exit(1)
    
    db = sqlite3.connect(args.db)
    logs = get_assist_log(db, args.last)
    print_report(logs)
    db.close()


if __name__ == "__main__":
    main()
