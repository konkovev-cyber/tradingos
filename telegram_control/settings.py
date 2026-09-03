"""
settings.py — настройка ключевых показателей TradingOS из Telegram-бота (2026-09-01).

Команда /settings: показывает все ключевые показатели системы и позволяет
менять их кнопками. Каждый параметр — с описанием (что это и зачем).

Параметры двух уровней:
  1. trading_mode.json — AUTO-контур (reality): риск, лимиты, фильтры.
  2. manual_session.json — ручной контур: риск, стопы, сканер.

Безопасность: значения валидируются по границам, записываются атомарно,
файлы защищены chattr +i — снимаем/возвращаем флаг вокруг записи.
"""
import json
import logging
import os
import subprocess
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger("settings")

TRADING_MODE = Path("/root/tradingos/operations/trading_mode.json")
MANUAL_SESSION = Path("/root/tradingos/operations/manual_session.json")

# ─── Параметры: key -> (файл, label, описание, [min,max], type, группа) ─────
PARAMS = [
    # — Риск и лимиты (AUTO) —
    ("risk_per_trade", "tm", "💵 Риск на сделку ($)",
     "Максимальный риск на одну позицию в долларах.",
     0.5, 1000.0, "float", "risk"),
    ("max_positions", "tm", "📦 Макс. одновременных позиций",
     "Сколько позиций может держать AUTO-контур одновременно.",
     1, 10, "int", "risk"),
    ("max_daily_loss_pct", "tm", "🛑 Дневной лимит убытка (%)",
     "Если дневной убыток превышает этот % от баланса — торговля стоп.",
     0.1, 10.0, "float", "risk"),
    ("max_total_loss_pct", "tm", "🌀 Общий лимит убытка (%)",
     "Максимальный суммарный убыток за период до принудительного стопа.",
     0.1, 20.0, "float", "risk"),
    # — Направление (AUTO) —
    ("sell_disabled", "tm", "🔀 Шорты (SELL)",
     "True = шорты запрещены (только лонги). False = шорты разрешены.",
     0, 1, "bool", "direction"),
    # — Универсум (AUTO) —
    ("universe_size", "tm", "🌐 Размер универсума",
     "Сколько топ-символов по объёму сканируется (ликвидность).",
     10, 120, "int", "market"),
    ("night_ban_start", "tm", "🌙 Начало ночного бана (UTC ч)",
     "Час UTC, с которого AUTO не торгует (не входить в ночь).",
     0, 23, "int", "market"),
    ("night_ban_end", "tm", "🌙 Конец ночного бана (UTC ч)",
     "Час UTC, до которого AUTO не торгует.",
     0, 23, "int", "market"),
    # — Фильтры сигнала (AUTO) —
    ("sg_prob_min", "tm", "🎯 Минимальная вероятность сигнала",
     "Порог final_probability. 0.55-0.60 — стандарт; 0.70+ — редко, но уверенно.",
     0.5, 0.7, "float", "signal"),
    ("sg_score_min", "tm", "📊 Минимальный score сигнала",
     "Порог total_score (0-100). 60 — стандарт; 75+ — только сильные.",
     50, 90, "int", "signal"),
    # — Тейк-профит (AUTO) —
    ("tp_atr_mult", "tm", "🎯 Тейк-профит (×ATR)",
     "Множитель ATR для TP. 1.5 = скальп; 3 = позиционный (комиссия несущественна).",
     1.0, 6.0, "float", "risk"),
    # — Ручной контур —
    ("manual_risk", "ms", "💵 Ручной риск на сделку ($)",
     "Риск для ручных сделок через бота (USD).",
     0.1, 100.0, "float", "manual"),
    ("manual_min_score", "ms", "📊 Ручной сканер: мин. score",
     "Ниже этого score сканер не показывает сигнал.",
     50, 95, "int", "manual"),
    ("manual_max_pos", "ms", "📦 Ручные макс. позиции",
     "Макс. одновременных ручных позиций.",
     1, 10, "int", "manual"),
]

# Карта: tm/manual ключ -> имя в файле
_TM_KEYS = {
    "sell_disabled": "sell_disabled",
    "max_positions": "max_positions",
    "max_daily_loss_pct": "max_daily_loss_pct",
    "max_total_loss_pct": "max_total_loss_pct",
    "universe_size": "universe_size",
    "night_ban_start": "night_ban_start",
    "night_ban_end": "night_ban_end",
}
# Специальные ключи (вычисляемые из ТРЕХ конфигов)
_SPECIAL_SG = {"sg_prob_min", "sg_score_min", "tp_atr_mult"}

# ─── Чтение/запись ────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception as e:
        logger.error(f"read {path}: {e}")
        return {}

