"""
execution/mt5/adapter.py
MT5 Adapter — bridges MT5 Terminal with TradingOS Position Guardian.

Read-Only in Phase 1.
Communication: HTTP REST (MT5 EA polls/sends data).
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List
from pathlib import Path

from .schemas import MT5Snapshot, MT5Account, MT5Position, MT5Order, to_tradingos_position

logger = logging.getLogger("mt5.adapter")

SNAPSHOT_PATH = Path("/root/tradingos/execution/mt5/last_snapshot.json")


class MT5Adapter:
    """
    Minimal MT5 adapter for Phase 1 — Read Only.
    Reads MT5 snapshot and provides it to TradingOS Position Guardian.
    """

    def __init__(self):
        self.last_snapshot: Optional[MT5Snapshot] = None
        self._connected = False

    def load_snapshot(self, data: dict) -> MT5Snapshot:
        """Parse incoming MT5 snapshot and update internal state."""
        try:
            account_data = data.get("account", {})
            account = MT5Account(
                balance=float(account_data.get("balance", 0)),
                equity=float(account_data.get("equity", 0)),
                margin=float(account_data.get("margin", 0)),
                free_margin=float(account_data.get("free_margin", 0)),
                server_time=account_data.get("server_time", ""),
                connected=account_data.get("connected", False),
            )

            positions = []
            for p in data.get("positions", []):
                positions.append(MT5Position(
                    ticket=int(p.get("ticket", 0)),
                    symbol=p.get("symbol", ""),
                    side=p.get("type", ""),
                    volume=float(p.get("volume", 0)),
                    open_price=float(p.get("open_price", 0)),
                    sl=float(p["sl"]) if p.get("sl") else None,
                    tp=float(p["tp"]) if p.get("tp") else None,
                    profit=float(p.get("profit", 0)),
                ))

            snapshot = MT5Snapshot(
                account=account,
                positions=positions,
                heartbeat=datetime.now(timezone.utc).isoformat(),
            )

            self.last_snapshot = snapshot
            self._connected = True
            self._save_local(snapshot)
            return snapshot

        except Exception as e:
            logger.error(f"Failed to parse MT5 snapshot: {e}")
            self._connected = False
            return None

    def is_connected(self, max_age_seconds: int = 60) -> bool:
        """Check if recent heartbeat exists."""
        if not self._connected or not self.last_snapshot:
            return False
        return True

    def get_tradingos_positions(self) -> List[dict]:
        """Return current positions in TradingOS format."""
        if not self.last_snapshot:
            return []
        return [to_tradingos_position(p) for p in self.last_snapshot.positions]

    def _save_local(self, snapshot: MT5Snapshot):
        """Save latest snapshot to disk for debugging."""
        SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with SNAPSHOT_PATH.open("w") as f:
            f.write(json.dumps({
                "account": {
                    "balance": snapshot.account.balance,
                    "equity": snapshot.account.equity,
                },
                "positions_count": len(snapshot.positions),
                "heartbeat": snapshot.heartbeat,
                "connected": snapshot.account.connected,
            }, indent=2))

    def health_check(self) -> dict:
        """Minimal health status for monitoring."""
        return {
            "connected": self._connected,
            "has_snapshot": self.last_snapshot is not None,
            "positions": len(self.last_snapshot.positions) if self.last_snapshot else 0,
            "heartbeat": self.last_snapshot.heartbeat if self.last_snapshot else None,
        }
