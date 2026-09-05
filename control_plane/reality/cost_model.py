"""
control_plane/reality/cost_model.py
Cost Model — BingX-specific cost assumptions.

Read-only. No LIVE API. Pure cost simulation.
"""
from dataclasses import dataclass


@dataclass
class BingXCostModel:
    """BingX Futures cost model (BingX standard tier)."""
    taker_fee_pct: float = 0.0006   # 0.06% per side
    maker_fee_pct: float = 0.0002   # 0.02% per side
    spread_bps: float = 0.5
    slippage_bps: float = 1.0
    funding_rate_pct: float = 0.0001  # ~0.01% per 8h, daily
    latency_ms: float = 100.0
    fill_rate: float = 0.98

    def total_round_trip_cost_pct(self) -> float:
        """Round-trip cost as percentage of position value."""
        return (self.taker_fee_pct * 2) + (self.spread_bps / 10000 * 2) + (self.slippage_bps / 10000)

    def calculate_costs(self, position_value: float, action: str) -> dict:
        """Calculate dollar costs for a given action on a position."""
        if action == "HOLD":
            return {
                "commission": 0.0,
                "spread": 0.0,
                "slippage": 0.0,
                "funding": position_value * self.funding_rate_pct,
                "latency_penalty": 0.0,
                "total": position_value * self.funding_rate_pct,
            }
        elif action == "MOVE_SL_BE":
            return {
                "commission": 0.0,
                "spread": 0.0,
                "slippage": 0.0,
                "funding": position_value * self.funding_rate_pct,
                "latency_penalty": 0.0,
                "total": position_value * self.funding_rate_pct,
            }
        else:
            commission = position_value * self.taker_fee_pct
            spread = position_value * (self.spread_bps / 10000)
            slippage = position_value * (self.slippage_bps / 10000)
            funding = position_value * self.funding_rate_pct
            latency_penalty = position_value * (self.latency_ms / 1000 * 0.0001)
            total = commission + spread + slippage + funding + latency_penalty
            return {
                "commission": commission,
                "spread": spread,
                "slippage": slippage,
                "funding": funding,
                "latency_penalty": latency_penalty,
                "total": total,
            }
