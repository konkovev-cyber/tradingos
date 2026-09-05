"""
DecisionEngine v2 — агрегация сигналов от множества стратегий.
Улучшения:
- PF-фильтр: исключает стратегии с PF < 1.0 (>20 сделок)
- Вето: при конфликте BUY/SELL с перевесом < 2x
- Взвешенное голосование с учётом PF
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum

from tradingos.signals.models.types import Action, Decision, Direction
from tradingos.signals.models.signal import Signal

log = logging.getLogger("DecisionEngineV2")


@dataclass
class AggregatedDecision:
    symbol: str
    action: Action = Action.NO_TRADE
    size: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    entry_price: float = 0.0
    final_score: float = 0.0
    confidence: float = 0.0
    source_strategy: str = ""
    reason: List[str] = field(default_factory=list)
    breakdown: Dict[str, Any] = field(default_factory=dict)
    reject_stage: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    
    @property
    def is_trade(self) -> bool:
        return self.action != Action.NO_TRADE
    
    @property
    def risk_reward(self) -> float:
        if self.entry_price <= 0 or self.sl <= 0:
            return 0.0
        sl_dist = abs(self.entry_price - self.sl)
        tp_dist = abs(self.entry_price - self.tp)
        return tp_dist / sl_dist if sl_dist > 0 else 0.0
    
    def to_decision(self) -> Decision:
        return Decision(
            symbol=self.symbol, action=self.action, confidence=self.confidence,
            size=self.size, stop_loss=self.sl, take_profit=self.tp,
            entry_price=self.entry_price, reason=self.reason,
            reject_stage=self.reject_stage, final_score=self.final_score,
            metadata={"breakdown": self.breakdown, "source_strategy": self.source_strategy},
        )


class DecisionEngineV2:
    def __init__(self, config: dict = None):
        self.config = config or {}
        self.strategy_weights: Dict[str, float] = {}
        self._load_weights()
        self.max_spread_pct = self.config.get("max_spread_pct", 0.01)
        # Read min_score from risk config, not top-level (top-level doesn't exist in YAML)
        risk_cfg = self.config.get("risk", {})
        self.min_score = risk_cfg.get("min_score", self.config.get("min_score", 30))
        self.min_rr = risk_cfg.get("min_risk_reward", self.config.get("min_risk_reward", 1.0))
        self._strategy_pf: Dict[str, float] = {}
        self._strategy_trades: Dict[str, int] = {}
    
    def _load_weights(self):
        strategies_config = self.config.get("strategies", {})
        for name, cfg in strategies_config.items():
            if cfg.get("enabled", True):
                self.strategy_weights[name] = cfg.get("weight", 1.0)
    
    def set_strategy_weights(self, weights: Dict[str, float]):
        self.strategy_weights.update(weights)
    
    def update_strategy_stats(self, name: str, pf: float, trades: int):
        self._strategy_pf[name] = pf
        self._strategy_trades[name] = trades
    
    def evaluate(self, symbol: str, signals: Dict[str, Signal], ctx, portfolio_snapshot: dict = None) -> AggregatedDecision:
        if not signals:
            return AggregatedDecision(symbol=symbol, action=Action.NO_TRADE, reason=["No signals"])
        
        # ═══════════════════════════════════════════════════════════════
        # WEIGHT SYSTEM: PF влияет на score, но НЕ блокирует
        # Старая логика "if PF < 1.0 → exclude" — УДАЛЕНА
        # ═══════════════════════════════════════════════════════════════
        
        # Вместо блокировки — учитываем вес в score
        # DecisionEngineV2 уже получает сигналы с применёнными весами из runner_v5
        filtered = signals  # Уже применены веса из PF
        
        if not filtered:
            return AggregatedDecision(symbol=symbol, action=Action.NO_TRADE, reason=["No signals after weighting"])
        
        # 2. Veto при конфликте BUY/SELL
        # Only veto when conviction is TRULY equal — both sides have
        # similar number of strategies AND similar weighted scores
        has_buy = any(s.is_buy for s in filtered.values())
        has_sell = any(s.is_sell for s in filtered.values())
        if has_buy and has_sell:
            buy_strats = [n for n, s in filtered.items() if s.is_buy]
            sell_strats = [n for n, s in filtered.items() if s.is_sell]
            buy_sc = sum(s.score * self.strategy_weights.get(n, 1.0) for n, s in filtered.items() if s.is_buy)
            sell_sc = sum(s.score * self.strategy_weights.get(n, 1.0) for n, s in filtered.items() if s.is_sell)
            ratio = buy_sc / sell_sc if sell_sc > 0 else float('inf')
            strat_ratio = len(buy_strats) / len(sell_strats) if len(sell_strats) > 0 else float('inf')
            # Veto ONLY when: scores nearly equal AND strategy count nearly equal
            # This prevents veto when one side has clear majority
            if (0.85 < ratio < 1.18) and (0.7 < strat_ratio < 1.43):
                return AggregatedDecision(symbol=symbol, action=Action.NO_TRADE,
                    reason=[f"Veto: buy={buy_sc:.0f}({len(buy_strats)}s) sell={sell_sc:.0f}({len(sell_strats)}s) ratio={ratio:.2f}"], reject_stage="VETO")
        
        # 3. Взвешенное голосование
        buy_score = 0.0
        sell_score = 0.0
        buy_weight = 0.0
        sell_weight = 0.0
        breakdown = {}
        best_signal = None
        best_score = 0
        
        for name, signal in filtered.items():
            w = self.strategy_weights.get(name, 1.0)
            ws = signal.score * w
            breakdown[name] = {"direction": signal.direction.value, "score": signal.score,
                               "weight": w, "weighted_score": ws, "reason": signal.reason,
                               "pf": self._strategy_pf.get(name, 1.0)}
            if signal.is_buy:
                buy_score += ws
                buy_weight += w
            elif signal.is_sell:
                sell_score += ws
                sell_weight += w
            if signal.score > best_score and signal.is_actionable:
                best_score = signal.score
                best_signal = signal
        
        total_w = buy_weight + sell_weight
        if total_w == 0:
            return AggregatedDecision(symbol=symbol, action=Action.NO_TRADE, reason=["No actionable signals"], breakdown=breakdown)
        
        if buy_score > sell_score:
            direction = Action.BUY
            final_score = buy_score / max(buy_weight, 0.01)
            source = self._find_best(filtered, Direction.BUY)
        elif sell_score > buy_score:
            direction = Action.SELL
            final_score = sell_score / max(sell_weight, 0.01)
            source = self._find_best(filtered, Direction.SELL)
        else:
            bc = max((s.confidence for s in filtered.values() if s.is_buy), default=0)
            sc = max((s.confidence for s in filtered.values() if s.is_sell), default=0)
            if bc > sc:
                direction = Action.BUY
                final_score = buy_score / max(buy_weight, 0.01)
                source = self._find_best(filtered, Direction.BUY)
            elif sc > bc:
                direction = Action.SELL
                final_score = sell_score / max(sell_weight, 0.01)
                source = self._find_best(filtered, Direction.SELL)
            else:
                return AggregatedDecision(symbol=symbol, action=Action.NO_TRADE,
                    reason=["Conflicting: equal scores"], breakdown=breakdown)
        
        if final_score < self.min_score:
            return AggregatedDecision(symbol=symbol, action=Action.NO_TRADE, final_score=final_score,
                reason=[f"Score {final_score:.1f} < {self.min_score}"], breakdown=breakdown, reject_stage="SCORE")
        
        sl = best_signal.sl if best_signal else 0
        tp = best_signal.tp if best_signal else 0
        price = ctx.snapshot.last_price if hasattr(ctx, 'snapshot') else 0
        
        if sl and tp and price:
            rr = abs(tp - price) / abs(price - sl) if abs(price - sl) > 0 else 0
            if rr < self.min_rr:
                return AggregatedDecision(symbol=symbol, action=Action.NO_TRADE, final_score=final_score,
                    reason=[f"R:R {rr:.2f} < {self.min_rr}"], breakdown=breakdown, reject_stage="RISK_REWARD")
        
        confidence = final_score / 100.0
        return AggregatedDecision(symbol=symbol, action=direction, size=0.0, sl=sl, tp=tp,
            entry_price=price, final_score=final_score, confidence=confidence,
            source_strategy=source, reason=[f"{direction.value} by {source}: score={final_score:.1f}"],
            breakdown=breakdown)
    
    def _find_best(self, signals: Dict[str, Signal], direction: Direction) -> str:
        best_name = "unknown"
        best_score = 0
        for name, signal in signals.items():
            if signal.direction == direction and signal.score > best_score:
                best_score = signal.score
                best_name = name
        return best_name
