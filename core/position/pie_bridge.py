"""
core/position/pie_bridge.py
PIE Bridge v1 — адаптер чтения между PIE Observer и Position Guard.

Ответственность ТОЛЬКО ОДНА:
"Превратить существующее состояние PIE в PositionSnapshot"

Не делает:
- Не модифицирует PIE
- Не пишет в position_events
- Не вызывает BingX API (для stop_price - оставляет None на v1)

Stale Detection (passive, read-only):
- Помечает snapshot как CLOSED_CONFIRMED если в position_summary есть запись
  с тем же position_id и closed_at > timestamp_utc снимка.
- Помечает snapshot как STALE если последний BAR_UPDATE для position_id
  старше freshness_max_age_minutes минут (по умолчанию 30).
  Это покрывает случай "PIE забыл закрыть позицию, а биржа её уже закрыла".
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .models import PositionSnapshot, PositionStatus, Side

logger = logging.getLogger("tradingos.pie_bridge")


class PIEBridge:
    """
    Чистый адаптер чтения. Открывает SQLite read-only,
    извлекает последний snapshot по каждой открытой позиции,
    маппит в PositionSnapshot.

    Также реализует stale-фильтр на базе:
      - position_summary (closed records)
      - freshness check (отсутствие свежих BAR_UPDATE)
    """

    DEFAULT_FRESHNESS_MAX_AGE_MINUTES = 30

    def __init__(
        self,
        db_path: Path,
        freshness_max_age_minutes: int = DEFAULT_FRESHNESS_MAX_AGE_MINUTES,
    ):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"PIE DB not found: {self.db_path}")
        self.freshness_max_age = freshness_max_age_minutes

    def _connect_ro(self) -> Optional[sqlite3.Connection]:
        try:
            uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e:
            logger.error(f"Failed to open PIE DB read-only: {e}")
            return None

    def _load_closed_records(self, conn: sqlite3.Connection) -> Dict[str, datetime]:
        """
        Загружает (position_id -> closed_at) из position_summary.
        Используется для маркировки snapshot-ов как CLOSED_CONFIRMED.
        """
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT position_id, closed_at
                FROM position_summary
                WHERE closed_at IS NOT NULL
                """
            )
            result: Dict[str, datetime] = {}
            for r in cur.fetchall():
                pid = r["position_id"]
                ca = r["closed_at"]
                if not pid or not ca:
                    continue
                try:
                    ts = datetime.fromisoformat(ca)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    result[pid] = ts
                except Exception:
                    continue
            return result
        except Exception as e:
            logger.warning(f"position_summary not available: {e}")
            return {}

    def _load_latest_event_ts(self, conn: sqlite3.Connection) -> Dict[str, datetime]:
        """
        Загружает (position_id -> MAX(timestamp_utc)) из position_events.
        Используется для freshness check.
        """
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT position_id, MAX(timestamp_utc) AS last_ts
                FROM position_events
                GROUP BY position_id
                """
            )
            result: Dict[str, datetime] = {}
            for r in cur.fetchall():
                pid = r["position_id"]
                ts_str = r["last_ts"]
                if not pid or not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    result[pid] = ts
                except Exception:
                    continue
            return result
        except Exception as e:
            logger.warning(f"Could not compute latest event ts: {e}")
            return {}

    def get_positions(self) -> List[PositionSnapshot]:
        """
        Возвращает список PositionSnapshot для всех активных позиций.
        Каждый snapshot получает status:
          - CLOSED_CONFIRMED: position_summary содержит closed_at > snapshot.timestamp
          - STALE: последний BAR_UPDATE старше freshness_max_age минут
          - ACTIVE: всё в порядке
        """
        conn = self._connect_ro()
        if conn is None:
            return []

        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT position_id, symbol, side, entry_price, current_price,
                       pnl_pct, max_profit_seen, health_score, timestamp_utc
                FROM position_events
                WHERE id IN (
                    SELECT MAX(id) FROM position_events GROUP BY position_id
                )
                ORDER BY timestamp_utc DESC
                """
            )
            rows = cur.fetchall()
            closed_records = self._load_closed_records(conn)
            latest_event_ts = self._load_latest_event_ts(conn)
        except Exception as e:
            logger.error(f"Failed to read PIE DB: {e}")
            conn.close()
            return []
        finally:
            conn.close()

        now = datetime.now(timezone.utc)
        snapshots: List[PositionSnapshot] = []

        for row in rows:
            side_str = row["side"].upper()
            if side_str not in ("BUY", "LONG"):
                side = Side.SHORT
            else:
                side = Side.LONG

            try:
                ts = datetime.fromisoformat(row["timestamp_utc"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                ts = now

            pnl_pct = (row["pnl_pct"] or 0.0) * 100
            mfe_pct = (row["max_profit_seen"] or 0.0) * 100

            if abs(pnl_pct) > 100 or abs(mfe_pct) > 100:
                logger.warning(
                    f"PIE Bridge: skipping {row['symbol']} (position_id={row['position_id']}) — "
                    f"absurd pnl_pct={pnl_pct:.2f}% or mfe_pct={mfe_pct:.2f}%. "
                    f"Likely stale data in PIE DB."
                )
                continue

            if not row["symbol"] or not row["position_id"] or not row["timestamp_utc"]:
                logger.warning(
                    f"PIE Bridge: skipping incomplete row: symbol={row['symbol']!r} "
                    f"position_id={row['position_id']!r}"
                )
                continue

            position_id = row["position_id"]
            status = PositionStatus.ACTIVE
            status_reason = ""

            if position_id in closed_records:
                closed_at = closed_records[position_id]
                if closed_at >= ts:
                    status = PositionStatus.CLOSED_CONFIRMED
                    status_reason = f"position_summary.closed_at={closed_at.isoformat()}"
            elif position_id in latest_event_ts:
                last_event = latest_event_ts[position_id]
                age_minutes = (now - last_event).total_seconds() / 60.0
                if age_minutes > self.freshness_max_age:
                    status = PositionStatus.STALE
                    status_reason = (
                        f"last BAR_UPDATE {age_minutes:.0f}m ago "
                        f"(threshold {self.freshness_max_age}m)"
                    )

            snap = PositionSnapshot(
                timestamp=ts,
                exchange="bingx",
                symbol=row["symbol"],
                side=side,
                entry_price=float(row["entry_price"] or 0.0),
                mark_price=float(row["current_price"] or 0.0),
                pnl_pct=pnl_pct,
                mfe_pct=mfe_pct,
                health_score=float(row["health_score"] or 0.0),
                qty=0.0,
                stop_price=None,
                position_id=position_id,
                status=status,
            )
            snapshots.append(snap)

            if status in (PositionStatus.STALE, PositionStatus.CLOSED_CONFIRMED):
                logger.info(
                    f"PIE Bridge: {row['symbol']} marked as {status.value} — {status_reason}"
                )

        n_active = sum(1 for s in snapshots if s.status == PositionStatus.ACTIVE)
        n_stale = sum(1 for s in snapshots if s.status == PositionStatus.STALE)
        n_closed = sum(1 for s in snapshots if s.status == PositionStatus.CLOSED_CONFIRMED)
        logger.info(
            f"PIE Bridge: {len(snapshots)} positions "
            f"(active={n_active} stale={n_stale} closed_confirmed={n_closed})"
        )
        return snapshots
