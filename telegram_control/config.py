#!/usr/bin/env python3
"""
Telegram Control — Trading Control Layer.

Архитектура:
    BingX positions → PIE Brain → Telegram Control → Human confirms → Execution

Приоритет:
    1. MANUAL USER (может отменить любое решение AI)
    2. Risk Controller
    3. PIE
    4. Strategy

Безопасность:
    - Все опасные действия требуют подтверждения
    - Каждое действие логируется
    - "Freeze" режим блокирует новые входы
    - "Panic" закрывает позиции с подтверждением
"""

import os
import sys
import json
import asyncio
import logging
import sqlite3
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("TelegramControl")


# --- Config ---
ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "tradingos_data.db"
CONTROL_DB = ROOT / "tradingos_control.db"
STATE_FILE = ROOT / "tradingos_control_state.json"


def load_state() -> Dict[str, Any]:
    """Загрузить состояние управления."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"frozen": False, "mode": "manual", "panic": False}


def save_state(state: Dict[str, Any]):
    """Сохранить состояние управления."""
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        logger.error(f"Failed to save state: {e}")


# --- Helpers ---

def get_position_summary() -> Dict[str, Any]:
    """Получить сводку из БД PIE."""
    if not DB_PATH.exists():
        return {"total": 0}

    try:
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.execute("""
            SELECT position_id, symbol, side, entry_price, current_price,
                   pnl_pct, max_profit_seen, max_loss_seen, health_score,
                   state, recommendation
            FROM (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY id DESC) as rn
                FROM position_events
                WHERE event_type = 'BAR_UPDATE'
            )
            WHERE rn = 1
            ORDER BY symbol
        """)
        columns = [desc[0] for desc in cursor.description]
        positions = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return {"total": len(positions), "positions": positions}
    except Exception as e:
        logger.error(f"DB error: {e}")
        return {"total": 0, "error": str(e)}


def get_pnl_summary(positions: List[Dict]) -> Dict[str, float]:
    """Рассчитать сводку PnL."""
    total = 0.0
    wins = 0
    losses = 0
    for p in positions:
        pnl = p.get("pnl_pct", 0) or 0
        total += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    return {"total_pnl_pct": total, "wins": wins, "losses": losses}


# --- Panic / Freeze ---

def set_freeze(frozen: bool) -> Dict[str, Any]:
    """Заморозить/разморозить торговлю."""
    state = load_state()
    state["frozen"] = frozen
    save_state(state)
    return state


def set_mode(mode: str) -> Dict[str, Any]:
    """Установить режим управления."""
    valid = {"manual", "assist", "auto_safe", "auto_full"}
    if mode not in valid:
        return {"error": f"Invalid mode: {mode}. Valid: {', '.join(valid)}"}
    state = load_state()
    state["mode"] = mode
    save_state(state)
    return state
