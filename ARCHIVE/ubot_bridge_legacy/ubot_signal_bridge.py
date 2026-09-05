#!/usr/bin/env python3
"""
ubot_signal_bridge.py — мост от uBot_bingx Signal → TradingOS decision.json.

Архитектура:
  uBot_bingx (Signal Provider)
       ↓
  ubot_signal_bridge.py
       ↓
  decision.json (TradingOS schema)
       ↓
  Executor Hardened → Approval → Guardian → Bybit

Режимы:
  SHADOW  — режим по умолчанию. Пишет decision.json, НЕ вызывает Executor.
  LIVE    — после ручного подтверждения.

Usage:
    python3 ubot_signal_bridge.py              # SHADOW mode
    python3 ubot_signal_bridge.py --mode live  # LIVE mode
    python3 ubot_signal_bridge.py --status     # последний сигнал + статус
"""
from __future__ import annotations

import json
import uuid
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Пути ──────────────────────────────────────────────────────────────────────
UBOT_DATA_DIR = Path("/opt/ubot_bingx/data")
ENTRY_LOG = UBOT_DATA_DIR / "entry_snapshot.jsonl"
PROCESSED_LOG = UBOT_DATA_DIR / "processed_signals.jsonl"
DECISION_OUT = Path("/root/trading_brain_v4/research/execution/decision.json")
DECISION_ARCHIVE = Path("/root/tradingos/bridges/decisions")

# Конфигурация размера позиции (SHADOW: минимальный безопасный размер)
DEFAULT_USDT_AMOUNT = 50.0  # 50 USDT на сигнал в SHADOW


# ── Загрузка lot_sizes из uBot_bingx ─────────────────────────────────────────
sys.path.insert(0, "/opt/ubot_bingx")
try:
    from core.lot_sizes import calculate_qty, LOT_SIZES
except ImportError:
    calculate_qty = None
    LOT_SIZES = {}


def normalize_symbol(sym: str) -> str:
    """Привести к TradingOS формату: DOGE-USDT → DOGEUSDT."""
    return sym.replace("-", "").upper()


def load_entries() -> list[dict]:
    """Загрузить все entry-сигналы, самые свежие в конце."""
    if not ENTRY_LOG.exists():
        return []
    entries = []
    for line in ENTRY_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def load_processed() -> set[str]:
    """Загрузить ID уже обработанных сигналов."""
    if not PROCESSED_LOG.exists():
        return set()
    processed = set()
    for line in PROCESSED_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            processed.add(entry.get("signal_id", ""))
        except json.JSONDecodeError:
            continue
    return processed


def make_signal_id(entry: dict) -> str:
    """Уникальный ID сигнала: symbol + timestamp + side."""
    ts = entry.get("timestamp", "")
    sym = entry.get("symbol", "")
    side = entry.get("side", "")
    return f"{sym}|{side}|{ts}"


