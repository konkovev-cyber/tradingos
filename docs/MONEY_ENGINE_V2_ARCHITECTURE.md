# TradingOS Money Engine v2 — Architecture

## Guiding Principle

```
"Как получить максимум денег из хорошей сделки, не отдавая прибыль обратно рынку?"
```

---

## 1. Position Intelligence

### Назначение
Понять здоровье позиции в реальном времени.

### Health Score (0-100)

```
H = 30 × profit_signal
  + 20 × momentum_signal
  + 15 × structure_signal
  + 15 × time_signal
  + 10 × volume_signal
  + 10 × risk_signal
```

### Компоненты

**profit_signal (0-1):**
```python
if pnl_pct > 0:
    profit_signal = min(pnl_pct / mfe_pct, 1.0) if mfe_pct > 0 else 1.0
else:
    profit_signal = max(1 + pnl_pct / mae_pct, 0) if mae_pct < 0 else 0
```
Интерпретация: сколько от достигнутого осталось.

**momentum_signal (0-1):**
```python
# Скорость изменения MFE за последние N тиков
delta_mfe = current_mfe - mfe_5min_ago
momentum_signal = clamp(delta_mfe / 0.5, -1, 1)  # нормализация
```
Интерпретация: ускоряется или замедляется движение.

**structure_signal (0-1):**
```python
# Цена выше EMA = good structure
above_ema = price > ema_20
spreading = current_mfe > previous_mfe
structure_signal = (0.5 if above_ema else 0) + (0.5 if spreading else 0)
```

**time_signal (0-1):**
```python
# Оптимальное время для типа сделки
age_min = position_age / 60
if age_min < 5:    time_signal = 0.3  # слишком рано
elif age_min < 30: time_signal = 1.0  # нормально
elif age_min < 120: time_signal = 0.7  # длительная
else:              time_signal = 0.4  # слишком долго
```

**volume_signal (0-1):**
```python
# Если есть обёмные данные
vol_ratio = current_vol / avg_vol_20
volume_signal = clamp(vol_ratio, 0, 1)
```

**risk_signal (0-1):**
```python
dist_to_sl_pct = abs(price - sl) / price * 100
risk_signal = clamp(dist_to_sl_pct / 2.0, 0, 1)  # 2% = полный риск
```

### Данные (уже собираются)

```json
{
  "symbol": "ADA-USDT",
  "mfe_pct": 0.63,
  "mae_pct": -0.05,
  "pnl_pct": 0.35,
  "duration_sec": 1800,
  "current_price": 0.174,
  "entry_price": 0.172,
  "sl": 0.176,
  "tp": 0.180,
  "mfe_5min_ago": 0.50,
  "ema_20": 0.1735
}
```

---

## 2. Profit Protection Engine

### Адаптивная логика

```
STATE MACHINE:

NONE
 │
 │ MFE < 0.3%
 ▼
WATCH
 │
 │ MFE >= 0.3%
 ▼
ARMED
 │
 │ MFE >= 0.5% AND giveback_ratio > 40%
 ▼
WARNING
 │
 │ MFE >= 1.0%
 ▼
BE_READY
 │
 │ MFE >= 1.5%
 ▼
LOCK_READY
 │
 │ MFE >= 3.0%
 ▼
TRAILING
```

### Условия переходов

```python
def profit_protection_decision(mfe, pnl, giveback_ratio, peak_age_min, health_score):
    state = "NONE"

    if mfe < 0.3:
        state = "WATCH"
    elif mfe < 0.5:
        state = "ARMED"
    elif mfe < 1.0 and giveback_ratio < 40:
        state = "ARMED"
    elif mfe < 1.0 and giveback_ratio >= 40:
        state = "WARNING"
    elif mfe < 1.5:
        state = "BE_READY"
    elif mfe < 3.0:
        state = "LOCK_READY"
    else:
        state = "TRAILING"

    # Дополнительная проверка: ускоряется ли giveback?
    if state in ("ARMED", "WARNING") and giveback_ratio > 60:
        state = "WARNING"

    return state
```

