"""Глобальные риск-страховки TradingOS — единая точка для ВСЕХ исполнительных контуров.

2026-09-05 (owner: «исключить просадки и добавить прибыль»). Аудит сделок
01–05.09 показал: night_ban был только в reality (run_observation:938,
коммит 44fe4f1 от 09-03 19:54), а ношнл-кап $500 — только в
trade_executor (reality-путь). Контурные ночные убытки: 09-04 PRL −72 /
BBX −42 / DOT −92 (long-limit-auto + dn_sweep + funding). DOT/BBX $6000
ношнл = risk $50 / min SL-dist 0.8% в dn_sweep.

Это риск-КОНТРОЛЬ (Class A по K97: «wrong position size»), не сигнальная
логика — Evidence Gate не нарушается. Все функции fail-closed.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

MODE_PATH = Path("/root/tradingos/operations/trading_mode.json")


def _cfg() -> dict:
    try:
        return json.loads(MODE_PATH.read_text())
    except Exception:
        return {}


def night_ban_active() -> bool:
    """True, если сейчас ночное окно запрета торговли (UTC).

    night_ban_start/end из trading_mode.json (default 0-6).
    Поддержка wrap-окна (например 22-6). При ошибке чтения — False
    (не блокируем торговлю из-за сбоя конфига; блокирующие страховки
    должны быть явными).
    """
    c = _cfg()
    try:
        s = int(c.get("night_ban_start", 0) or 0)
        e = int(c.get("night_ban_end", 6) or 6)
    except Exception:
        return False
    h = datetime.now(timezone.utc).hour
    if s < e:
        return s <= h < e
    if s > e:  # wrap: 22-6
        return h >= s or h < e
    return False


def night_ban_reason() -> str:
    return f"night_ban {datetime.now(timezone.utc):%H}Z (конфиг {night_ban_window()})"


def night_ban_window() -> str:
    c = _cfg()
    return f"{c.get('night_ban_start', 0)}-{c.get('night_ban_end', 6)}Z"


def max_notional_usd() -> float:
    """Жёсткий ношнл-кап на позицию из конфига (max_position_size_usd).

    Fail-closed: конфиг нечитаем → $500 (baseline v1.2).
    """
    c = _cfg()
    try:
        v = float(c.get("max_position_size_usd", 500) or 500)
        return v if v > 0 else 500.0
    except Exception:
        return 500.0


def clamp_notional(usd: float) -> float:
    """Ограничить ношнл позиции глобальным капом."""
    return min(max(0.0, float(usd or 0)), max_notional_usd())
