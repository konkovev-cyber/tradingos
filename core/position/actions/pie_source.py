"""
core/position/actions/pie_source.py
PIE recommendations reader (READ-ONLY).
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import List

from .models import PIERecommendation

logger = logging.getLogger("tradingos.action_shadow.pie")


class PIESource:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(f"PIE DB not found: {self.db_path}")

    def fetch_recent(self, since_minutes: int = 60) -> List[PIERecommendation]:
        if not self.db_path.exists():
            return []
        out: List[PIERecommendation] = []
        try:
            uri = f"file:{self.db_path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, position_id, symbol, side, recommendation,
                       price_at_recommendation, pnl_at_recommendation,
                       mfe_at_recommendation, health_at_recommendation,
                       reason, timestamp_utc
                FROM live_assist_log
                WHERE timestamp_utc >= datetime('now', ?)
                ORDER BY timestamp_utc DESC
                LIMIT 100
                """,
                (f"-{since_minutes} minutes",),
            )
            for r in cur.fetchall():
                ts_str = r["timestamp_utc"]
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                except Exception:
                    ts = datetime.now(timezone.utc)
                out.append(PIERecommendation(
                    rec_id=int(r["id"]),
                    timestamp=ts,
                    symbol=r["symbol"],
                    side=r["side"],
                    recommendation=r["recommendation"],
                    price_at_recommendation=float(r["price_at_recommendation"] or 0.0),
                    pnl_at_recommendation=float(r["pnl_at_recommendation"] or 0.0),
                    mfe_at_recommendation=float(r["mfe_at_recommendation"] or 0.0),
                    health_at_recommendation=float(r["health_at_recommendation"] or 0.0),
                    reason=r["reason"] or "",
                ))
            conn.close()
        except Exception as e:
            logger.error(f"PIE source read failed: {e}")
        return out