### Action Map

```python
ACTION_MAP = {
    "NONE":        {"action": "HOLD", "new_sl": None},
    "WATCH":       {"action": "HOLD", "new_sl": None},
    "ARMED":       {"action": "HOLD", "new_sl": None},
    "WARNING":     {"action": "PREPARE_BE", "new_sl": None},
    "BE_READY":    {"action": "MOVE_SL_BE", "new_sl": "entry + 0.1%"},
    "LOCK_READY":  {"action": "MOVE_SL_LOCK", "new_sl": "entry + MFE * 0.3"},
    "TRAILING":    {"action": "MOVE_SL_TRAIL", "new_sl": "current - MFE * 0.35"},
}
```

### Защитный коэффициент

```python
# Для SHORT (продал — прибыль при падении)
def locked_profit_lock(entry, mfe_pct, side="SELL"):
    if side == "SELL":
        return entry * (1 - mfe_pct * 0.3 / 100)
    else:
        return entry * (1 + mfe_pct * 0.3 / 100)

# Пример:
# SELL entry=100, MFE=2%
# lock = 100 * (1 - 0.006) = 99.4
# Значит SL = 99.4, фиксируем +0.6%
```

### Данные

уже собираются в guardian_journal.jsonl:
- mfe_pct, mae_pct, pnl_pct, giveback_live_pct, dist_to_sl_pct, mfe_state

---

## 3. Market Regime Detector

### Определение режима

```python
def detect_regime(close_20, atr_20, volume_20):
    # ATR percentage = волатильность
    atr_pct = atr_20 / close_20 * 100

    # Тренд через EMA
    ema_fast = ema(close_20, 10)
    ema_slow = ema(close_20, 20)
    trend_strength = (ema_fast - ema_slow) / ema_slow * 100

    # Классификация
    if atr_pct > 2.0:
        if abs(trend_strength) > 1.0:
            regime = "HIGH_VOL_TREND"
        else:
            regime = "HIGH_VOL_RANGE"
    elif atr_pct < 0.3:
        regime = "LOW_VOLATILITY"
    elif abs(trend_strength) > 0.5:
        regime = "TREND"
    else:
        regime = "RANGE"

    # Panic detection
    recent_drop = (close_20[-1] - max(close_20[-5:])) / close_20[-1] * 100
    if recent_drop < -3:
        regime = "PANIC"

    return regime
```

### Guardian Response per Regime

| Режим | TRAILING | LOCK | Exit Speed |
|-------|----------|------|------------|
| TREND | агрессивный (0.3×MFE) | поздний | медленный |
| RANGE | умеренный (0.4×MFE) | средний | быстрый |
| HIGH_VOL | консервативный (0.25×MFE) | ранний | быстрый |
| LOW_VOL | стандартный (0.3×MFE) | стандартный | средний |
| PANIC | экстренный (0.2×MFE) | немедленный | немедленный |

### Данные

уже есть в DataHub:
- atr, atr_pct
- ema, sma
- volume
- RegimeBrain класс

---

## 4. Position Scoring

### Формула

```python
def position_score(mfe, mae, pnl, health, age_min, giveback, regime):
    score = 0

    # Прибыль (30 баллов)
    if pnl > 0:
        score += min(pnl / 2.0, 1.0) * 30  # +2% = максимум
    else:
        score += max(pnl / 3.0, -0.5) * 30  # -1.5% = минимум

    # Скорость (20 баллов)
    speed = mfe / (age_min + 1)
    score += min(speed / 0.1, 1.0) * 20  # 0.1%/мин = максимум

    # Giveback (20 баллов)
    if giveback < 20:
        score += 20
    elif giveback < 50:
        score += 10
    else:
        score += 0

    # Health (15 баллов)
    score += (health / 100) * 15

    # Время (15 баллов)
    if age_min < 60:
        score += 15
    elif age_min < 360:
        score += 10
    else:
        score += 5

    # Режим рынка (бонус/штраф)
    if regime == "TREND" and pnl > 0:
        score += 10  # бонус за удержание в тренде
    elif regime == "RANGE" and pnl > 0:
        score -= 5  # штраф за удержание в рейндже

    return clamp(score, 0, 100)
```

