#!/usr/bin/env python3
"""Rolling OOS degradation scan (2026-09-05, review-idea #3).

Раз в час читает trade_results.jsonl и проверяет каждый контур на
деградацию по скользящему окну сделок:
  - PF (profit factor) последних N сделок против предыдущих N
  - net за последние N

Алерт в Telegram только при РЕАЛЬНОЙ деградации (PF упал ниже порога
на фоне здоровой истории) — не спамим. Состояние хранится в
operations/contour_health.json. Запуск: systemd timer (hourly).

История ma_bb (молчал 2 месяца из-за whitelist-бага) — обоснование:
деградация/молчание контура должно обнаруживаться машиной, не человеком.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

TRADE_RESULTS = Path("/root/tradingos/logs/trades/trade_results.jsonl")
HEALTH_PATH = Path("/root/tradingos/operations/contour_health.json")

WINDOW = 30          # скользящее окно (сделок на контур)
MIN_TRADES = 15      # минимум сделок для вердикта
PF_DEGRADE = 0.8     # PF окна < 0.8 при историческом PF >= 1.2 → деградация
SILENCE_HOURS = 48   # валидированный контур молчит дольше → алерт «не торгует»
VALIDATED_CONTOURS = {
    # контур: максимум допустимого молчания уже учтён в SILENCE_HOURS
    "REALITY_DISCOVERY", "MA_BB", "MEME_SHORTS_D1", "funding", "long_limit",
}


def _load_trades():
    rows = []
    if not TRADE_RESULTS.exists():
        return rows
    with TRADE_RESULTS.open() as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _contour_of(t: dict) -> str:
    # Новые записи имеют contour; старые — только side/outcome → "unknown"
    return t.get("contour") or "unknown"


def _pf(pnls: list[float]) -> float:
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    if gross_loss <= 0:
        return float("inf") if gross_win > 0 else 0.0
    return gross_win / gross_loss


def _parse_ts(s: str) -> float:
    try:
        d = datetime.fromisoformat(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return 0.0


def scan() -> list[str]:
    """Вернуть список текстов алертов (пусто = всё здорово)."""
    trades = _load_trades()
    now = datetime.now(timezone.utc).timestamp()
    alerts = []

    by_contour = defaultdict(list)
    for t in trades:
        c = _contour_of(t)
        if t.get("net_pnl") is not None:
            by_contour[c].append((_parse_ts(t.get("timestamp", "")), float(t["net_pnl"])))

    state = {}
    if HEALTH_PATH.exists():
        try:
            state = json.loads(HEALTH_PATH.read_text())
        except Exception:
            state = {}

    for contour, series in by_contour.items():
        series.sort()
        pnls = [p for _, p in series]
        if contour == "unknown" or len(pnls) < 2 * MIN_TRADES:
            continue  # мало данных — вердикт не выдаём
        recent, prev = pnls[-WINDOW:], pnls[-2 * WINDOW:-WINDOW]
        pf_recent, pf_prev = _pf(recent), _pf(prev)
        net_recent = sum(recent)
        prev_alert = state.get(contour, {}).get("pf_below", False)
        degraded = pf_recent < PF_DEGRADE and pf_prev >= 1.2
        key = f"{contour}"
        if degraded and not prev_alert:
            alerts.append(
                f"🩺 ДЕГРАДАЦИЯ {key}: PF последних {WINDOW}={pf_recent:.2f} "
                f"(было {pf_prev:.2f} на предыдущих {WINDOW}). Net окна ${net_recent:+.0f}. "
                f"Проверить контур."
            )
        state.setdefault(contour, {})["pf_below"] = degraded
        state[contour]["last_scan"] = datetime.now(timezone.utc).isoformat()
        state[contour]["pf_recent"] = round(pf_recent, 2)
        state[contour]["pf_prev"] = round(pf_prev, 2)
        state[contour]["n_trades"] = len(pnls)

    # Молчание валидированных контуров (последняя сделка старше SILENCE_HOURS)
    for contour in VALIDATED_CONTOURS:
        series = by_contour.get(contour) or []
        # контур может быть валидирован, но ещё не иметь записей с contour-полем
        # (переходный период до первых закрытий) — не алертим, если записей 0
        if not series:
            continue
        last_ts = series[-1][0]
        silent_h = (now - last_ts) / 3600 if last_ts else 0
        prev_silent = state.get(contour, {}).get("silent_alerted", False)
        if silent_h > SILENCE_HOURS and not prev_silent:
            alerts.append(
                f"🔕 МОЛЧАНИЕ {contour}: последняя сделка {silent_h:.0f}ч назад "
                f"(> {SILENCE_HOURS}ч). Возможен гейт/баг — см. журнал контура."
            )
            state.setdefault(contour, {})["silent_alerted"] = True
        elif silent_h <= SILENCE_HOURS and prev_silent:
            state.setdefault(contour, {})["silent_alerted"] = False

    try:
        HEALTH_PATH.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    except Exception as e:
        print(f"health state write failed: {e}", file=sys.stderr)
    return alerts


def _send_telegram(texts: list[str]):
    if not texts:
        return
    if not os.environ.get("TELEGRAM_BOT_TOKEN") or not os.environ.get("TELEGRAM_CHAT_ID"):
        print("TG creds missing, alerts:", texts, file=sys.stderr)
        return
    try:
        import httpx
        for text in texts:
            httpx.post(
                f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
                json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text},
                timeout=15,
            )
    except Exception as e:
        print(f"telegram send failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    alerts = scan()
    _send_telegram(alerts)
    for a in alerts:
        print(a)
    print(f"scan complete: {len(alerts)} alert(s)")
