"""Production Hardening Layer — fault-tolerant architecture для 24/7 режима.

Компоненты:
1. CrashRecovery — восстановление после падения
2. StateConsistency — проверка целостности state
3. CircuitBreaker — защита от каскадных ошибок
4. HealthMonitor — непрерывный мониторинг
5. GracefulShutdown — корректное завершение
"""
import json
import time
import signal
import sqlite3
import asyncio
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Callable
from pathlib import Path
from enum import Enum
from collections import deque


# ── System State ───────────────────────────────────────────

class SystemState(str, Enum):
    BOOTING = "BOOTING"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    RUNNING = "RUNNING"
    DEGRADED = "DEGRADED"
    RECOVERING = "RECOVERING"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


# ── Crash Recovery ─────────────────────────────────────────

class CrashRecovery:
    """Восстановление состояния после падения."""

    def __init__(self, state_dir: Path):
        self._state_dir = state_dir
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._snapshot_file = state_dir / "system_snapshot.json"
        self._recovery_log = state_dir / "recovery_log.jsonl"

    def save_snapshot(self, state: dict):
        """Сохранить снимок состояния."""
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": state,
            "pid": os.getpid() if "os" in dir() else 0,
        }
        tmp = str(self._snapshot_file) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
        os.replace(tmp, str(self._snapshot_file))

    def load_snapshot(self) -> Optional[dict]:
        """Загрузить последний снимок."""
        if not self._snapshot_file.exists():
            return None
        try:
            with open(self._snapshot_file) as f:
                data = json.load(f)
            self._log_recovery("snapshot_loaded", data.get("timestamp", ""))
            return data.get("state", {})
        except Exception as e:
            self._log_recovery("snapshot_load_failed", str(e))
            return None

    def needs_recovery(self) -> bool:
        """Проверить нужно ли восстановление."""
        if not self._snapshot_file.exists():
            return False
        snapshot = self.load_snapshot()
        if not snapshot:
            return False
        # If snapshot exists but system was not cleanly stopped
        return snapshot.get("state", {}).get("system_state") != SystemState.STOPPED

    def _log_recovery(self, event: str, detail: str):
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "detail": detail,
        }
        with open(self._recovery_log, "a") as f:
            f.write(json.dumps(entry) + "\n")


# ── State Consistency ──────────────────────────────────────

class StateConsistency:
    """Проверка целостности состояния системы."""

    def __init__(self):
        self._checks: list[dict] = []

    def register_check(self, name: str, check_fn: Callable[[], bool], description: str = ""):
        self._checks.append({
            "name": name,
            "check_fn": check_fn,
            "description": description,
        })

    def run_all(self) -> dict:
        """Запустить все проверки."""
        results = {}
        failed = []
        for check in self._checks:
            try:
                ok = check["check_fn"]()
                results[check["name"]] = {"status": "OK" if ok else "FAIL", "description": check["description"]}
                if not ok:
                    failed.append(check["name"])
            except Exception as e:
                results[check["name"]] = {"status": "ERROR", "error": str(e), "description": check["description"]}
                failed.append(check["name"])

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "total": len(self._checks),
            "passed": len(self._checks) - len(failed),
            "failed": failed,
            "results": results,
            "healthy": len(failed) == 0,
        }


# ── Circuit Breaker ────────────────────────────────────────

class CircuitBreaker:
    """Защита от каскадных ошибок. Три состояния: CLOSED → OPEN → HALF_OPEN."""

    CLOSED = "CLOSED"       # нормальная работа
    OPEN = "OPEN"           # блокируем вызовы
    HALF_OPEN = "HALF_OPEN" # пробный вызов

    def __init__(self, failure_threshold: int = 5, recovery_timeout: int = 60):
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._state = self.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0
        self._success_count = 0

    def call(self, fn: Callable, *args, **kwargs):
        """Обернуть вызов через circuit breaker."""
        if self._state == self.OPEN:
            if time.time() - self._last_failure_time > self._recovery_timeout:
                self._state = self.HALF_OPEN
                self._success_count = 0
            else:
                raise CircuitBreakerOpenError(f"Circuit breaker OPEN, retry in {self._recovery_timeout}s")

        try:
            result = fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        if self._state == self.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= 3:
                self._state = self.CLOSED
                self._failure_count = 0
        elif self._state == self.CLOSED:
            self._failure_count = 0

    def _on_failure(self):
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self._failure_threshold:
            self._state = self.OPEN

    @property
    def state(self) -> str:
        return self._state

    def get_status(self) -> dict:
        return {
            "state": self._state,
            "failure_count": self._failure_count,
            "success_count": self._success_count,
        }


class CircuitBreakerOpenError(Exception):
    pass


# ── Health Monitor ─────────────────────────────────────────

