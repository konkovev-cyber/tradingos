#!/usr/bin/env python3
"""
tools/health_watchdog.py — эксплуатационный монитор LIVE MICRO + tradFi.

Проверяет (по расписанию systemd timer, раз в час):
1. Сервисы активны: tradingos-reality, tradingos-tradfi, tradingos-deriv-collector,
   tradingos-guardian, tradingos-telegram.
2. Логи растут: signal_log.jsonl / signal_log_tradfi.jsonl свежее MAX_LOG_AGE_MIN.
3. Отчёты обновляются: tradable_universe_report.json / tradfi_universe_report.json
   свежее MAX_REPORT_AGE_MIN.

Алерт в Telegram только при СМЕНЕ состояния (healthy -> issues / issues -> healthy),
чтобы не спамить каждый час. Состояние — operations/watchdog_state.json.

НЕ трогает контур. Чистая эксплуатация.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
STATE = Path("/root/tradingos/operations/watchdog_state.json")
ENV_PATH = Path("/root/trading_brain_v4/research/execution/.env")

SERVICES = [
    "tradingos-reality.service",
    "tradingos-tradfi.service",
    "tradingos-deriv-collector.service",
    "tradingos-guardian.service",
    "tradingos-telegram.service",
]
LOGS = {
    "/root/tradingos/memory/signal_log.jsonl": 10,           # минут
    "/root/tradingos/memory/signal_log_tradfi.jsonl": 10,
}
REPORTS = {
    "/root/tradingos/tradable_universe_report.json": 3 * 60,  # минут
    "/root/tradingos/tradfi_universe_report.json": 3 * 60,
}


def _load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _send_tg(text: str) -> None:
    env = _load_env()
    token, chat = env.get("TELEGRAM_BOT_TOKEN", ""), env.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        print(f"[watchdog] telegram env missing, skip alert: {text}")
        return
    try:
        import httpx
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=10,
        )
    except Exception as e:
        print(f"[watchdog] telegram send failed: {e}")


def _service_ok(name: str) -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _fresh(path: str, max_age_min: int) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    try:
        return (time.time() - p.stat().st_mtime) <= max_age_min * 60
    except Exception:
        return False


def check() -> list[str]:
    issues = []
    for s in SERVICES:
        if not _service_ok(s):
            issues.append(f"service down: {s}")
    for p, age in LOGS.items():
        if not _fresh(p, age):
            issues.append(f"log stale (> {age} min): {Path(p).name}")
    for p, age in REPORTS.items():
        if not _fresh(p, age):
            issues.append(f"report stale (> {age // 60} h): {Path(p).name}")
    return issues


def main():
    issues = check()
    now = datetime.now(timezone.utc).isoformat()
    state = {"last_check": now, "last_state": None, "last_alert": None}
    if STATE.exists():
        try:
            state.update(json.loads(STATE.read_text()))
        except Exception:
            pass

    prev = state.get("last_state")
    cur = "OK" if not issues else "; ".join(issues)

    if prev is None or prev != cur:
        lines = [f"🛡 TradingOS watchdog — {now}"]
        if issues:
            lines.append("⚠ ПРОБЛЕМЫ:")
            lines += [f"  • {i}" for i in issues]
            lines.append(f"позиции/сервисы: см. /status")
        else:
            lines.append("✅ Всё в порядке: сервисы активны, логи растут, отчёты свежие.")
        _send_tg("\n".join(lines))

    state["last_state"] = cur
    state["last_check"] = now
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))
    print(f"[watchdog] {now} state={cur}")


if __name__ == "__main__":
    sys.exit(main())