### Решения

```python
if score >= 80:   action = "HOLD_AGGRESSIVE"
elif score >= 60: action = "HOLD"
elif score >= 40: action = "PROTECT"
elif score >= 20: action = "REDUCE"
else:             action = "EXIT_CANDIDATE"
```

### Данные

уже собираются: mfe, mae, pnl, giveback_live_pct, health_score, duration_sec

---

## 5. Capital Allocation Engine

### Динамический размер

```python
def calculate_position_size(account_equity, risk_per_trade_pct, atr, regime):
    # Базовый риск
    risk_amount = account_equity * risk_per_trade_pct / 100

    # Адаптация к режиму
    regime_mult = {
        "TREND": 1.0,
        "RANGE": 0.8,
        "HIGH_VOL": 0.6,
        "LOW_VOL": 0.9,
        "PANIC": 0.3,
    }

    # Адаптация к серии
    recent_trades = get_recent_trades(20)
    win_rate = sum(1 for t in recent_trades if t.pnl > 0) / len(recent_trades) if recent_trades else 0.5
    streak_mult = 1.0 + (win_rate - 0.5) * 0.4  # 0.8 - 1.2

    adjusted_risk = risk_amount * regime_mult.get(regime, 1.0) * streak_mult

    # Размер позиции
    stop_distance_pct = atr * 2 / account_equity * 100
    position_size = adjusted_risk / (stop_distance_pct / 100) if stop_distance_pct > 0 else 0

    # Ограничения
    max_notional = account_equity * 0.15  # 15% максимум
    position_size = min(position_size, max_notional)

    return round(position_size, 2)
```

### Правила

- Максимум 15% от equity на сделку
- После 3 убытков подряд: снижение на 25%
- После 5 побед подряд: увеличение на 15%
- В PANIC: автоматическое снижение до 30%
- Никогда не увеличивать после убытка

### Данные

- account_equity: из exchange API
- win_rate: из trade_journal_v2
- regime: из RegimeDetector

---

## 6. Autopsy Engine

### Автоматический анализ после закрытия

```python
def autopsy_trade(trade):
    """Анализ закрытой сделки."""
    result = {
        "trade_id": trade.trade_id,
        "symbol": trade.symbol,
        "side": trade.side,
        "entry": trade.entry_price,
        "exit": trade.exit_price,
        "pnl_pct": trade.pnl_pct,
        "mfe_pct": trade.mfe_pct,
        "mae_pct": trade.mae_pct,
        "giveback_pct": trade.giveback_pct,
        "duration_min": trade.duration_min,
        "exit_reason": trade.exit_reason,
    }

    # Классификация
    if trade.mfe_pct > 0.5 and trade.pnl_pct < 0:
        result["classification"] = "PROFIT_GAVE_BACK"
    elif trade.mfe_pct > 0.5 and trade.pnl_pct > 0 and trade.giveback_pct > 50:
        result["classification"] = "PARTIAL_GIVEBACK"
    elif trade.pnl_pct > 0:
        result["classification"] = "CLEAN_WIN"
    elif trade.mae_pct < -1.0:
        result["classification"] = "DEEP_LOSS"
    else:
        result["classification"] = "SMALL_LOSS"

    # Что было бы с Guardian
    result["would_lock05"] = 0.5 if trade.mfe_pct >= 0.5 else trade.pnl_pct
    result["would_trail03"] = max(0, trade.mfe_pct - 0.3) if trade.mfe_pct >= 0.5 else trade.pnl_pct

    # Ответы
    result["questions"] = {
        "gave_back_profit": trade.mfe_pct > 0.5 and trade.giveback_pct > 50,
        "could_guardian_help": trade.mfe_pct >= 0.5 and result["would_lock05"] > trade.pnl_pct,
        "was_stop_too_tight": trade.pnl_pct < 0 and trade.mfe_pct < 0.2,
    }

    return result
```