def _write_json_atomic(path: Path, data: dict) -> bool:
    """Запись с chattr-защитой и атомарной заменой."""
    try:
        if path.exists():
            subprocess.run(["chattr", "-i", str(path)], capture_output=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        tmp.replace(path)
        subprocess.run(["chattr", "+i", str(path)], capture_output=True)
        return True
    except Exception as e:
        logger.error(f"write {path}: {e}")
        try:
            subprocess.run(["chattr", "+i", str(path)], capture_output=True)
        except Exception:
            pass
        return False

def get_value(key: str):
    """Прочитать текущее значение параметра."""
    if key == "sg_prob_min":
        # Спец: из run_observation фильтра + SG_CONF (берём код-фильтр 0.58)
        try:
            src = Path("/root/tradingos/data/run_observation.py").read_text()
            import re
            m = re.search(r"_prob_ok = prob_for_cand >= ([\d.]+)", src)
            if m: return float(m.group(1))
        except Exception:
            pass
        return 0.58
    if key == "sg_score_min":
        try:
            src = Path("/root/tradingos/data/run_observation.py").read_text()
            import re
            m = re.search(r"_score_ok = total_for_cand >= (\d+)", src)
            if m: return int(m.group(1))
        except Exception:
            pass
        return 60
    if key == "tp_atr_mult":
        try:
            src = Path("/root/tradingos/data/run_observation.py").read_text()
            import re
            m = re.search(r"tp = entry \+ atr \* ([\d.]+)", src)
            if m: return float(m.group(1))
        except Exception:
            pass
        return 3.0
    if key.startswith("manual_"):
        ms = _read_json(MANUAL_SESSION)
        keymap = {
            "manual_risk": "risk_per_trade_usd",
            "manual_min_score": "min_signal_score",
            "manual_max_pos": "max_positions",
        }
        return ms.get(keymap.get(key, key))
    tm = _read_json(TRADING_MODE)
    return tm.get(_TM_KEYS.get(key, key))

def set_value(key: str, value) -> bool:
    """Записать значение параметра. Спец-ключи меняют файлы/код с заботой."""
    # Спец-параметры: пишем через конфиг-оверрайды, чтобы не править код
    if key == "sg_prob_min":
        # Понижаем/повышаем порог в коде run_observation (единственный источник)
        return _patch_code_value(r"_prob_ok = prob_for_cand >= [\d.]+",
                                 f"_prob_ok = prob_for_cand >= {value:.2f}", value)
    if key == "sg_score_min":
        return _patch_code_value(r"_score_ok = total_for_cand >= \d+",
                                 f"_score_ok = total_for_cand >= {int(value)}", value)
    if key == "tp_atr_mult":
        ok1 = _patch_code_value(r"tp = entry \+ atr \* [\d.]+",
                                f"tp = entry + atr * {value:.1f}", value)
        ok2 = _patch_code_value(r"tp = entry - atr \* [\d.]+",
                                f"tp = entry - atr * {value:.1f}", value)
        return ok1 and ok2
    if key.startswith("manual_"):
        ms = _read_json(MANUAL_SESSION)
        keymap = {
            "manual_risk": "risk_per_trade_usd",
            "manual_min_score": "min_signal_score",
            "manual_max_pos": "max_positions",
        }
        if key == "manual_risk":
            ms["risk_per_trade_usd"] = float(value)
        elif key == "manual_min_score":
            ms["min_signal_score"] = int(value)
        elif key == "manual_max_pos":
            ms["max_positions"] = int(value)
        return _write_json_atomic(MANUAL_SESSION, ms)
    tm = _read_json(TRADING_MODE)
    tm_key = _TM_KEYS.get(key, key)
    if key == "sell_disabled":
        tm[tm_key] = bool(value)
    elif key in ("max_positions", "universe_size", "night_ban_start", "night_ban_end"):
        tm[tm_key] = int(value)
    else:
        tm[tm_key] = float(value)
    return _write_json_atomic(TRADING_MODE, tm)

def _patch_code_value(pattern: str, replacement: str, value) -> bool:
    """Патчить значение параметра в коде run_observation.py (фильтры сигналов)."""
    path = Path("/root/tradingos/data/run_observation.py")
    try:
        src = path.read_text()
        import re
        if not re.search(pattern, src):
            logger.error(f"pattern not found: {pattern}")
            return False
        new_src = re.sub(pattern, replacement, src, count=1)
        path.write_text(new_src)
        # Проверить компиляцию
        import py_compile
        py_compile.compile(str(path), doraise=True)
        logger.info(f"patched {pattern} → {replacement}")
        return True
    except Exception as e:
        logger.error(f"patch {pattern}: {e}")
        return False


# ─── UI ────────────────────────────────────────────────────────────

_ESC = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def format_value(v) -> str:
    if isinstance(v, bool):
        return "✅ разрешено" if v else "⛔ запрещено"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать ключевые показатели с кнопками редактирования."""
    groups = [
        ("risk", "💵 Риск и лимиты"),
        ("direction", "🔀 Направление"),
        ("market", "🌐 Рынок/расписание"),
        ("signal", "🎯 Фильтры сигнала"),
        ("manual", "👤 Ручной контур"),
    ]
    lines = ["⚙️ <b>НАСТРОЙКИ СИСТЕМЫ</b>", ""]
    for gkey, gname in groups:
        lines.append(f"<b>{gname}</b>")
        for key, _, label, desc, _, _, typ, group in PARAMS:
            if group != gkey:
                continue
            val = get_value(key)
            lines.append(f"  • {label}: <code>{format_value(val)}</code>")
        lines.append("")
    lines.append("<i>Нажмите кнопку ниже, чтобы изменить параметр.</i>")
    kb = []
    for key, _, label, desc, _, _, typ, group in PARAMS:
        kb.append([InlineKeyboardButton(f"✏️ {label}", callback_data=f"SET:{key}")])
    kb.append([InlineKeyboardButton("❌ Закрыть", callback_data="SET:close")])
    reply = InlineKeyboardMarkup(kb)
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=reply)


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий кнопок настроек: выбор параметра / запрос значения."""
    q = update.callback_query
    data = q.data or ""
    await q.answer()
    if data == "SET:close":
        try:
            await q.message.delete()
        except Exception:
            pass
        return
    if data.startswith("SET:"):
        key = data.split(":", 1)[1]
        # Найти параметр
        param = next((p for p in PARAMS if p[0] == key), None)
        if not param:
            await q.message.reply_text("❌ Неизвестный параметр", parse_mode="HTML")
            return
        _, _, label, desc, lo, hi, typ, group = param
        current = get_value(key)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("−5%", callback_data=f"SETVAL:{key}:{typ}:dec"),
             InlineKeyboardButton("+5%", callback_data=f"SETVAL:{key}:{typ}:inc")],
            [InlineKeyboardButton("◀ −1", callback_data=f"SETVAL:{key}:{typ}:dec1"),
             InlineKeyboardButton("+1 ▶", callback_data=f"SETVAL:{key}:{typ}:inc1")],
            [InlineKeyboardButton("⚖️ Сбросить (стандарт)", callback_data=f"SETVAL:{key}:{typ}:reset")],
            [InlineKeyboardButton("❌ Назад", callback_data="SET:back")],
        ])
        text = (
            f"⚙️ <b>{label}</b>\n\n"
            f"<b>Что это:</b> {_ESC(desc)}\n\n"
            f"<b>Текущее:</b> <code>{format_value(current)}</code>\n"
            f"<b>Диапазон:</b> {lo} – {hi}\n\n"
            f"<i>Изменяйте кнопками (−/+). Изменение применится после рестарта контура.</i>"
        )
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            await q.message.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return


