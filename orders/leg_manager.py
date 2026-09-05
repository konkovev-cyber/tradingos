"""
leg_manager.py — Multi-leg + multi-symbol order manager.

Manages 12-20 LIMIT orders simultaneously across 14 whitelist symbols.
Each leg has lifecycle: SCANNED → PLACED → AMENDED → FILLED → BRACKET → CLOSED.
Persists state to state.json, logs events to event_log.jsonl.

Currently PAPER mode (live_trading_enabled=false): simulates fills from price.
After validation period → live mode (calls Bybit API).
"""
import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict


STATE_PATH = Path("/root/tradingos/orders/state.json")
EVENT_LOG = Path("/root/tradingos/orders/event_log.jsonl")
PAPER_TRADES_LOG = Path("/root/tradingos/orders/paper_trades.jsonl")

# Tier definitions (offset from entry_zone in %)
TIERS = {
    1: {"name": "core", "offset_pct": 0.0, "expiry_min": 30, "max_per_symbol": 2},
    2: {"name": "aggressive", "offset_pct": -0.3, "expiry_min": 30, "max_per_symbol": 2},
    3: {"name": "patience", "offset_pct": +0.5, "expiry_min": 60, "max_per_symbol": 1},
}

MAX_LEGS_PLACED = 20
MAX_POSITIONS_FILLED = 4
MAX_AMENDS_PER_LEG = 2


@dataclass
class Leg:
    order_link_id: str
    symbol: str
    side: str  # BUY/SELL
    tier: int  # 1, 2, 3
    pattern: str  # DN_SWEEP, ...
    signal_ts: int  # ms timestamp of pattern detection
    signal_ts_iso: str
    entry_zone: float  # прогнозная точка входа
    placed_price: float  # actual LIMIT price (after tier offset)
    placed_ts: int
    placed_ts_iso: str
    state: str = "PLACED"  # PLACED → AMENDED → FILLED → BRACKET → CLOSED_*
    confidence: float = 0.0
    regime: int = 1
    regime_name: str = "TRENDING_UP"
    amend_count: int = 0
    amend_history: list = field(default_factory=list)
    fill_price: Optional[float] = None
    fill_ts: Optional[int] = None
    fill_ts_iso: Optional[str] = None
    sl_price: Optional[float] = None
    tp_price: Optional[float] = None
    exit_price: Optional[float] = None
    exit_ts: Optional[int] = None
    exit_outcome: Optional[str] = None  # TP/SL/TIME/BE+TP/BE+SL
    ret_gross: Optional[float] = None
    ret_net_maker: Optional[float] = None
    metadata: dict = field(default_factory=dict)