def entry_to_decision(entry: dict, qty: float, mode: str = "SHADOW") -> dict:
    """Конвертировать entry-сигнал в TradingOS decision.json.

    Поля uBot_bingx:
        symbol: "DOGE-USDT"
        side: "BUY" | "SELL"
        entry: float (цена входа)
        sl: float (stop loss)
        tp: float (take profit)
        score: float (0-100)
        strategy: str
        tradeability: float
        timestamp: str

    Поля TradingOS decision.json:
        trace_id, decision_id, event_id, symbol, direction, quantity,
        entry_price, stop_loss, take_profit, action, source, confidence, reason, mode
    """
    trace_id = str(uuid.uuid4())
    decision_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())

    symbol = normalize_symbol(entry.get("symbol", ""))
    direction = entry.get("side", "BUY").upper()
    entry_price = float(entry.get("entry", 0.0))
    sl = float(entry.get("sl", 0.0)) or None
    tp = float(entry.get("tp", 0.0)) or None

    # Безопасное значение quantity
    if qty <= 0:
        qty = 10.0

    # Confidence: score из uBot_bingx (0-100) → 0.0-1.0
    score = float(entry.get("score", 0.0))
    confidence = round(score / 100.0, 4)

    # Reason: сбор информации о сигнале
    strategy = entry.get("strategy", "unknown")
    regime = entry.get("regime", "unknown")
    tradeability = entry.get("tradeability", 0.0)
    timestamp = entry.get("timestamp", "")
    reasons = [
        f"strategy={strategy}",
        f"regime={regime}",
        f"tradeability={tradeability}",
    ]

    return {
        "trace_id": trace_id,
        "decision_id": decision_id,
        "event_id": event_id,
        "symbol": symbol,
        "direction": direction,
        "quantity": qty,
        "entry_price": entry_price,
        "stop_loss": sl,
        "take_profit": tp,
        "action": "OPEN_POSITION",
        "source": "ubot_bingx_bridge",
        "confidence": confidence,
        "reason": "; ".join(reasons),
        "mode": mode,
        "_meta": {
            "original_symbol": entry.get("symbol", ""),
            "strategy": strategy,
            "score": score,
            "tradeability": tradeability,
            "regime": regime,
            "signal_timestamp": timestamp,
            "bridge_version": "1.0",
            "bridge_timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def get_quantity(symbol: str, price: float) -> float:
    """Рассчитать количество по uBot_bingx lot_sizes или fallback."""
    if calculate_qty is not None:
        try:
            return calculate_qty(symbol, price, DEFAULT_USDT_AMOUNT)
        except Exception:
            pass
    return DEFAULT_USDT_AMOUNT / max(price, 0.0001)


def write_decision(decision: dict) -> Path:
    """Записать decision.json для Executor Hardened."""
    DECISION_OUT.parent.mkdir(parents=True, exist_ok=True)
    DECISION_OUT.write_text(json.dumps(decision, indent=2, ensure_ascii=False))
    return DECISION_OUT


def archive_decision(decision: dict) -> Path:
    """Архивировать копию решения."""
    DECISION_ARCHIVE.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sym = decision.get("symbol", "unknown")
    path = DECISION_ARCHIVE / f"decision_{ts}_{sym}.json"
    path.write_text(json.dumps(decision, indent=2, ensure_ascii=False))
    return path


def mark_processed(entry: dict, decision: dict):
    """Записать в лог обработанных сигналов."""
    record = {
        "signal_id": make_signal_id(entry),
        "decision_id": decision["decision_id"],
        "trace_id": decision["trace_id"],
        "symbol": entry.get("symbol", ""),
        "side": entry.get("side", ""),
        "timestamp": entry.get("timestamp", ""),
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "mode": decision.get("mode", "SHADOW"),
    }
    with open(PROCESSED_LOG, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_latest_unprocessed(entries: list[dict], processed: set[str]) -> dict | None:
    """Найти последний (самый новый) необработанный сигнал."""
    for entry in reversed(entries):
        if make_signal_id(entry) not in processed:
            return entry
    return None


def run_bridge(mode: str = "SHADOW") -> dict:
    """Основной цикл bridge."""
    entries = load_entries()
    if not entries:
        return {"status": "NO_SIGNALS", "reason": "entry_snapshot.jsonl пуст или отсутствует"}

    processed = load_processed()
    entry = find_latest_unprocessed(entries, processed)

    if entry is None:
        return {"status": "NO_NEW_SIGNALS", "reason": "все сигналы уже обработаны"}

    symbol_normalized = normalize_symbol(entry.get("symbol", ""))
    entry_price = float(entry.get("entry", 0.0))

    qty = get_quantity(symbol_normalized, entry_price)

    decision = entry_to_decision(entry, qty, mode=mode)

    decision_path = write_decision(decision)
    archive_path = archive_decision(decision)
    mark_processed(entry, decision)

    return {
        "status": "OK",
        "mode": mode,
        "signal_id": make_signal_id(entry),
        "symbol": symbol_normalized,
        "direction": decision["direction"],
        "quantity": qty,
        "entry_price": entry_price,
        "stop_loss": decision["stop_loss"],
        "take_profit": decision["take_profit"],
        "confidence": decision["confidence"],
        "source": entry.get("strategy", "unknown"),
        "decision_path": str(decision_path),
        "archive_path": str(archive_path),
    }


def show_status() -> dict:
    """Показать последний сигнал и статус."""
    entries = load_entries()
    processed = load_processed()
    last = entries[-1] if entries else None

    report = {
        "bridge": "ubot_signal_bridge",
        "version": "1.0",
        "total_entries": len(entries),
        "processed": len(processed),
        "pending": len(entries) - len(processed),
        "last_signal": None,
        "processed_ids": sorted(processed),
    }

    if last:
        sid = make_signal_id(last)
        report["last_signal"] = {
            "signal_id": sid,
            "symbol": last.get("symbol"),
            "side": last.get("side"),
            "entry": last.get("entry"),
            "sl": last.get("sl"),
            "tp": last.get("tp"),
            "strategy": last.get("strategy"),
            "score": last.get("score"),
            "timestamp": last.get("timestamp"),
            "already_processed": sid in processed,
        }

    return report


def main():
    import argparse

    parser = argparse.ArgumentParser(description="uBot_bingx → TradingOS Signal Bridge")
    parser.add_argument("--mode", choices=["shadow", "live"], default="shadow",
                        help="SHADOW (только запись) или LIVE (вызов Executor)")
    parser.add_argument("--status", action="store_true", help="показать статус")

    args = parser.parse_args()

    if args.status:
        report = show_status()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    mode = "SHADOW" if args.mode == "shadow" else "LIVE"
    result = run_bridge(mode)

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if result.get("status") == "OK":
        print(f"\n✅ Signal bridge: {result['symbol']} {result['direction']} @ {result['entry_price']}")
        print(f"   Mode: {result['mode']} | Qty: {result['quantity']} | SL: {result['stop_loss']} | TP: {result['take_profit']}")
        print(f"   Decision: {result['decision_path']}")
        print(f"   Archive:  {result['archive_path']}")
    else:
        print(f"\n⚠️  {result.get('status')}: {result.get('reason', '')}")


if __name__ == "__main__":
    main()