### Агрегированный отчёт

```python
def aggregate_autopsy(trades):
    total = len(trades)
    gave_back = sum(1 for t in trades if t["questions"]["gave_back_profit"])
    could_help = sum(1 for t in trades if t["questions"]["could_guardian_help"])

    return {
        "total_trades": total,
        "gave_back_count": gave_back,
        "gave_back_pct": gave_back / total * 100 if total else 0,
        "guardian_could_help": could_help,
        "guardian_help_pct": could_help / total * 100 if total else 0,
        "avg_giveback": sum(t["giveback_pct"] for t in trades if t["mfe_pct"] > 0.5) / max(1, sum(1 for t in trades if t["mfe_pct"] > 0.5)),
    }
```

### Данные

уже есть: trade_journal_v2.jsonl, guardian_journal.jsonl

---

## 7. Adaptive Learning

### Сбор статистики (НЕ автоматическое изменение)

```python
class AdaptiveLearner:
    def __init__(self):
        self.protection_stats = defaultdict(list)

    def record_outcome(self, trade, protection_method, actual_pnl):
        """Записывает результат для анализа."""
        self.protection_stats[protection_method].append({
            "symbol": trade.symbol,
            "regime": trade.regime,
            "pnl": actual_pnl,
            "mfe": trade.mfe_pct,
        })

    def suggest_changes(self):
        """Предлагает изменения человеку. НЕ применяет автоматически."""
        suggestions = []
        for method, outcomes in self.protection_stats.items():
            if len(outcomes) >= 30:
                avg_pnl = sum(o["pnl"] for o in outcomes) / len(outcomes)
                win_rate = sum(1 for o in outcomes if o["pnl"] > 0) / len(outcomes)

                if avg_pnl > 0.5 and win_rate > 0.6:
                    suggestions.append({
                        "method": method,
                        "avg_pnl": avg_pnl,
                        "win_rate": win_rate,
                        "recommendation": "CONSIDER_ACTIVATION",
                    })

        return suggestions
```

---

## Данные (что уже есть)

| Источник | Данные |
|----------|--------|
| guardian_journal.jsonl | mfe, mae, pnl, giveback, dist_to_sl, sos_armed |
| position_timeline.jsonl | price, pnl, mfe, mae per tick |
| trade_journal_v2.jsonl | entry, exit, pnl, mfe, mae, giveback, exit_reason |
| shadow_protection_report_v13.jsonl | simulation results |
| portfolio_state.json | positions, sl, tp |
| pie_decision_queue.jsonl | PIE recommendations |
| DataHub | atr, ema, volume, regime |

## Тестирование на истории

```bash
# Запустить симулятор на существующих данных
python3 shadow_protection_simulator.py --mode=full

# Получить отчёт
cat data/shadow_protection_summary_v13.json
```

## План внедрения

### Phase 1 (сейчас)
- Shadow Simulator v1.3 → v2.0 (добавить regime-aware)
- Собрать 30-50 закрытых сделок
- Guardian Validation Report

### Phase 2 (после статистики)
- Включить MOVE_SL_BE (только BE)
- 1 позиция, минимальный риск
- Mon-Fri only

### Phase 3 (после 50 сделок)
- Добавить LOCK_PROFIT
- Адаптация к regime
- Position Scoring

### Phase 4 (после 100 сделок)
- Capital Allocation Engine
- Adaptive Learning suggestions
- Full Money Engine