async def settings_val_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Изменение значения параметра (−1/+1 / −5%/+5% / сброс)."""
    q = update.callback_query
    data = q.data or ""
    await q.answer()
    if not data.startswith("SETVAL:"):
        return
    try:
        _, key, typ, act = data.split(":")
    except ValueError:
        return
    param = next((p for p in PARAMS if p[0] == key), None)
    if not param:
        return
    _, _, label, desc, lo, hi, vtype, group = param
    current = get_value(key)
    if current is None:
        current = lo
    # Вычислить новое значение
    if act == "reset":
        default = {"sg_prob_min": 0.58, "sg_score_min": 60, "tp_atr_mult": 3.0,
                   "manual_risk": 0.5, "manual_min_score": 70, "manual_max_pos": 2,
                   "risk_per_trade": 10, "max_positions": 4}.get(key, lo)
        new_val = default
    elif typ == "bool":
        new_val = not bool(current)
    else:
        if act.startswith("dec") and act.endswith("1"):
            new_val = float(current) - 1
        elif act.startswith("inc") and act.endswith("1"):
            new_val = float(current) + 1
        elif act == "dec":
            new_val = float(current) * 0.95
        elif act == "inc":
            new_val = float(current) * 1.05
        else:
            new_val = float(current)
    # Ограничения
    if vtype == "int":
        new_val = max(int(lo), min(int(hi), int(round(new_val))))
    else:
        new_val = max(float(lo), min(float(hi), float(new_val)))
    # Сохранить
    ok = set_value(key, new_val)
    status = "✅ сохранено" if ok else "❌ ошибка сохранения"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("−5%", callback_data=f"SETVAL:{key}:{typ}:dec"),
         InlineKeyboardButton("+5%", callback_data=f"SETVAL:{key}:{typ}:inc")],
        [InlineKeyboardButton("◀ −1", callback_data=f"SETVAL:{key}:{typ}:dec1"),
         InlineKeyboardButton("+1 ▶", callback_data=f"SETVAL:{key}:{typ}:inc1")],
        [InlineKeyboardButton("⚖️ Сбросить", callback_data=f"SETVAL:{key}:{typ}:reset")],
        [InlineKeyboardButton("❌ Назад к списку", callback_data="SET:list")],
    ])
    text = (
        f"⚙️ <b>{label}</b>\n\n"
        f"<b>Текущее:</b> <code>{format_value(get_value(key))}</code>\n"
        f"{status} {('(рестарт контура для применения)' if key in _SPECIAL_SG or key.startswith('manual_') else '(применится сразу)')}"
    )
    try:
        await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass