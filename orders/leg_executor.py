"""
leg_executor.py — Multi-leg + multi-symbol order execution layer.

Wraps the live DN-sweep detector with LegManager.
For each new DN-sweep signal:
- Determine tier (1=core, 2=aggressive, 3=patience) by confidence
- Place leg via LegManager
- Monitor PLACED legs:
  - Fill detection (price crosses entry)
  - Amend policy (max 2 per leg)
  - Cancel on invalidation
- Track state across restarts

Currently PAPER mode. LIVE mode requires explicit owner approval.
"""
import json
import time
import sys
import os
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, "/root/tradingos")

from orders.leg_manager import LegManager, Leg, TIERS, MAX_LEGS_PLACED, MAX_AMENDS_PER_LEG

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger("leg_executor")


class LegExecutor:
    """Bridges signal detection → leg placement → fill detection → close."""

    def __init__(self, paper_mode: bool = True):
        self.mgr = LegManager(paper_mode=paper_mode)
        self.paper_mode = paper_mode

    # Sentinel returned by amend_policy_check meaning "cancel the leg"
    AMEND_SENTINEL_CANCEL = "CANCEL"

    def determine_tier(self, confidence: float, pattern_strength: float = 0.5) -> int:
        """Map confidence → tier.
        Tier 1 (core): confidence > 0.9 AND pattern_strength high → tight entry at entry_zone
        Tier 2 (aggressive): confidence 0.7-0.9 → deeper entry (better fill)
        Tier 3 (patience): confidence 0.5-0.7 → higher entry (late, post-pullback)
        """
        if confidence >= 0.9 and pattern_strength > 0.7:
            return 1
        elif confidence >= 0.7:
            return 2
        else:
            return 3

    def on_signal_detected(
        self,
        symbol: str,
        signal_ts: int,
        entry_zone: float,
        confidence: float,
        regime: int,
        regime_name: str,
        sl_hint: float,
        tp_hint: float,
        metadata: dict = None,
        side: str = "BUY",
    ) -> tuple[bool, str, Optional[Leg]]:
        """Called when a new DN-sweep signal is found."""
        tier = self.determine_tier(confidence)
        ok, reason, leg = self.mgr.place_leg(
            symbol=symbol,
            side=side,
            tier=tier,
            pattern="DN_SWEEP",
            signal_ts=signal_ts,
            entry_zone=entry_zone,
            confidence=confidence,
            regime=regime,
            regime_name=regime_name,
            metadata={
                "sl_hint": sl_hint,
                "tp_hint": tp_hint,
                **(metadata or {}),
            },
        )
        if ok:
            log.info(f"✅ LEG PLACED {symbol} tier={tier} conf={confidence:.2f} entry={leg.placed_price:.6g} expires_in={TIERS[tier]['expiry_min']}min")
        else:
            log.debug(f"⏭️ LEG SKIPPED {symbol}: {reason}")
        return ok, reason, leg

    def check_fills_for_symbol(self, symbol: str, high_probe: float, low_probe: float) -> Optional[Leg]:
        """Check if any PLACED leg for symbol got filled.

        Probes: high_probe/low_probe bound the price range since the last
        check (ticker + current M15 bar extremes). A BUY LIMIT at P fills if
        the market traded at or below P → low_probe <= P. Fill price is the
        LIMIT price (maker), not the market price.
        Returns the filled leg (and updates state) or None."""
        for order_link_id, leg in list(self.mgr.legs.items()):
            if leg.symbol != symbol or leg.state != "PLACED":
                continue
            # Check expiry
            now_ms = int(time.time() * 1000)
            expiry_ms = TIERS[leg.tier]["expiry_min"] * 60 * 1000
            if now_ms - leg.placed_ts > expiry_ms:
                # Expired — will be moved to EXPIRED in cleanup
                continue
            # Check fill condition
            if leg.side == "BUY" and low_probe <= leg.placed_price:
                # Maker fill: AT placed_price, not market price
                ok, _ = self.mgr.fill_leg(order_link_id)
                if ok:
                    log.info(f"✅ LEG FILLED {symbol} leg={order_link_id} fill={leg.placed_price:.6g} entry_zone={leg.entry_zone:.6g}")
                    return leg
            elif leg.side == "SELL" and high_probe >= leg.placed_price:
                ok, _ = self.mgr.fill_leg(order_link_id)
                if ok:
                    log.info(f"✅ LEG FILLED {symbol} leg={order_link_id} fill={leg.placed_price:.6g} entry_zone={leg.entry_zone:.6g}")
                    return leg
        return None

    def check_exits_for_symbol(self, symbol: str, high_probe: float, low_probe: float) -> Optional[Leg]:
        """Check if any BRACKET leg hit SL or 4h hold end (SL-only exit model,
        VALIDATION_v2_SL800.md — no TP: exit at hold end close).
        Probes bound the price range since the last check. SL exit recorded at
        the structural level; TIME exit at current market price.
        Returns the closed leg (and updates state) or None."""
        for order_link_id, leg in list(self.mgr.legs.items()):
            if leg.symbol != symbol or leg.state != "BRACKET":
                continue
            if leg.fill_price is None or leg.sl_price is None:
                continue
            # Check hold time (4h)
            now_ms = int(time.time() * 1000)
            hold_ms = now_ms - leg.fill_ts if leg.fill_ts else 0
            if hold_ms > 4 * 3600 * 1000:  # 4h
                ok, _ = self.mgr.close_leg(order_link_id, exit_price=high_probe, outcome="TIME")
                if ok:
                    log.info(f"⏱️ LEG TIME-EXIT {symbol} ret={leg.ret_gross*100:+.2f}%")
                    return leg
                continue
            # Check SL hit using probes
            if leg.side == "BUY":
                sl_hit = low_probe <= leg.sl_price
            else:
                sl_hit = high_probe >= leg.sl_price
            if sl_hit:
                ok, _ = self.mgr.close_leg(order_link_id, outcome="SL")
                if ok:
                    log.info(f"🛑 LEG SL-EXIT {symbol} ret={leg.ret_gross*100:+.2f}%")
                    return leg
        return None

    def amend_policy_check(self, leg: Leg, current_price: float, bar_close: float) -> Optional[float]:
        """Decide whether to amend PLACED leg.
        Returns new_price if amend needed, None otherwise.
        Amend rules (v2 — NO chase):
        - Time to expiry < 5 min → no amend (let expire)
        - Amend count >= MAX_AMENDS_PER_LEG → no amend
        - Structure invalidated (price broke sweep low by >0.5%) → CANCEL via
          returned sentinel; caller cancels the leg.

        IMPORTANT (owner principle "поставил ордер в прогнозной точке и ждёшь"):
        chasing price with amendments converts LIMIT into a hidden market order
        and destroys the edge. The only permitted amend is NOTHING. We only
        cancel on structure invalidation.
        """
        # Structure invalidation for BUY: price fell below sweep wick low
        wick_low = (leg.metadata or {}).get("wick_low")
        if leg.side == "BUY" and wick_low and current_price < wick_low * 0.999:
            return self.AMEND_SENTINEL_CANCEL
        return None

    def run_cycle(self, symbol_to_price: dict):
        """Run one monitor cycle: check fills, exits, expire, invalidate.

        symbol_to_price: {symbol: (high_probe, low_probe)} where probes bound
        the price range since the last cycle (ticker + current M15 bar).
        """
        symbol_to_price = symbol_to_price or {}
        # First: expire stale
        self.mgr.expire_stale_legs()
        # Per-symbol: check fills, then exits, then structure invalidation
        for symbol, (high_probe, low_probe) in symbol_to_price.items():
            self.check_fills_for_symbol(symbol, high_probe, low_probe)
            self.check_exits_for_symbol(symbol, high_probe, low_probe)
            # Structure invalidation for PLACED legs (cancel, no chase)
            for order_link_id, leg in list(self.mgr.legs.items()):
                if leg.symbol != symbol or leg.state != "PLACED":
                    continue
                signal = self.amend_policy_check(leg, low_probe, bar_close=high_probe)
                if signal == self.AMEND_SENTINEL_CANCEL:
                    ok, _ = self.mgr.cancel_leg(order_link_id, reason="structure_invalidated")
                    if ok:
                        log.info(f"❌ LEG CANCELLED {symbol} leg={order_link_id} (sweep low broken)")
        # Periodic cleanup
        if int(time.time()) % 600 < 5:  # roughly every 10 min
            self.mgr.cleanup_old_legs()


if __name__ == "__main__":
    ex = LegExecutor(paper_mode=True)
    print(f"loaded {len(ex.mgr.legs)} legs")
    print(f"stats: {ex.mgr.get_stats()}")
