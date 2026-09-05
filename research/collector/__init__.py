"""Research Collector — непрерывный сбор данных в Data Lake.

Модуль T2. Собирает свечи, тики, сигналы, решения, сделки из разных источников
и пишет в Data Lake с контролем качества, восстановлением после restart и
версионированием схем.

Не содержит торговой логики.
"""
import json
import time
import hashlib
import sqlite3
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Iterator
from pathlib import Path

logger = logging.getLogger("collector")


# ── Source Config ──────────────────────────────────────────

@dataclass
class SourceConfig:
    """Конфигурация источника данных."""
    name: str
    source_type: str           # "phase3", "bybit", "mt5", "binance"
    enabled: bool = True
    interval_seconds: int = 60
    schema_version: str = "1.0.0"
    metadata: dict = field(default_factory=dict)


# ── Source Adapter (ABC) ──────────────────────────────────

class SourceAdapter(ABC):
    """Абстракция источника данных. Каждый источник реализует этот интерфейс."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def source_type(self) -> str: ...

    @abstractmethod
    def collect(self) -> list[dict]:
        """Собрать новые данные. Возвращает список events для Data Lake."""
        ...

    def health(self) -> dict:
        return {"status": "ok"}


# ── Phase3 Adapter ─────────────────────────────────────────

class Phase3Adapter(SourceAdapter):
    """Сбор данных из BTC Phase3 shadow process."""

    def __init__(self, log_dir: Path, marker_db: Path):
        self._log_dir = log_dir
        self._marker_db = marker_db
        self._init_marker_db()

    @property
    def name(self) -> str:
        return "phase3_btc"

    @property
    def source_type(self) -> str:
        return "phase3"

    def _init_marker_db(self):
        conn = sqlite3.connect(str(self._marker_db), timeout=5)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS collect_markers (
                source      TEXT PRIMARY KEY,
                last_line   INTEGER DEFAULT 0,
                last_ts     TEXT,
                total_collected INTEGER DEFAULT 0,
                total_errors    INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def _get_marker(self) -> int:
        conn = sqlite3.connect(str(self._marker_db), timeout=5)
        row = conn.execute(
            "SELECT last_line FROM collect_markers WHERE source = ?",
            (self.name,),
        ).fetchone()
        conn.close()
        return row[0] if row else 0

    def _set_marker(self, line_num: int, total: int, errors: int):
        conn = sqlite3.connect(str(self._marker_db), timeout=5)
        conn.execute("""
            INSERT OR REPLACE INTO collect_markers
            (source, last_line, last_ts, total_collected, total_errors)
            VALUES (?, ?, ?, ?, ?)
        """, (self.name, line_num, datetime.now(timezone.utc).isoformat(), total, errors))
        conn.commit()
        conn.close()

    def collect(self) -> list[dict]:
        # Try stdout log first (current process), fallback to stderr (old process)
        log_file = self._log_dir / "phase3_btc.log"
        if not log_file.exists():
            log_file = self._log_dir / "phase3_btc_stderr.log"
        if not log_file.exists():
            return []

        last_line = self._get_marker()
        with open(log_file, "r") as f:
            lines = f.readlines()

        new_lines = lines[last_line:]
        if not new_lines:
            return []

        events = []
        errors = 0
        for line in new_lines:
            if "Phase3Crypto" not in line:
                continue
            event = self._parse_line(line)
            if event:
                events.append(event)
            else:
                errors += 1

        self._set_marker(last_line + len(new_lines), last_line + len(new_lines), errors)
        return events

    def _parse_line(self, line: str) -> Optional[dict]:
        now = datetime.now(timezone.utc).isoformat()

        if "CYCLE" in line:
            return self._parse_cycle(line, now)
        elif "Regime:" in line:
            return self._parse_regime(line, now)
        elif "SHADOW SIGNAL" in line:
            return self._parse_signal(line, now)
        elif "SHADOW OPEN" in line:
            return self._parse_open(line, now)
        elif "SHADOW CLOSE" in line:
            return self._parse_close(line, now)
        elif "HEARTBEAT" in line:
            return self._parse_heartbeat(line, now)
        return None

    def _parse_cycle(self, line: str, now: str) -> dict:
        try:
            price = float(line.split("price=")[1].split()[0])
            atr = float(line.split("atr=")[1].split()[0])
            adx = float(line.split("adx=")[1].split()[0])
            vol = float(line.split("vol=")[1].split()[0])
            cycle = int(line.split("CYCLE ")[1].split(":")[0])
            return {
                "event_type": "CandleClosed",
                "schema_version": "1.0.0",
                "timestamp": now,
                "source_module": "phase3_btc",
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "severity": "info",
                "payload": {
                    "price": price, "atr": atr, "adx": adx,
                    "volume": vol, "cycle": cycle,
                },
            }
        except Exception:
            return None

    def _parse_regime(self, line: str, now: str) -> dict:
        try:
            regime = line.split("Regime:")[1].split("(")[0].strip()
            conf = float(line.split("conf=")[1].split(")")[0])
            return {
                "event_type": "RegimeDetected",
                "schema_version": "1.0.0",
                "timestamp": now,
                "source_module": "phase3_btc",
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "severity": "info",
                "payload": {"regime": regime, "confidence": conf},
            }
        except Exception:
            return None

    def _parse_signal(self, line: str, now: str) -> dict:
        try:
            parts = line.split("SHADOW SIGNAL:")[1].strip().split()
            direction = parts[0]
            symbol = parts[1]
            price = float(parts[2])
            regime = line.split("regime=")[1].split()[0]
            score = float(line.split("score=")[1].split()[0])
            return {
                "event_type": "SignalGenerated",
                "schema_version": "1.0.0",
                "timestamp": now,
                "source_module": "phase3_btc",
                "symbol": symbol,
                "timeframe": "5m",
                "severity": "info",
                "payload": {
                    "direction": direction, "symbol": symbol,
                    "price": price, "regime": regime, "score": score,
                },
            }
        except Exception:
            return None

    def _parse_open(self, line: str, now: str) -> dict:
        try:
            trade_id = line.split("SHADOW OPEN:")[1].strip().split()[0]
            direction = line.split("SHADOW OPEN:")[1].strip().split()[1]
            price = float(line.split("@ ")[1].split()[0])
            return {
                "event_type": "PositionOpened",
                "schema_version": "1.0.0",
                "timestamp": now,
                "source_module": "phase3_btc",
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "severity": "info",
                "payload": {"trade_id": trade_id, "direction": direction, "price": price},
            }
        except Exception:
            return None

    def _parse_close(self, line: str, now: str) -> dict:
        try:
            trade_id = line.split("SHADOW CLOSE:")[1].strip().split()[0]
            exit_price = float(line.split("exit=")[1].split()[0])
            pnl = float(line.split("pnl=")[1].split()[0])
            return {
                "event_type": "TradeRecorded",
                "schema_version": "1.0.0",
                "timestamp": now,
                "source_module": "phase3_btc",
                "symbol": "BTCUSDT",
                "timeframe": "5m",
                "severity": "info",
                "payload": {"trade_id": trade_id, "exit_price": exit_price, "pnl": pnl},
            }
        except Exception:
            return None

    def _parse_heartbeat(self, line: str, now: str) -> dict:
        try:
            uptime = int(line.split("uptime=")[1].split("s")[0])
            mem = int(line.split("mem=")[1].split("KB")[0])
            trades = int(line.split("trades=")[1].split()[0])
            return {
                "event_type": "Heartbeat",
                "schema_version": "1.0.0",
                "timestamp": now,
                "source_module": "phase3_btc",
                "symbol": "BTCUSDT",
                "severity": "info",
                "payload": {"uptime_seconds": uptime, "memory_kb": mem, "trades_total": trades},
            }
        except Exception:
            return None

    def health(self) -> dict:
        stderr_file = self._log_dir / "phase3_btc_stderr.log"
        exists = stderr_file.exists()
        age = time.time() - stderr_file.stat().st_mtime if exists else -1
        marker = self._get_marker()
        return {
            "status": "ok" if exists and age < 300 else "warning",
            "file_exists": exists,
            "file_age_seconds": round(age),
            "last_line": marker,
        }


# ── Collector Stats ────────────────────────────────────────

@dataclass
class CollectorStats:
    """Статистика сбора данных."""
    events_received: int = 0
    events_written: int = 0
    events_skipped: int = 0
    events_errors: int = 0
    bytes_processed: int = 0
    last_collect_ts: Optional[str] = None
    last_write_ts: Optional[str] = None
    collection_time_ms: float = 0
    write_time_ms: float = 0
    sources_active: int = 0
    sources_total: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ── Data Quality Checker ───────────────────────────────────

class QualityChecker:
    """Проверка качества данных перед записью."""

    def __init__(self):
        self._checks_passed = 0
        self._checks_failed = 0

    def validate(self, event: dict) -> tuple[bool, str]:
        """Валидация event. Возвращает (ok, reason)."""
        required = ["event_type", "timestamp", "source_module", "severity"]
        for field in required:
            if field not in event:
                self._checks_failed += 1
                return False, f"missing_field:{field}"

        if not event.get("event_type"):
            self._checks_failed += 1
            return False, "empty_event_type"

        if not event.get("timestamp"):
            self._checks_failed += 1
            return False, "empty_timestamp"

        if "payload" not in event:
            event["payload"] = {}

        self._checks_passed += 1
        return True, "ok"

    def stats(self) -> dict:
        total = self._checks_passed + self._checks_failed
        return {
            "passed": self._checks_passed,
            "failed": self._checks_failed,
            "pass_rate": round(self._checks_passed / total * 100, 1) if total else 0,
        }


# ── Collector ──────────────────────────────────────────────

class ResearchCollector:
    """Основной collector — собирает данные из нескольких источников в Data Lake."""

    def __init__(self, data_lake, marker_db: Path):
        self._lake = data_lake
        self._marker_db = marker_db
        self._sources: list[SourceAdapter] = []
        self._quality = QualityChecker()
        self._stats = CollectorStats()

    def add_source(self, source: SourceAdapter):
        self._sources.append(source)
        self._stats.sources_total = len(self._sources)

    def collect_once(self) -> CollectorStats:
        """Один проход сбора по всем источникам."""
        self._stats.sources_active = 0
        total_events = 0

        for source in self._sources:
            try:
                start = time.time()
                events = source.collect()
                elapsed = (time.time() - start) * 1000

                self._stats.collection_time_ms = elapsed
                self._stats.events_received += len(events)
                self._stats.sources_active += 1

                written = 0
                for event in events:
                    ok, reason = self._quality.validate(event)
                    if not ok:
                        self._stats.events_skipped += 1
                        logger.debug(f"Skipped event: {reason}")
                        continue

                    event["source_module"] = source.name
                    try:
                        self._lake.write(event)
                        written += 1
                        self._stats.events_written += 1
                    except Exception as e:
                        self._stats.events_errors += 1
                        logger.error(f"Write error: {e}")

                self._stats.last_collect_ts = datetime.now(timezone.utc).isoformat()
                if written > 0:
                    self._stats.last_write_ts = datetime.now(timezone.utc).isoformat()

                total_events += len(events)
                logger.info(f"[{source.name}] collected={len(events)} written={written} time={elapsed:.0f}ms")

            except Exception as e:
                self._stats.events_errors += 1
                logger.error(f"Source {source.name} error: {e}")

        return self._stats

    def get_stats(self) -> dict:
        return {
            **self._stats.to_dict(),
            "quality": self._quality.stats(),
            "sources": [
                {"name": s.name, "type": s.source_type, **s.health()}
                for s in self._sources
            ],
        }

    def get_persistent_stats(self) -> dict:
        """Get stats from Data Lake (survives restarts)."""
        import sqlite3
        try:
            conn = sqlite3.connect(str(self._lake._path), timeout=5)
            cursor = conn.cursor()

            # Total events
            cursor.execute("SELECT COUNT(*) FROM events")
            total = cursor.fetchone()[0]

            # Events by source
            cursor.execute("SELECT source_module, COUNT(*) FROM events GROUP BY source_module")
            by_source = {row[0]: row[1] for row in cursor.fetchall()}

            # Latest event timestamp
            cursor.execute("SELECT MAX(timestamp) FROM events")
            latest_ts = cursor.fetchone()[0]

            # Events in last hour
            cursor.execute("""
                SELECT COUNT(*) FROM events
                WHERE timestamp > datetime('now', '-1 hour')
            """)
            recent = cursor.fetchone()[0]

            conn.close()

            return {
                "events_total": total,
                "events_by_source": by_source,
                "latest_event_ts": latest_ts,
                "events_last_hour": recent,
                "sources": [
                    {"name": s.name, "type": s.source_type, **s.health()}
                    for s in self._sources
                ],
            }
        except Exception as e:
            return {"error": str(e)}

    def run_daemon(self, interval: int = 60):
        """Непрерывный сбор данных."""
        logger.info(f"Research Collector started. Interval={interval}s, sources={len(self._sources)}")
        try:
            while True:
                stats = self.collect_once()
                logger.info(
                    f"Cycle done: received={stats.events_received} "
                    f"written={stats.events_written} errors={stats.events_errors}"
                )
                time.sleep(interval)
        except KeyboardInterrupt:
            logger.info("Collector stopped.")
