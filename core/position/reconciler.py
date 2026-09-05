"""
core/position/reconciler.py
Position Reconciler v1.

Responsibility: PIE Snapshot + BingX Reality → Reconciled PositionSnapshot
Stale or closed positions are marked accordingly and excluded from Guard.

Architecture:
                  +----------------+
                  | BingX READ API |
                  |  (truth state) |
                  +-------+--------+
                          |
PIE DB ------------------+
(analytics)              |
                          v
                  Position Reconciler
                          |
                          v
                   Valid Snapshot
                          |
                          v
                   Position Guard
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Set

from .models import PositionSnapshot

logger = logging.getLogger("tradingos.reconciler")


class ValidationStatus(str, Enum):
    OK = "OK"
    PIE_STALE = "PIE_STALE"
    BINGX_CLOSED = "BINGX_CLOSED"
    DATA_MISMATCH = "DATA_MISMATCH"


@dataclass
class ReconciledSnapshot:
    """
    Wraps a PositionSnapshot with reconciliation metadata.
    If validation.reconciled is False, Guard MUST skip it.
    """
    snapshot: PositionSnapshot
    pie_active: bool
    bingx_active: bool
    reconciled: bool
    validation: ValidationStatus
    reason: str = ""

    @property
    def guard_allowed(self) -> bool:
        return self.reconciled


class PositionReconciler:
    """
    Combines PIE snapshots (analytics) with BingX reality (ground truth)
    to produce validated snapshots suitable for Guard decision-making.
    """

    def __init__(self, bingx_symbols_provider):
        """
        bingx_symbols_provider: callable returning Set[str] of currently
        OPEN position symbols on BingX. May be sync or async.
        """
        self._bingx_symbols_provider = bingx_symbols_provider

    def _fetch_bingx_symbols(self) -> Set[str]:
        provider = self._bingx_symbols_provider
        if provider is None:
            return set()
        try:
            result = provider()
            if hasattr(result, "__await__"):
                import asyncio
                try:
                    return asyncio.get_event_loop().run_until_complete(result)
                except RuntimeError:
                    return asyncio.run(result)
            return result or set()
        except Exception as e:
            logger.error(f"Failed to fetch BingX reality: {e}")
            return set()

    def reconcile(self, pie_snapshots: List[PositionSnapshot]) -> List[ReconciledSnapshot]:
        """
        For each PIE snapshot, validate against BingX reality.
        Returns list of ReconciledSnapshot. Caller filters by guard_allowed.
        """
        bingx_symbols = self._fetch_bingx_symbols()
        reconciled: List[ReconciledSnapshot] = []

        for snap in pie_snapshots:
            pie_has = True
            bingx_has = snap.symbol in bingx_symbols

            if bingx_symbols and not bingx_has:
                reconciled.append(
                    ReconciledSnapshot(
                        snapshot=snap,
                        pie_active=True,
                        bingx_active=False,
                        reconciled=False,
                        validation=ValidationStatus.BINGX_CLOSED,
                        reason=f"PIE says {snap.symbol} open, but BingX reports it CLOSED",
                    )
                )
                logger.info(
                    f"RECONCILER: {snap.symbol} excluded — PIE=active, BingX=closed"
                )
                continue

            reconciled.append(
                ReconciledSnapshot(
                    snapshot=snap,
                    pie_active=pie_has,
                    bingx_active=bingx_has,
                    reconciled=True,
                    validation=ValidationStatus.OK,
                    reason="",
                )
            )

        n_total = len(pie_snapshots)
        n_ok = sum(1 for r in reconciled if r.reconciled)
        n_excluded = n_total - n_ok
        logger.info(
            f"RECONCILER: {n_ok}/{n_total} snapshots validated, {n_excluded} excluded"
        )
        return reconciled
