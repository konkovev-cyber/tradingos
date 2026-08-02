"""
tools/trade_validator.py
Trade Validation Layer — проверяет каждую сделку перед попаданием в аналитику.

Source of Truth: Bybit API (closed-pnl, executions, order history)
Derived data: direction, R_multiple, win/loss — проверяются на каждом CLOSE.

Если сделка не прошла валидацию → DATA_INVALID → не попадает в статистику.
"""
import json, logging
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger("TradeValidator")

TRADE_RESULTS = Path("/root/tradingos/logs/trades/trade_results.jsonl")
VALIDATION_LOG = Path("/root/tradingos/logs/trades/validation_log.jsonl")


@dataclass
class ValidationResult:
    trade_id: str
    symbol: str
    direction: str
    status: str  # VALID | DATA_INVALID
    checks: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)


def validate_trade(trade: dict) -> ValidationResult:
    """Проверить одну сделку на консистентность.
    
    Проверки:
    1. Direction: SELL + exit < entry = profit; BUY + exit > entry = profit
    2. PnL: calculated PnL vs actual gross_pnl (допуск 0.02)
    3. R_multiple: знак R должен совпадать со знаком PnL
    """
    trade_id = trade.get("trade_id", "?")
    symbol = trade.get("symbol", "?")
    direction = trade.get("direction", "?")
    entry = trade.get("entry_price", 0)
    exit_p = trade.get("exit_price", 0)
    size = trade.get("size", 0)
    gross_pnl = trade.get("gross_pnl", 0)
    r_multiple = trade.get("R_multiple", 0)
    
    issues = []
    checks = {}
    
    # ─── Check 1: Direction ────────────────────────────────
    if direction == "SELL":
        expected_profit = exit_p < entry
    elif direction == "BUY":
        expected_profit = exit_p > entry
    else:
        expected_profit = None
        issues.append(f"Unknown direction: {direction}")
    
    actual_profit = gross_pnl > 0
    direction_ok = (expected_profit == actual_profit) if expected_profit is not None else False
    checks["direction"] = "PASS" if direction_ok else "FAIL"
    if not direction_ok:
        issues.append(f"Direction mismatch: {direction} entry={entry} exit={exit_p} "
                      f"expected_profit={expected_profit} actual_profit={actual_profit}")
    
    # ─── Check 2: PnL consistency ─────────────────────────
    if direction == "SELL":
        calc_pnl = (entry - exit_p) * size
    elif direction == "BUY":
        calc_pnl = (exit_p - entry) * size
    else:
        calc_pnl = 0
    
    pnl_diff = abs(calc_pnl - gross_pnl)
    pnl_ok = pnl_diff <= 0.02  # допуск 2 цента
    checks["pnl"] = "PASS" if pnl_ok else "FAIL"
    if not pnl_ok:
        issues.append(f"PnL mismatch: calc={calc_pnl:.6f} actual={gross_pnl:.6f} diff={pnl_diff:.6f}")
    
    # ─── Check 3: R_multiple sign ─────────────────────────
    r_ok = (r_multiple > 0) == (gross_pnl > 0) or abs(gross_pnl) < 0.001
    checks["r_multiple"] = "PASS" if r_ok else "FAIL"
    if not r_ok:
        issues.append(f"R_multiple sign mismatch: R={r_multiple:+.3f} PnL={gross_pnl:+.4f}")
    
    # ─── Status ───────────────────────────────────────────
    status = "VALID" if not issues else "DATA_INVALID"
    
    return ValidationResult(
        trade_id=trade_id,
        symbol=symbol,
        direction=direction,
        status=status,
        checks=checks,
        issues=issues,
    )


def validate_all_trades(trades_file: Path = TRADE_RESULTS) -> list[ValidationResult]:
    """Проверить все сделки в файле."""
    if not trades_file.exists():
        logger.warning(f"Trades file not found: {trades_file}")
        return []
    
    with open(trades_file) as f:
        trades = [json.loads(l) for l in f if l.strip()]
    
    results = []
    for trade in trades:
        result = validate_trade(trade)
        results.append(result)
        
        if result.status == "DATA_INVALID":
            logger.warning(f"❌ {result.symbol} {result.direction}: {'; '.join(result.issues)}")
        else:
            logger.info(f"✅ {result.symbol} {result.direction}: VALID")
    
    return results


def get_valid_trades(trades_file: Path = TRADE_RESULTS) -> list[dict]:
    """Вернуть только валидные сделки."""
    if not trades_file.exists():
        return []
    
    with open(trades_file) as f:
        trades = [json.loads(l) for l in f if l.strip()]
    
    valid = []
    for trade in trades:
        result = validate_trade(trade)
        if result.status == "VALID":
            valid.append(trade)
    
    return valid


def compute_metrics(trades: list[dict]) -> dict:
    """Рассчитать метрики по списку сделок."""
    if not trades:
        return {"error": "no trades"}
    
    total = len(trades)
    wins = [t for t in trades if t.get("net_pnl", 0) > 0]
    losses = [t for t in trades if t.get("net_pnl", 0) <= 0]
    total_pnl = sum(t.get("net_pnl", 0) for t in trades)
    total_fees = sum(t.get("total_fees", 0) for t in trades)
    avg_r = sum(t.get("R_multiple", 0) for t in trades) / max(total, 1)
    avg_hold = sum(t.get("holding_hours", 0) for t in trades) / max(total, 1)
    
    win_sum = sum(t.get("net_pnl", 0) for t in wins)
    loss_sum = abs(sum(t.get("net_pnl", 0) for t in losses))
    
    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / max(total, 1) * 100,
        "gross_pnl": round(sum(t.get("gross_pnl", 0) for t in trades), 4),
        "total_fees": round(total_fees, 4),
        "net_pnl": round(total_pnl, 4),
        "avg_r": round(avg_r, 3),
        "avg_hold_hours": round(avg_hold, 1),
        "profit_factor": round(win_sum / max(loss_sum, 0.0001), 2),
        "avg_win": round(win_sum / max(len(wins), 1), 4),
        "avg_loss": round(loss_sum / max(len(losses), 1), 4),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    print("=== TRADE VALIDATION ===\n")
    results = validate_all_trades()
    
    valid_count = sum(1 for r in results if r.status == "VALID")
    invalid_count = sum(1 for r in results if r.status == "DATA_INVALID")
    print(f"\nValid: {valid_count}")
    print(f"Invalid: {invalid_count}")
    print(f"Total: {len(results)}")
    
    if valid_count > 0:
        valid_trades = get_valid_trades()
        metrics = compute_metrics(valid_trades)
        print(f"\n=== METRICS (VALID TRADES ONLY) ===")
        for k, v in metrics.items():
            print(f"  {k}: {v}")
