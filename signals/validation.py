"""
validation.py — Two-Contour forensic validation infrastructure.

Read-only observation layer that records:
  1. Candidate funnel (how many at each stage including score gate)
  2. Decision traces (full audit trail per candidate)
  3. Counterfactual outcomes (what happened after each decision)
  4. Below-min_score counterfactuals
  5. OLD vs NEW counterfactual comparison

NO production changes. Pure shadow measurement.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from threading import Lock
from collections import defaultdict

ROOT = Path("/root/tradingos")
VALIDATION_DIR = ROOT / "memory" / "validation"
VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

FUNNEL_LOG = VALIDATION_DIR / "candidate_funnel.jsonl"
DECISION_LOG = VALIDATION_DIR / "decision_trace.jsonl"
COUNTERFACTUAL_LOG = VALIDATION_DIR / "counterfactual.jsonl"
BELOW_MIN_SCORE_LOG = VALIDATION_DIR / "below_min_score.jsonl"
OLD_VS_NEW_LOG = VALIDATION_DIR / "old_vs_new.jsonl"

_lock = Lock()


# Score distribution buckets
SCORE_BUCKETS = [
    ("lt_50", 0, 50),
    ("50_59", 50, 60),
    ("60_69", 60, 70),
    ("70_79", 70, 80),
    ("80_plus", 80, 101),
]


@dataclass
class FunnelSnapshot:
    """Single scan cycle funnel snapshot including score distribution."""
    ts: float
    iso: str
    # Raw & quality
    raw_candidates: int
    quality_reject: int
    liquidity_reject: int
    stale_reject: int
    flat_reject: int
    mtf_reject: int
    entry_gate_reject: int
    tp_unreachable: int
    rr_reject: int
    # Score distribution (candidates passing quality)
    score_lt_50: int = 0
    score_50_59: int = 0
    score_60_69: int = 0
    score_70_79: int = 0
    score_80_plus: int = 0
    score_lt_50_long: int = 0
    score_50_59_long: int = 0
    score_60_69_long: int = 0
    score_70_79_long: int = 0
    score_80_plus_long: int = 0
    score_lt_50_short: int = 0
    score_50_59_short: int = 0
    score_60_69_short: int = 0
    score_70_79_short: int = 0
    score_80_plus_short: int = 0
    # Contour distribution (candidates >= min_score)
    market_candidates: int = 0
    limit_candidates: int = 0
    no_trade: int = 0
    long_candidates: int = 0
    short_candidates: int = 0
    market_long: int = 0
    market_short: int = 0
    limit_long: int = 0
    limit_short: int = 0
    no_trade_long: int = 0
    no_trade_short: int = 0


@dataclass
class DecisionTrace:
    """Complete decision trace for one candidate."""
    ts: float
    iso: str
    symbol: str
    direction: str  # LONG / SHORT
    # Raw features
    h1_trend_score: int
    m15_structure_score: int
    momentum_score: int
    volume_score: int
    funding_oi_score: int
    entry_quality_score: int
    total_score: int
    # Quality gates
    h1_notional_med: float
    h1_move_pct: float
    data_age_hours: float
    quality_passed: bool
    quality_reject_reason: str
    # Classifier
    classifier_version: str
    contour: str  # MARKET / LIMIT / NO_TRADE
    contour_confidence: float
    contour_reasoning: list[str]
    # Thresholds used
    market_min_momentum_score: int
    market_min_vol_ratio: float
    market_min_h1_trend_score: int
    market_max_dist_to_ema20_pct: float
    market_require_mtf_agree: bool
    limit_min_pullback_mature: bool
    limit_min_pullback_dist_pct: float
    limit_min_pullback_dist_atr_mult: float
    limit_max_rr_from_current: float
    limit_require_tp_beyond_7d: bool
    limit_min_wait_rr: float
    # Rejection
    rejection_reason: str
    # Context
    is_stock: bool
    h4_trend: str | None
    d1_trend: str | None
    mtf_agree: bool | None
    # Prices
    signal_price: float
    wait_limit_entry: float
    wait_limit_entry_deep: float
    sl: float
    final_tp: float
    atr: float
    vol_ratio: float
    dist_to_e20_pct: float


@dataclass
class CounterfactualSnapshot:
    """Price evolution after a decision."""
    ts: float
    iso: str
    symbol: str
    direction: str
    contour: str  # MARKET / LIMIT / NO_TRADE
    reason: str
    # Reference prices
    signal_price: float
    current_price: float
    sl: float
    final_tp: float
    wait_limit_entry: float
    wait_limit_entry_deep: float
    atr: float
    # Time horizons (seconds after decision)
    h_15: dict | None = None
    h_30: dict | None = None
    h_60: dict | None = None
    h_120: dict | None = None
    h_300: dict | None = None
    h_900: dict | None = None


@dataclass
class BelowMinScoreSnapshot:
    """Counterfactual tracking for candidates that passed quality but scored below min_score."""
    ts: float
    iso: str
    symbol: str
    direction: str
    # Score info
    total_score: int
    score_components: dict  # h1_trend, m15_structure, momentum, volume, funding_oi, entry_quality
    quality_passed: bool
    # Reference prices
    signal_price: float
    sl: float
    final_tp: float
    atr: float
    vol_ratio: float
    dist_to_e20_pct: float
    # Time horizons
    h_15: dict | None = None
    h_30: dict | None = None
    h_60: dict | None = None
    h_120: dict | None = None
    h_300: dict | None = None
    h_900: dict | None = None


@dataclass
class OldVsNewSnapshot:
    """OLD bot decision vs NEW classifier decision counterfactual comparison."""
    ts: float
    iso: str
    symbol: str
    direction: str
    # Old bot decision
    old_trade_decision: str  # ALLOW / WAIT_LIMIT / SKIP (from old trade_decision logic)
    old_contour: str  # derived: MARKET / LIMIT / NO_TRADE
    # New classifier decision
    new_contour: str  # MARKET / LIMIT / NO_TRADE
    new_confidence: float
    # Reference prices
    signal_price: float
    sl: float
    final_tp: float
    wait_limit_entry: float
    wait_limit_entry_deep: float
    atr: float
    # Time horizons with outcome comparison
    h_15: dict | None = None
    h_30: dict | None = None
    h_60: dict | None = None
    h_120: dict | None = None
    h_300: dict | None = None
    h_900: dict | None = None


def _make_horizon(px: float, signal_px: float, sl: float, tp: float, direction: str) -> dict:
    """Calculate MFE/MAE/return at a price point."""
    is_long = direction.upper() in ("LONG", "BUY")
    if is_long:
        mfe = (px - signal_px) / max(signal_px - sl, 1e-9) if sl else 0.0
        mae = (px - signal_px) / max(signal_px - sl, 1e-9) if sl else 0.0  # negative = MAE
        ret = (px - signal_px) / signal_px * 100 if signal_px else 0.0
    else:
        mfe = (signal_px - px) / max(sl - signal_px, 1e-9) if sl else 0.0
        mae = (signal_px - px) / max(sl - signal_px, 1e-9) if sl else 0.0
        ret = (signal_px - px) / signal_px * 100 if signal_px else 0.0
    return {
        "price": round(px, 6),
        "mfe_r": round(mfe, 4),
        "mae_r": round(mae, 4),
        "ret_pct": round(ret, 4),
        "sl_distance_pct": round(abs(px - sl) / px * 100, 4) if sl and px else 0.0,
        "tp_distance_pct": round(abs(tp - px) / px * 100, 4) if tp and px else 0.0,
    }


class ValidationRecorder:
    """Thread-safe recorder for validation data."""

    def __init__(self):
        self._pending_counterfactuals: dict[str, CounterfactualSnapshot] = {}

    def record_funnel(self, snapshot: FunnelSnapshot) -> None:
        with _lock:
            with FUNNEL_LOG.open("a") as f:
                f.write(json.dumps(asdict(snapshot), ensure_ascii=False) + "\n")

    def record_decision(self, trace: DecisionTrace) -> None:
        with _lock:
            with DECISION_LOG.open("a") as f:
                f.write(json.dumps(asdict(trace), ensure_ascii=False) + "\n")

    def start_counterfactual(self, trace: DecisionTrace, current_price: float) -> None:
        """Initialize counterfactual tracking for a decision."""
        key = f"{trace.symbol}_{trace.direction}_{int(trace.ts)}"
        cf = CounterfactualSnapshot(
            ts=trace.ts,
            iso=trace.iso,
            symbol=trace.symbol,
            direction=trace.direction,
            contour=trace.contour,
            reason=trace.rejection_reason,
            signal_price=trace.signal_price,
            current_price=current_price,
            sl=trace.sl,
            final_tp=trace.final_tp,
            wait_limit_entry=trace.wait_limit_entry,
            wait_limit_entry_deep=trace.wait_limit_entry_deep,
            atr=trace.atr,
        )
        with _lock:
            self._pending_counterfactuals[key] = cf

    def update_counterfactuals(self, prices: dict[str, float]) -> None:
        """Update all pending counterfactuals with current prices.
        Called periodically (e.g., every 15s) from position monitor."""
        now = time.time()
        to_remove = []
        with _lock:
            for key, cf in self._pending_counterfactuals.items():
                sym = cf.symbol
                if sym not in prices:
                    continue
                px = prices[sym]
                elapsed = now - cf.ts
                horizon = _make_horizon(px, cf.signal_price, cf.sl, cf.final_tp, cf.direction)
                if elapsed >= 15 and cf.h_15 is None:
                    cf.h_15 = horizon
                if elapsed >= 30 and cf.h_30 is None:
                    cf.h_30 = horizon
                if elapsed >= 60 and cf.h_60 is None:
                    cf.h_60 = horizon
                if elapsed >= 120 and cf.h_120 is None:
                    cf.h_120 = horizon
                if elapsed >= 300 and cf.h_300 is None:
                    cf.h_300 = horizon
                if elapsed >= 900 and cf.h_900 is None:
                    cf.h_900 = horizon
                    # Final horizon reached — persist and remove
                    with COUNTERFACTUAL_LOG.open("a") as f:
                        f.write(json.dumps(asdict(cf), ensure_ascii=False) + "\n")
                    to_remove.append(key)
            for k in to_remove:
                del self._pending_counterfactuals[k]

    def get_pending_count(self) -> int:
        with _lock:
            return len(self._pending_counterfactuals)


# Global singleton
_recorder = ValidationRecorder()


def get_recorder() -> ValidationRecorder:
    return _recorder


def load_funnel() -> list[FunnelSnapshot]:
    out = []
    if not FUNNEL_LOG.exists():
        return out
    for line in FUNNEL_LOG.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(FunnelSnapshot(**d))
        except Exception:
            pass
    return out


def load_decisions() -> list[DecisionTrace]:
    out = []
    if not DECISION_LOG.exists():
        return out
    for line in DECISION_LOG.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(DecisionTrace(**d))
        except Exception:
            pass
    return out


def load_counterfactuals() -> list[CounterfactualSnapshot]:
    out = []
    if not COUNTERFACTUAL_LOG.exists():
        return out
    for line in COUNTERFACTUAL_LOG.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(CounterfactualSnapshot(**d))
        except Exception:
            pass
    return out


def generate_report() -> str:
    """Generate human-readable validation report."""
    funnels = load_funnel()
    decisions = load_decisions()
    counterfactuals = load_counterfactuals()

    if not funnels and not decisions:
        return "No validation data yet."

    lines = [
        "=" * 70,
        "TWO-CONTOUR VALIDATION REPORT",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Classifier version: {decisions[0].classifier_version if decisions else 'unknown'}",
        "=" * 70,
    ]

    # Funnel summary
    if funnels:
        latest = funnels[-1]
        lines += [
            "",
            "### FUNNEL (last scan)",
            f"  Raw candidates:        {latest.raw_candidates}",
            f"  Quality reject:        {latest.quality_reject}",
            f"    Liquidity:           {latest.liquidity_reject}",
            f"    Stale data:          {latest.stale_reject}",
            f"    Flat market:         {latest.flat_reject}",
            f"  MTF reject:            {latest.mtf_reject}",
            f"  Entry gate reject:     {latest.entry_gate_reject}",
            f"  TP unreachable:        {latest.tp_unreachable}",
            f"  RR reject:             {latest.rr_reject}",
            f"  ────────────────────────",
            f"  MARKET:                {latest.market_candidates} (L:{latest.market_long} S:{latest.market_short})",
            f"  LIMIT:                 {latest.limit_candidates} (L:{latest.limit_long} S:{latest.limit_short})",
            f"  NO_TRADE:              {latest.no_trade} (L:{latest.no_trade_long} S:{latest.no_trade_short})",
        ]

    # Decision distribution
    if decisions:
        from collections import Counter
        contours = Counter(d.contour for d in decisions)
        reasons = Counter(d.rejection_reason for d in decisions if d.contour == "NO_TRADE")
        lines += [
            "",
            "### DECISION DISTRIBUTION (all time)",
            f"  Total decisions:       {len(decisions)}",
            f"  MARKET:                {contours.get('MARKET', 0)}",
            f"  LIMIT:                 {contours.get('LIMIT', 0)}",
            f"  NO_TRADE:              {contours.get('NO_TRADE', 0)}",
        ]
        if reasons:
            lines.append("  NO_TRADE reasons:")
            for r, c in reasons.most_common():
                lines.append(f"    {r}: {c}")

    # Counterfactual summary
    if counterfactuals:
        lines += [
            "",
            "### COUNTERFACTUAL (completed horizons)",
            f"  Total tracked:         {len(counterfactuals)}",
        ]
        by_contour = {}
        for cf in counterfactuals:
            by_contour.setdefault(cf.contour, []).append(cf)

        for contour, items in by_contour.items():
            lines.append(f"  {contour}: {len(items)}")
            for h in ["h_15", "h_30", "h_60", "h_120", "h_300", "h_900"]:
                vals = [getattr(i, h) for i in items if getattr(i, h)]
                if vals:
                    mfe = sum(v["mfe_r"] for v in vals) / len(vals)
                    mae = sum(v["mae_r"] for v in vals) / len(vals)
                    ret = sum(v["ret_pct"] for v in vals) / len(vals)
                    lines.append(f"    {h}: MFE={mfe:+.2f}R MAE={mae:+.2f}R ret={ret:+.2f}% (n={len(vals)})")

    return "\n".join(lines)


if __name__ == "__main__":
    print(generate_report())