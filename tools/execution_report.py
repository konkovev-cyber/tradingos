"""
tools/execution_report.py
Reality Execution Report — разделяет Signal PnL vs Fee PnL vs Slippage PnL.

Запуск:
    python3 /root/tradingos/tools/execution_report.py

Вывод:
    SIGNAL PERFORMANCE
    EXECUTION PERFORMANCE
    REAL RESULT
    CLASSIFICATION BREAKDOWN
"""
import json, sys
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.trade_validator import validate_trade, compute_metrics

TRADE_FILE = Path("/root/tradingos/logs/trades/trade_results.jsonl")


def load_trades():
    if not TRADE_FILE.exists():
        print(f"File not found: {TRADE_FILE}")
        return []
    with open(TRADE_FILE) as f:
        return [json.loads(l) for l in f if l.strip()]


def classify_trade(trade: dict) -> str:
    """Классифицировать сделку по signal_class или вычислить."""
    sc = trade.get("signal_class", "")
    if sc:
        return sc
    
    # Fallback: вычислить
    direction = trade.get("direction", trade.get("side", ""))
    entry = trade.get("entry_price", trade.get("entry", 0))
    exit_p = trade.get("exit_price", trade.get("close_price", 0))
    gross = trade.get("gross_pnl", trade.get("realized_pnl", 0))
    
    if direction in ("Sell", "SELL"):
        signal_win = exit_p < entry
    elif direction in ("Buy", "BUY"):
        signal_win = exit_p > entry
    else:
        return "UNKNOWN"
    
    if signal_win and gross > 0:
        return "SIGNAL_WIN_EXEC_WIN"
    elif signal_win and gross <= 0:
        return "SIGNAL_WIN_EXEC_LOSS"
    else:
        return "SIGNAL_LOSS_EXEC_LOSS"


def run_report():
    trades = load_trades()
    if not trades:
        print("No trades found")
        return
    
    # Validate
    valid = []
    invalid = []
    for t in trades:
        result = validate_trade(t)
        if result.status == "VALID":
            valid.append(t)
        else:
            invalid.append(t)
    
    print("=" * 60)
    print("  TRADINGOS EXECUTION REPORT")
    print("=" * 60)
    print()
    print(f"Trades analyzed: {len(trades)}")
    print(f"  Valid: {len(valid)}")
    print(f"  Invalid: {len(invalid)}")
    print()
    
    # ─── SIGNAL PERFORMANCE ────────────────────────────────
    print("─" * 40)
    print("  SIGNAL PERFORMANCE")
    print("─" * 40)
    
    signal_wins = sum(1 for t in valid if classify_trade(t).startswith("SIGNAL_WIN"))
    print(f"Signal wins:  {signal_wins}/{len(valid)}")
    print(f"Signal win rate: {signal_wins/max(len(valid),1)*100:.0f}%")
    print()
    
    # ─── EXECUTION PERFORMANCE ─────────────────────────────
    print("─" * 40)
    print("  EXECUTION PERFORMANCE")
    print("─" * 40)
    
    classes = Counter(classify_trade(t) for t in valid)
    for cls in ["SIGNAL_WIN_EXEC_WIN", "SIGNAL_WIN_EXEC_LOSS", "SIGNAL_LOSS_EXEC_LOSS"]:
        count = classes.get(cls, 0)
        print(f"  {cls}: {count}")
    
    exec_loss_from_fees = sum(1 for t in valid if classify_trade(t) == "SIGNAL_WIN_EXEC_LOSS")
    print(f"\nExecution loss from fees/slippage: {exec_loss_from_fees}")
    print()
    
    # ─── REAL RESULT ───────────────────────────────────────
    print("─" * 40)
    print("  REAL RESULT")
    print("─" * 40)
    
    metrics = compute_metrics(valid)
    for k, v in metrics.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}" if "pnl" in k.lower() or "fee" in k.lower() else f"  {k}: {v:.2f}")
        else:
            print(f"  {k}: {v}")
    print()
    
    # ─── CLASSIFICATION BREAKDOWN ──────────────────────────
    print("─" * 40)
    print("  CLASSIFICATION BREAKDOWN")
    print("─" * 40)
    
    for cls in ["SIGNAL_WIN_EXEC_WIN", "SIGNAL_WIN_EXEC_LOSS", "SIGNAL_LOSS_EXEC_LOSS"]:
        subset = [t for t in valid if classify_trade(t) == cls]
        if not subset:
            continue
        total_pnl = sum(t.get("net_pnl", t.get("realized_pnl", 0)) for t in subset)
        total_fees = sum(t.get("total_fees", t.get("fees", 0)) for t in subset)
        total_slippage = sum(t.get("slippage_cost", 0) for t in subset)
        print(f"\n  {cls} ({len(subset)} trades):")
        print(f"    Net PnL: ${total_pnl:.4f}")
        print(f"    Fees: ${total_fees:.4f}")
        print(f"    Slippage: ${total_slippage:.4f}")
        for t in subset:
            sym = t.get("symbol", "?")
            pnl = t.get("net_pnl", t.get("realized_pnl", 0))
            print(f"      {sym}: ${pnl:.4f}")
    print()
    
    # ─── CONCLUSION ────────────────────────────────────────
    print("=" * 60)
    print("  CONCLUSION")
    print("=" * 60)
    print()
    
    if signal_wins / max(len(valid), 1) > 0.5:
        print("  ✅ Signal has potential (win rate > 50%)")
    else:
        print("  ⚠️  Signal win rate below 50%")
    
    if exec_loss_from_fees > 0:
        print(f"  ⚠️  {exec_loss_from_fees} trades lost to execution (fees/slippage)")
    
    if metrics.get("profit_factor", 0) > 1.0:
        print("  ✅ Net PF > 1.0 — strategy is profitable")
    else:
        print(f"  ❌ Net PF {metrics.get('profit_factor', 0):.2f} — strategy not yet profitable")
    
    print()
    print(f"  Verdict: {'CONTINUE' if len(valid) < 30 else 'REVIEW'}")
    print()


if __name__ == "__main__":
    run_report()
