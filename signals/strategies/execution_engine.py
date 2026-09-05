"""
Execution Intelligence Layer v7.4 — Проверка качества исполнения.

Архитектура:
    FEATURES → Probability → Regime → Integrity
                ↓
         EXECUTION CHECK (NEW 🔥)
                ↓
         SPREAD FILTER
         LIQUIDITY CHECK
         ORDER BOOK PRESSURE
         SLIPPAGE ESTIMATOR
                ↓
         EXECUTION SCORE
                ↓
         POSITION SIZING
                ↓
         ORDER EXECUTION

Философия:
- Signal ≠ Execution
- Хороший сигнал при плохом исполнении = плохой трейд
- Фильтруем тонкие стаканы, широкие спреды, проскальзывание
"""
import logging
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

log = logging.getLogger("ExecutionEngine")


@dataclass
class ExecutionCheckResult:
    """Результат проверки качества исполнения."""
    spread_ok: bool
    spread_value: float
    liquidity_score: float
    orderbook_pressure: str
    slippage_estimate: float
    execution_score: float
    can_execute: bool
    reason: str
    position_size_multiplier: float


class ExecutionIntelligenceEngine:
    """
    Execution Intelligence Layer v7.4.
    
    Проверяет качество исполнения перед входом:
    1. Spread Filter — спред не более 0.3%
    2. Liquidity Check — достаточная глубина стакана
    3. Order Book Pressure — быки/медведи баланс
    4. Slippage Estimator — проскальзывание при входе
    
    Результат:
    - execution_score 0-1 (качество исполнения)
    - position_size *= execution_score
    - can_execute = execution_score >= 0.4
    """
    
    # ==================== THRESHOLDS ====================
    
    # Максимальный спред (0.3%)
    MAX_SPREAD = 0.003
    
    # Минимальная ликвидность (сумма в USDT)
    MIN_LIQUIDITY = 100
    
    # Максимальное проскальзывание (0.2%)
    MAX_SLIPPAGE = 0.002
    
    # Минимальный execution score для входа
    MIN_EXECUTION_SCORE = 0.4
    
    def __init__(self):
        """Инициализация Execution Engine."""
        pass
    
    # ==================== MAIN API ====================
    
    def check_execution(
        self,
        orderbook: Dict,
        position_size_usdt: float,
        symbol: str = "UNKNOWN"
    ) -> ExecutionCheckResult:
        """
        Проверить качество исполнения.
        
        Args:
            orderbook: {
                "bids": [(price, volume), ...],
                "asks": [(price, volume), ...],
                "spread": float (опционально)
            }
            position_size_usdt: Размер позиции в USDT
            symbol: Символ для логирования
            
        Returns:
            ExecutionCheckResult с оценкой качества
        """
        # ========== STEP 1: SPREAD CHECK ==========
        spread_ok, spread_value = self._check_spread(orderbook)
        
        # ========== STEP 2: LIQUIDITY CHECK ==========
        liquidity_score = self._check_liquidity(orderbook)
        
        # ========== STEP 3: ORDER BOOK PRESSURE ==========
        orderbook_pressure = self._check_orderbook_pressure(orderbook)
        
        # ========== STEP 4: SLIPPAGE ESTIMATE ==========
        slippage_estimate = self._estimate_slippage(
            orderbook,
            position_size_usdt,
            side="BUY"  # Упрощённо, можно передать как параметр
        )
        
        # ========== STEP 5: EXECUTION SCORE ==========
        execution_score, position_size_multiplier = self._compute_execution_score(
            spread_ok=spread_ok,
            spread_value=spread_value,
            liquidity_score=liquidity_score,
            slippage_estimate=slippage_estimate,
            orderbook_pressure=orderbook_pressure
        )
        
        # ========== STEP 6: DECISION ==========
        can_execute = execution_score >= self.MIN_EXECUTION_SCORE
        
        reason = self._build_reason(
            spread_ok=spread_ok,
            spread_value=spread_value,
            liquidity_score=liquidity_score,
            slippage_estimate=slippage_estimate,
            execution_score=execution_score,
            can_execute=can_execute
        )
        
        # ========== LOG ==========
        log.debug(
            f"{symbol}: Execution — "
            f"Spread={spread_value:.3%} ({'OK' if spread_ok else 'HIGH'}) | "
            f"Liquidity={liquidity_score:.2f} | "
            f"Pressure={orderbook_pressure} | "
            f"Slippage={slippage_estimate:.3%} | "
            f"Score={execution_score:.2f} | "
            f"Size={position_size_multiplier:.0%}"
        )
        
        return ExecutionCheckResult(
            spread_ok=spread_ok,
            spread_value=spread_value,
            liquidity_score=liquidity_score,
            orderbook_pressure=orderbook_pressure,
            slippage_estimate=slippage_estimate,
            execution_score=execution_score,
            can_execute=can_execute,
            reason=reason,
            position_size_multiplier=position_size_multiplier
        )
    
    # ==================== PRIVATE METHODS ====================
    
    def _check_spread(self, orderbook: Dict) -> Tuple[bool, float]:
        """
        Проверить спред.
        
        Returns:
            (spread_ok, spread_value)
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        
        if not bids or not asks:
            return False, 1.0  # Нет данных → плохой спред
        
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        
        # Spread = (ask - bid) / bid
        spread = (best_ask - best_bid) / best_bid
        
        spread_ok = spread <= self.MAX_SPREAD
        
        return spread_ok, spread
    
    def _check_liquidity(self, orderbook: Dict) -> float:
        """
        Проверить ликвидность стакана.
        
        Returns:
            liquidity_score 0-1
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        
        if not bids or not asks:
            return 0.0
        
        # Сумма объёма в топ-5 уровнях
        bid_depth = sum([b[1] * b[0] for b in bids[:5]])  # USDT
        ask_depth = sum([a[1] * a[0] for a in asks[:5]])  # USDT
        
        total_liquidity = bid_depth + ask_depth
        
        # Score based on liquidity
        if total_liquidity >= 10000:
            return 1.0
        elif total_liquidity >= 5000:
            return 0.8
        elif total_liquidity >= 1000:
            return 0.6
        elif total_liquidity >= 100:
            return 0.4
        else:
            return 0.2
    
    def _check_orderbook_pressure(self, orderbook: Dict) -> str:
        """
        Определить давление в стакане.
        
        Returns:
            "bullish" | "bearish" | "neutral"
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        
        if not bids or not asks:
            return "neutral"
        
        # Объём в топ-3 уровнях
        bid_volume = sum([b[1] for b in bids[:3]])
        ask_volume = sum([a[1] for a in asks[:3]])
        
        # Pressure ratio
        if bid_volume > ask_volume * 1.5:
            return "bullish"
        elif ask_volume > bid_volume * 1.5:
            return "bearish"
        else:
            return "neutral"
    
    def _estimate_slippage(
        self,
        orderbook: Dict,
        position_size_usdt: float,
        side: str = "BUY"
    ) -> float:
        """
        Оценить проскальзывание при входе.
        
        Returns:
            slippage_estimate (0-1)
        """
        if side == "BUY":
            levels = orderbook.get("asks", [])
        else:
            levels = orderbook.get("bids", [])
        
        if not levels:
            return 0.01  # 1% если нет данных
        
        best_price = levels[0][0]
        remaining_size = position_size_usdt
        total_filled = 0.0
        total_cost = 0.0
        
        for price, volume in levels:
            level_value = price * volume
            
            if remaining_size <= level_value:
                total_cost += remaining_size * price
                total_filled += remaining_size
                break
            else:
                total_cost += level_value * price
                total_filled += level_value
                remaining_size -= level_value
        
        if total_filled == 0:
            return 0.01
        
        avg_price = total_cost / total_filled
        slippage = abs(avg_price - best_price) / best_price
        
        return slippage
    
    def _compute_execution_score(
        self,
        spread_ok: bool,
        spread_value: float,
        liquidity_score: float,
        slippage_estimate: float,
        orderbook_pressure: str
    ) -> Tuple[float, float]:
        """
        Вычислить execution score.
        
        Returns:
            (execution_score, position_size_multiplier)
        """
        score = 1.0
        
        # Spread penalty
        if not spread_ok:
            score *= 0.5  # Жёсткий штраф за широкий спред
        elif spread_value > 0.002:
            score *= 0.8  # Средний штраф
        
        # Liquidity penalty
        score *= liquidity_score
        
        # Slippage penalty
        if slippage_estimate > self.MAX_SLIPPAGE:
            score *= 0.6
        elif slippage_estimate > 0.001:
            score *= 0.8
        
        # Order book pressure bonus/penalty
        if orderbook_pressure == "bullish":
            # Бычий стакан — лучше для BUY
            score *= 1.05
        elif orderbook_pressure == "bearish":
            # Медвежий стакан — хуже для BUY
            score *= 0.95
        
        # Clamp score
        score = max(0.0, min(1.0, score))
        
        # Position size multiplier
        position_size_multiplier = score
        
        return score, position_size_multiplier
    
    def _build_reason(
        self,
        spread_ok: bool,
        spread_value: float,
        liquidity_score: float,
        slippage_estimate: float,
        execution_score: float,
        can_execute: bool
    ) -> str:
        """Построить пояснение."""
        reasons = []
        
        if not spread_ok:
            reasons.append(f"spread={spread_value:.2%}>0.3%")
        
        if liquidity_score < 0.4:
            reasons.append(f"liquidity={liquidity_score:.2f}<0.4")
        
        if slippage_estimate > self.MAX_SLIPPAGE:
            reasons.append(f"slippage={slippage_estimate:.2%}>0.2%")
        
        if not reasons:
            return f"OK (score={execution_score:.2f})"
        
        return " | ".join(reasons)