class HealthMonitor:
    """Непрерывный мониторинг здоровья системы."""

    def __init__(self):
        self._metrics: deque = deque(maxlen=1000)
        self._alerts: deque = deque(maxlen=100)
        self._thresholds = {
            "memory_mb_max": 512,
            "cpu_pct_max": 90,
            "latency_ms_max": 5000,
            "error_rate_max": 0.05,
            "queue_size_max": 10000,
        }

    def record_metric(self, name: str, value: float, unit: str = ""):
        self._metrics.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "value": value,
            "unit": unit,
        })

        # Check thresholds
        threshold_key = f"{name}_max"
        if threshold_key in self._thresholds:
            if value > self._thresholds[threshold_key]:
                self._alerts.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "metric": name,
                    "value": value,
                    "threshold": self._thresholds[threshold_key],
                    "severity": "HIGH" if value > self._thresholds[threshold_key] * 1.5 else "MEDIUM",
                })

    def get_health(self) -> dict:
        """Общее здоровье системы."""
        recent_alerts = list(self._alerts)[-10:]
        high_alerts = [a for a in recent_alerts if a["severity"] == "HIGH"]

        if len(high_alerts) > 3:
            health = "CRITICAL"
        elif len(high_alerts) > 0:
            health = "DEGRADED"
        elif len(recent_alerts) > 5:
            health = "WARNING"
        else:
            health = "HEALTHY"

        return {
            "health": health,
            "total_metrics": len(self._metrics),
            "recent_alerts": len(recent_alerts),
            "high_alerts": len(high_alerts),
            "alerts": recent_alerts,
        }

    def get_metric_trend(self, name: str, window: int = 20) -> dict:
        """Тренд метрики."""
        values = [m["value"] for m in self._metrics if m["name"] == name][-window:]
        if len(values) < 2:
            return {"trend": "stable", "current": values[0] if values else 0}

        current = values[-1]
        avg = sum(values) / len(values)
        slope = (values[-1] - values[0]) / len(values)

        if slope > 0.01:
            trend = "increasing"
        elif slope < -0.01:
            trend = "decreasing"
        else:
            trend = "stable"

        return {
            "trend": trend,
            "current": round(current, 3),
            "avg": round(avg, 3),
            "slope": round(slope, 4),
            "samples": len(values),
        }


# ── Graceful Shutdown ──────────────────────────────────────

class GracefulShutdown:
    """Корректное завершение работы системы."""

    def __init__(self):
        self._shutdown_hooks: list[Callable] = []
        self._is_shutting_down = False
        self._timeout = 30

    def register_hook(self, hook: Callable, name: str = ""):
        self._shutdown_hooks.append({"hook": hook, "name": name})

    def initiate_shutdown(self):
        """Начать graceful shutdown."""
        if self._is_shutting_down:
            return
        self._is_shutting_down = True

        for hook_info in self._shutdown_hooks:
            try:
                hook_info["hook"]()
            except Exception as e:
                print(f"Shutdown hook {hook_info['name']} failed: {e}")

    @property
    def is_shutting_down(self) -> bool:
        return self._is_shutting_down


# ── Hardening Engine (ядро) ────────────────────────────────

class HardeningEngine:
    """Координирует все production hardening mechanisms."""

    def __init__(self, state_dir: Optional[Path] = None):
        self._state_dir = state_dir or Path(__file__).parent.parent.parent / "tradingos_state"
        self._crash_recovery = CrashRecovery(self._state_dir)
        self._consistency = StateConsistency()
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._health = HealthMonitor()
        self._shutdown = GracefulShutdown()
        self._system_state = SystemState.BOOTING

    def boot(self) -> dict:
        """Полная процедура boot."""
        self._system_state = SystemState.BOOTING
        result = {"boot_time": datetime.now(timezone.utc).isoformat()}

        # 1. Check for recovery
        if self._crash_recovery.needs_recovery():
            self._system_state = SystemState.RECOVERING
            result["recovery"] = "needed"
            prev_state = self._crash_recovery.load_snapshot()
            result["previous_state"] = prev_state
        else:
            result["recovery"] = "not_needed"

        # 2. Run consistency checks
        self._system_state = SystemState.INITIALIZING
        consistency = self._consistency.run_all()
        result["consistency"] = consistency

        # 3. Save initial snapshot
        self._crash_recovery.save_snapshot({
            "system_state": SystemState.READY,
            "boot_result": result,
        })

        self._system_state = SystemState.READY
        result["final_state"] = self._system_state
        return result

    def get_circuit_breaker(self, name: str) -> CircuitBreaker:
        """Получить или создать circuit breaker."""
        if name not in self._circuit_breakers:
            self._circuit_breakers[name] = CircuitBreaker()
        return self._circuit_breakers[name]

    def record_health(self, metrics: dict):
        """Записать метрики здоровья."""
        for name, value in metrics.items():
            if isinstance(value, (int, float)):
                self._health.record_metric(name, value)

    def get_system_status(self) -> dict:
        """Полный статус системы."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": self._system_state,
            "health": self._health.get_health(),
            "circuit_breakers": {
                name: cb.get_status()
                for name, cb in self._circuit_breakers.items()
            },
            "crash_recovery": {
                "has_snapshot": self._crash_recovery._snapshot_file.exists(),
            },
        }

    def shutdown(self):
        """Graceful shutdown."""
        self._system_state = SystemState.SHUTTING_DOWN
        self._crash_recovery.save_snapshot({
            "system_state": SystemState.STOPPED,
            "shutdown_time": datetime.now(timezone.utc).isoformat(),
        })
        self._shutdown.initiate_shutdown()
        self._system_state = SystemState.STOPPED
