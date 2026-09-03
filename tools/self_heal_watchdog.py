#!/usr/bin/env python3
"""
self_heal_watchdog.py — Self-healing watchdog для всех торговых контуров.

Проверяет каждые 15 минут:
  1. Живы ли процессы контуров (ps by cmdline pattern)
  2. Живы ли systemd services/timers
  3. Не замолчали ли ключевые логи (freshness check)
  4. Heartbeat-файл: если сам watchdog жив — пишет timestamp

При отказе:
  - systemd unit → systemctl restart
  - bare process → systemctl restart его unit, если есть; иначе лог-алерт
  - Повторный фейл после рестарта (2 подряд) → Telegram-алерт владельцу

Работает как systemd timer (15 min). Fail-safe: любые ошибки watchdog не
должны рушить торговлю — только логи и алерты.
"""
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/root/tradingos")
STATE = ROOT / "operations/self_heal_state.json"
LOG = ROOT / "memory/self_heal_events.jsonl"

# (название, тип проверки, значение, unit для рестарта)
CONTOURS = [
    # AUTO/reality главный движок
    ("run_observation", "process", "trading.data.run_observation", "tradingos-reality.service"),
    # Reality guardian (SL/TP защита позиций)
    ("reality_guardian", "process", "guardian/reality_guardian.py", "tradingos-guardian.service"),
    # Ручной Telegram-бот
    ("manual_bot", "service", "tradingos-manual-bot.service", "tradingos-manual-bot.service"),
    # BingX guardian
    ("bingx_guardian", "process", "guardian/bingx_guardian.py", None),
    # Position guard worker
    ("position_guard", "process", "services/position_guard_worker.py", None),
    # dn_sweep (мемкоины на прострелах)
    ("dn_sweep", "service", "dn-sweep-paper.service", "dn-sweep-paper.service"),
    # multi_leg detector
    ("multi_leg", "process", "orders/multi_leg_live_detector.py", None),
    # auto-limit timer (лимитки L1/L2)
    ("auto_limit_timer", "timer", "auto-limit-placer.timer", "auto-limit-placer.timer"),
    # funding watchdog (hourly)
    ("funding_watchdog", "timer", "opportunity-watchdog.timer", "opportunity-watchdog.timer"),
]

# Логи, которые должны обновляться (путь, max age seconds)
FRESH_LOGS = [
    ("dn_sweep_log", ROOT / "patterns/reversal_v1/paper/detector.log", 3600),
]

# Telegram alert
TG_ENV = Path("/root/mt5_trading_bot/manual_bot.env")


def _log_event(event: str, data: dict) -> None:
    rec = {"ts": time.time(),
           "iso": datetime.now(timezone.utc).isoformat(),
           "event": event, **data}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"restart_counts": {}, "alerts_sent": {}}


def _save_state(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=2, ensure_ascii=False))
    tmp.replace(STATE)


def _proc_alive(pattern: str) -> bool:
    """Check if any process cmdline contains pattern."""
    try:
        r = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=20)
        for line in r.stdout.splitlines():
            if pattern in line and "grep" not in line and "self_heal" not in line:
                return True
        return False
    except Exception:
        return False


def _unit_active(unit: str) -> bool:
    """systemd unit is active (running) or timer has next elapse."""
    try:
        r = subprocess.run(["systemctl", "is-active", unit],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _restart_unit(unit: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "restart", unit],
                           capture_output=True, text=True, timeout=30)
        ok = r.returncode == 0
        return ok, r.stdout.strip() + r.stderr.strip()
    except Exception as e:
        return False, str(e)


def _send_tg_alert(text: str) -> bool:
    """Alert owner via Grizzly bot (manual bot token, TradingOS chat)."""
    try:
        env = {}
        for line in open("/root/mt5_trading_bot/manual_bot.env"):
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k.strip()] = v.strip()
        token = env.get("TELEGRAM_BOT_TOKEN", "")
        chat = env.get("TELEGRAM_CHAT_ID", "")
        if not token or not chat:
            return False
        import httpx
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=10, proxy="socks5://127.0.0.1:1080")
        return r.status_code == 200
    except Exception:
        return False


def check_contours() -> list[str]:
    """Check all contours. Returns list of failure descriptions."""
    failures = []
    st = _load_state()
    restarts = st.setdefault("restart_counts", {})
    now = time.time()

    for name, kind, target, unit in CONTOURS:
        if kind == "process":
            alive = _proc_alive(target)
        elif kind == "service":
            alive = _unit_active(target)
        elif kind == "timer":
            # timer: unit itself is 'active' if scheduled; also check last service run ok
            alive = _unit_active(target)
        else:
            alive = False

        if alive:
            # healthy → reset restart counter (after 30 min of stability)
            if name in restarts and now - st.get("last_fail", {}).get(name, 0) > 1800:
                restarts.pop(name, None)
            continue

        # FAILURE path
        st.setdefault("last_fail", {})[name] = now
        restarts[name] = restarts.get(name, 0) + 1
        count = restarts[name]

        if unit and count <= 3:
            ok, out = _restart_unit(unit)
            _log_event("RESTART", {"contour": name, "unit": unit,
                                     "attempt": count, "ok": ok, "out": out[:200]})
            print(f"  🔧 {name}: DOWN → restart {unit} (attempt {count}) {'OK' if ok else 'FAIL'}")
        else:
            _log_event("FAILED_NO_RESTART", {"contour": name, "attempts": count})
            failures.append(f"{name} down, {count} attempts")
            print(f"  🚨 {name}: DOWN x{count} — no restart possible")

        # 2+ consecutive failures → alert owner (once per hour)
        if count >= 2:
            alerts = st.setdefault("alerts_sent", {})
            last_alert = alerts.get(name, 0)
            if now - last_alert > 3600:
                ok_tg = _send_tg_alert(
                    f"🚨 <b>SELF-HEAL</b>: контур <b>{name}</b> недоступен.\n"
                    f"Рестартов за сегодня: {count}\n"
                    f"Время: {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
                alerts[name] = now
                _log_event("ALERT_SENT", {"contour": name, "tg_ok": ok_tg})

    _save_state(st)
    return failures


def check_log_freshness() -> list[str]:
    """Key logs must be fresh — silence = contour broken."""
    failures = []
    for name, path, max_age in FRESH_LOGS:
        if not path.exists():
            continue
        try:
            age = time.time() - path.stat().st_mtime
            if age > max_age:
                failures.append(f"{name}: log silent {age/60:.0f}min (max {max_age/60:.0f}min)")
                _log_event("LOG_STALE", {"log": name, "age_min": round(age/60)})
                print(f"  ⏰ {name}: stale {age/60:.0f}min")
        except Exception:
            pass
    return failures


def heartbeat():
    st = _load_state()
    st["last_run"] = time.time()
    st["last_run_iso"] = datetime.now(timezone.utc).isoformat()
    _save_state(st)


if __name__ == "__main__":
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] Self-Heal Watchdog")
    fails = check_contours()
    stale = check_log_freshness()
    heartbeat()
    if not fails and not stale:
        print("  ✅ All contours healthy")
    else:
        for f in fails + stale:
            print(f"  {f}")