class LegManager:
    """Coordinates multi-leg order lifecycle."""

    def __init__(self, paper_mode: bool = True):
        self.paper_mode = paper_mode
        self.legs: dict[str, Leg] = {}  # order_link_id -> Leg
        self._load_state()

    def _load_state(self):
        if not STATE_PATH.exists():
            return
        try:
            state = json.loads(STATE_PATH.read_text())
        except Exception as e:
            print(f"state load error: {e}")
            return
        import dataclasses
        valid_fields = {f.name for f in dataclasses.fields(Leg)}
        for k, v in state.items():
            # Drop unknown keys (schema drift), backfill missing with defaults
            filtered = {kk: vv for kk, vv in v.items() if kk in valid_fields}
            try:
                self.legs[k] = Leg(**filtered)
            except Exception as e:
                print(f"leg {k} load error: {e}")

    def _save_state(self):
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(
            {k: asdict(v) for k, v in self.legs.items()},
            indent=2, default=str
        ))

    def _log_event(self, event_type: str, leg: Leg, **kwargs):
        rec = {
            "ts": int(time.time() * 1000),
            "ts_iso": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "order_link_id": leg.order_link_id,
            "symbol": leg.symbol,
            "side": leg.side,
            "tier": leg.tier,
            **kwargs,
        }
        EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with EVENT_LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")

    def get_active_legs(self, state: str = "PLACED") -> list[Leg]:
        return [l for l in self.legs.values() if l.state == state]

    def count_per_symbol_placed(self, symbol: str) -> int:
        return sum(1 for l in self.legs.values() if l.symbol == symbol and l.state == "PLACED")

    def count_per_state(self, state: str) -> int:
        return sum(1 for l in self.legs.values() if l.state == state)

    def can_place_leg(self, symbol: str, tier: int) -> tuple[bool, str]:
        """Check if we can place another leg for this symbol/tier."""
        # Per-symbol limit (tier-specific)
        per_sym = sum(1 for l in self.legs.values() if l.symbol == symbol and l.state == "PLACED")
        if per_sym >= TIERS[tier]["max_per_symbol"]:
            return False, f"per-symbol limit reached ({per_sym}/{TIERS[tier]['max_per_symbol']})"
        # Global PLACED limit
        total_placed = self.count_per_state("PLACED")
        if total_placed >= MAX_LEGS_PLACED:
            return False, f"global MAX_LEGS_PLACED reached ({total_placed}/{MAX_LEGS_PLACED})"
        # Per-symbol FILLED limit (max_positions)
        per_sym_filled = sum(1 for l in self.legs.values() if l.symbol == symbol and l.state in ("FILLED", "BRACKET"))
        if per_sym_filled >= 1:
            return False, f"per-symbol FILLED limit (already in position)"
        return True, "ok"

    def place_leg(
        self,
        symbol: str,
        side: str,
        tier: int,
        pattern: str,
        signal_ts: int,
        entry_zone: float,
        confidence: float,
        regime: int,
        regime_name: str,
        metadata: dict = None,
        sl_hint: Optional[float] = None,
        tp_hint: Optional[float] = None,
    ) -> tuple[bool, str, Optional[Leg]]:
        """Place a new leg. Returns (success, reason, leg)."""
        ok, reason = self.can_place_leg(symbol, tier)
        if not ok:
            return False, reason, None

        # Sanity: structural levels must be coherent for the side.
        # BUY: SL strictly below entry, TP strictly above. Blocks garbage
        # candles (TUTUSDT entry=10.0 at px 0.05) from entering the book.
        meta = metadata or {}
        _sl = sl_hint if sl_hint is not None else meta.get("sl_hint")
        _tp = tp_hint if tp_hint is not None else meta.get("tp_hint")
        if entry_zone <= 0:
            return False, f"bad entry_zone {entry_zone}", None
        if side == "BUY":
            if _sl is not None and _sl >= entry_zone:
                return False, f"sanity: SL {_sl} >= entry {entry_zone}", None
            if _tp is not None and _tp <= entry_zone:
                return False, f"sanity: TP {_tp} <= entry {entry_zone}", None
        else:
            if _sl is not None and _sl <= entry_zone:
                return False, f"sanity: SL {_sl} <= entry {entry_zone}", None
            if _tp is not None and _tp >= entry_zone:
                return False, f"sanity: TP {_tp} >= entry {entry_zone}", None

        # Generate order_link_id
        seq = sum(1 for l in self.legs.values() if l.symbol == symbol and l.signal_ts == signal_ts)
        order_link_id = f"dn-sweep-{symbol}-{signal_ts}-{tier}-{seq}"

        # Dedup: same symbol+signal_ts already placed (any state) → skip.
        # Survives restarts (unlike in-memory LAST_SIGNAL_TS in the detector).
        for l in self.legs.values():
            if l.symbol == symbol and l.signal_ts == signal_ts and l.side == side:
                return False, f"duplicate signal (existing leg {l.order_link_id} state={l.state})", None

        offset_pct = TIERS[tier]["offset_pct"]
        # Convention: offset_pct is signed; negative = deeper (better fill for BUY).
        # For BUY: placed = entry * (1 + offset_pct/100) → offset -0.3% → placed 0.3% BELOW entry.
        # For SELL (short limit): placed = entry * (1 - offset_pct/100) → mirror.
        if side == "BUY":
            placed_price = entry_zone * (1 + offset_pct / 100)
        else:  # SELL
            placed_price = entry_zone * (1 - offset_pct / 100)

        now_ms = int(time.time() * 1000)
        leg = Leg(
            order_link_id=order_link_id,
            symbol=symbol,
            side=side,
            tier=tier,
            pattern=pattern,
            signal_ts=signal_ts,
            signal_ts_iso=datetime.fromtimestamp(signal_ts/1000, tz=timezone.utc).isoformat(),
            entry_zone=entry_zone,
            placed_price=round(placed_price, 8),
            placed_ts=now_ms,
            placed_ts_iso=datetime.now(timezone.utc).isoformat(),
            state="PLACED",
            confidence=confidence,
            regime=regime,
            regime_name=regime_name,
            metadata=metadata or {},
        )

        if self.paper_mode:
            # Simulated place — no API call
            self.legs[order_link_id] = leg
            self._save_state()
            self._log_event("PLACED_PAPER", leg, placed_price=leg.placed_price)
            return True, "placed (paper)", leg
        else:
            # LIVE: actual Bybit API call
            # TODO: implement via BybitClient.create_order
            return False, "live mode not implemented", None

    def amend_leg(self, order_link_id: str, new_price: float, reason: str) -> tuple[bool, str]:
        """Amend (modify price) of an existing PLACED leg."""
        leg = self.legs.get(order_link_id)
        if not leg:
            return False, "leg not found"
        if leg.state != "PLACED":
            return False, f"leg state is {leg.state}, not PLACED"
        if leg.amend_count >= MAX_AMENDS_PER_LEG:
            return False, f"amend budget exhausted ({leg.amend_count}/{MAX_AMENDS_PER_LEG})"
        old_price = leg.placed_price
        leg.placed_price = round(new_price, 8)
        leg.amend_count += 1
        leg.amend_history.append({"ts": int(time.time()*1000), "old": old_price, "new": new_price, "reason": reason})
        self._save_state()
        self._log_event("AMENDED", leg, old_price=old_price, new_price=new_price, reason=reason)
        return True, "amended"

    def cancel_leg(self, order_link_id: str, reason: str) -> tuple[bool, str]:
        """Cancel a PLACED leg."""
        leg = self.legs.get(order_link_id)
        if not leg:
            return False, "leg not found"
        if leg.state != "PLACED":
            return False, f"leg state is {leg.state}, not PLACED"
        leg.state = "CANCELLED"
        self._save_state()
        self._log_event("CANCELLED", leg, reason=reason)
        return True, "cancelled"

    def fill_leg(self, order_link_id: str, fill_price: Optional[float] = None,
                 fill_ts: Optional[int] = None) -> tuple[bool, str]:
        """Mark leg as filled.

        Paper fill model: a post-only LIMIT BUY at placed_price fills AT
        placed_price (maker), not at the current market price. Recording
        market price as fill_price was inflating entries (ETHUSDT chased
        from 2467 up to 2503).

        Structural SL/TP: SL = sweep structure level (sl_hint, below the
        sweep wick), NOT entry*0.997. TP = 2R from the actual entry.
        """
        leg = self.legs.get(order_link_id)
        if not leg:
            return False, "leg not found"
        if leg.state != "PLACED":
            return False, f"leg state is {leg.state}, not PLACED"
        # Maker fill: at the limit price we placed
        leg.fill_price = leg.placed_price
        leg.fill_ts = fill_ts if fill_ts is not None else int(time.time() * 1000)
        leg.fill_ts_iso = datetime.fromtimestamp(leg.fill_ts/1000, tz=timezone.utc).isoformat()
        leg.state = "FILLED"
        # Cancel peer legs for same symbol (OCO simulation)
        peers_cancelled = []
        for k, l in self.legs.items():
            if k != order_link_id and l.symbol == leg.symbol and l.state == "PLACED":
                l.state = "CANCELLED"
                peers_cancelled.append(k)
        # Structural SL: prefer sweep structure level from metadata, never
        # fall back to a blind entry*0.997 (that created fake TP/SL).
        # v2 exit model (VALIDATION_v2_SL800.md): SL-only, hard-capped at
        # 8% below fill; NO TP — exit at 4h hold end.
        meta = leg.metadata or {}
        sl_hint = meta.get("sl_hint")
        sl_candidates = []
        if sl_hint is not None and 0 < sl_hint < leg.fill_price:
            sl_candidates.append(sl_hint)
        sl_candidates.append(leg.fill_price * (1 - 0.08))  # 8% hard cap
        leg.sl_price = round(max(sl_candidates), 8)
        # No TP: keep tp_price None. Legacy code that reads tp_price must
        # treat None as "no TP → TIME exit".
        leg.tp_price = None
        # Set state to BRACKET_PLACED (in live mode would call set_trading_stop)
        leg.state = "BRACKET"
        self._save_state()
        self._log_event("FILLED", leg, fill_price=leg.fill_price)
        self._log_event("BRACKET_PLACED", leg, sl=leg.sl_price, tp=leg.tp_price)
        for pk in peers_cancelled:
            self._log_event("PEER_CANCELLED_OCO", self.legs[pk], trigger_leg=order_link_id)
        return True, "filled + bracket"

    def close_leg(self, order_link_id: str, exit_price: Optional[float] = None,
                  outcome: str = "TIME", exit_ts: Optional[int] = None) -> tuple[bool, str]:
        """Close a leg (TP/SL/TIME).

        For TP/SL outcomes the exit MUST be at the structural level (tp_price /
        sl_price), not the market price at evaluation time — otherwise net PnL
        is mis-measured (old bug: "TP" outcomes with ~0 or negative net).
        exit_price is only used for TIME exits (market close at horizon).
        """
        leg = self.legs.get(order_link_id)
        if not leg:
            return False, "leg not found"
        if leg.state not in ("FILLED", "BRACKET"):
            return False, f"leg state is {leg.state}, not BRACKET"
        if leg.fill_price is None:
            return False, "no fill price recorded"
        # Enforce structural exit price for TP/SL outcomes
        if outcome == "TP" and leg.tp_price is not None:
            exit_price = leg.tp_price
        elif outcome == "SL" and leg.sl_price is not None:
            exit_price = leg.sl_price
        elif exit_price is None:
            return False, f"outcome {outcome} requires exit_price"
        leg.exit_price = round(exit_price, 8)
        leg.exit_ts = exit_ts if exit_ts is not None else int(time.time() * 1000)
        leg.exit_outcome = outcome
        if leg.side == "BUY":
            leg.ret_gross = (leg.exit_price - leg.fill_price) / leg.fill_price
        else:
            leg.ret_gross = (leg.fill_price - leg.exit_price) / leg.fill_price
        leg.ret_net_maker = leg.ret_gross - 0.0010  # 10bps maker cost
        leg.state = f"CLOSED_{outcome}"
        self._save_state()
        self._log_event("CLOSED", leg, exit_price=leg.exit_price, outcome=outcome,
                       ret_gross=leg.ret_gross, ret_net_maker=leg.ret_net_maker)
        # Log to paper_trades for analysis
        rec = asdict(leg)
        PAPER_TRADES_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PAPER_TRADES_LOG.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return True, "closed"

    def expire_stale_legs(self):
        """Move expired PLACED legs to EXPIRED state."""
        now_ms = int(time.time() * 1000)
        for k, leg in list(self.legs.items()):
            if leg.state != "PLACED":
                continue
            expiry_min = TIERS[leg.tier]["expiry_min"]
            age_ms = now_ms - leg.placed_ts
            if age_ms > expiry_min * 60 * 1000:
                leg.state = "EXPIRED"
                self._save_state()
                self._log_event("EXPIRED", leg)

    def cleanup_old_legs(self, max_age_hours: int = 48):
        """Remove legs older than max_age_hours (state file size management)."""
        cutoff = int(time.time() * 1000) - max_age_hours * 3600 * 1000
        old_ids = [k for k, l in self.legs.items()
                  if l.placed_ts < cutoff and (l.state.startswith("CLOSED") or l.state == "EXPIRED")]
        for k in old_ids:
            del self.legs[k]
        if old_ids:
            self._save_state()

    def get_stats(self) -> dict:
        """Current state summary."""
        state_counts = defaultdict(int)
        for l in self.legs.values():
            state_counts[l.state] += 1
        tier_counts = defaultdict(int)
        for l in self.legs.values():
            if l.state == "PLACED":
                tier_counts[l.tier] += 1
        total_pnl = sum(l.ret_net_maker or 0 for l in self.legs.values() if l.state.startswith("CLOSED"))
        n_closed = sum(1 for l in self.legs.values() if l.state.startswith("CLOSED"))
        wins = sum(1 for l in self.legs.values() if l.state.startswith("CLOSED") and (l.ret_gross or 0) > 0)
        return {
            "state_counts": dict(state_counts),
            "tier_counts_placed": dict(tier_counts),
            "n_closed": n_closed,
            "n_wins": wins,
            "wr_pct": 100 * wins / n_closed if n_closed else 0,
            "total_pnl_pct": total_pnl,
        }


if __name__ == "__main__":
    mgr = LegManager(paper_mode=True)
    print(f"loaded {len(mgr.legs)} legs")
    print(f"stats: {mgr.get_stats()}")
