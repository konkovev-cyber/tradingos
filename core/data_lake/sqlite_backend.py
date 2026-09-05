"""Data Lake — append-only event store (SQLite backend)."""
import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DB_PATH = Path(__file__).parent.parent.parent / "tradingos_data.db"


def _init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            event_id        TEXT PRIMARY KEY,
            event_type      TEXT NOT NULL,
            schema_version  TEXT NOT NULL DEFAULT '1.0.0',
            timestamp       TEXT NOT NULL,
            source_module   TEXT NOT NULL,
            trace_id        TEXT NOT NULL,
            correlation_id  TEXT,
            parent_event_id TEXT,
            symbol          TEXT,
            timeframe       TEXT,
            severity        TEXT NOT NULL DEFAULT 'info',
            payload         TEXT NOT NULL,
            ingested_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_symbol ON events(symbol, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id)")
    conn.commit()
    return conn


class DataLake:
    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or DB_PATH
        self._conn = _init_db(self._path)

    def write(self, event: dict) -> str:
        eid = event.get("event_id", str(uuid.uuid4()))
        event["event_id"] = eid
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "trace_id" not in event:
            event["trace_id"] = str(uuid.uuid4())
        if "schema_version" not in event:
            event["schema_version"] = "1.0.0"
        if "source_module" not in event:
            event["source_module"] = "unknown"
        if "severity" not in event:
            event["severity"] = "info"

        payload = event.get("payload", {})
        self._conn.execute(
            """INSERT OR IGNORE INTO events
               (event_id, event_type, schema_version, timestamp, source_module,
                trace_id, correlation_id, parent_event_id, symbol, timeframe,
                severity, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, event["event_type"], event["schema_version"],
                event["timestamp"], event["source_module"],
                event["trace_id"], event.get("correlation_id"),
                event.get("parent_event_id"), event.get("symbol"),
                event.get("timeframe"), event["severity"],
                json.dumps(payload),
            ),
        )
        self._conn.commit()
        return eid

    def query(self, event_type=None, symbol=None, limit=100, since=None):
        sql = "SELECT * FROM events WHERE 1=1"
        params = []
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        cur = self._conn.execute(sql, params)
        return [dict(zip(row.keys(), row)) for row in cur.fetchall()]

    def get_trace(self, trace_id: str):
        cur = self._conn.execute(
            "SELECT * FROM events WHERE trace_id = ? ORDER BY timestamp ASC",
            (trace_id,),
        )
        return [dict(zip(row.keys(), row)) for row in cur.fetchall()]

    def count(self, event_type=None):
        sql = "SELECT COUNT(*) FROM events"
        params = []
        if event_type:
            sql += " WHERE event_type = ?"
            params.append(event_type)
        return self._conn.execute(sql, params).fetchone()[0]

    def close(self):
        self._conn.close()
