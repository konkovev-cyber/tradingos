"""
Portfolio Risk Layer v7.5 — Управление портфельным риском.

Архитектура:
    FEATURES → Probability → Regime → Integrity → Execution
                ↓
         PORTFOLIO RISK CHECK (NEW 🔥)
                ↓
         CORRELATION EXPOSURE
         PORTFOLIO HEAT
         REGIME EXPOSURE LIMIT
         DAILY LOSS LIMIT
                ↓
         FINAL APPROVAL
                ↓
         POSITION SIZING

Философия:
- Нет изолированных сделок — каждая учитывает весь портфель
- Контроль корреляции (BTC ↑ = ETH + SOL считаются)
- Heat management — защита от перегруза плеча
- Regime-aware risk caps
"""
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta

log = logging.getLogger("PortfolioRisk")


@dataclass
class PortfolioRiskResult:
    """Результат проверки портфельного риска."""
    total_exposure: float
    correlation_exposure: float
    portfolio_heat: float
    regime_limit: float
    daily_pnl: float
    daily_limit: float
    risk_state: str
    can_trade: bool
    position_size_multiplier: float
    reason: str


# ==================== CORRELATION MAP ====================
# Группы коррелированных активов

CORRELATION_GROUPS = {
    "BTC_CRYPTOS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
    "LAYER1": ["ADAUSDT", "AVAXUSDT", "MATICUSDT", "NEARUSDT"],
    "MEME": ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT"],
    "GAMING": ["SANDUSDT", "MANAUSDT", "AXSUSDT", "ESPORTSUSDT"],
    "DEFI": ["AAVEUSDT", "UNIUSDT", "LINKUSDT"],
}


class PortfolioRiskEngine:
    """
    Portfolio Risk Layer v7.5.
    
    Контролирует портфельный риск:
    1. Correlation Exposure — коррелированные позиции
    2. Portfolio Heat — общий уровень риска
    3. Regime Exposure Limit — лимиты по режимам рынка
    4. Daily Loss Limit — защита от "отыграюсь"
    
    Результат:
    - can_trade = True/False
    - position_size *= risk_multiplier
    """
    
    # ==================== LIMITS ====================
    
    # Максимальная доля в одном активе
    MAX_SINGLE_POSITION = 0.30  # 30%
    
    # Максимальная корреляционная экспозиция
    MAX_CORRELATION_EXPOSURE = 0.50  # 50%
    
    # Максимальный портфельный heat
    MAX_PORTFOLIO_HEAT = 1.5
    
    # Дневной лимит убытка (от equity)
    DAILY_LOSS_LIMIT = 0.05  # 5%
    
    # Regime-specific limits
    REGIME_LIMITS = {
        "trending": 0.35,      # Тренд — можно больше
        "ranging": 0.20,       # Боковик — меньше
        "high_volatility": 0.15,  # Высокая волатильность — минимум
        "low_liquidity": 0.10,    # Низкая ликвидность — ещё меньше
        "normal": 0.25,        # Нормальный режим
    }
    
    # Risk state thresholds
    RISK_STATES = {
        "SAFE": 0.03,      # < 3% drawdown
        "CAUTION": 0.07,   # < 7% drawdown
        "RISK": 0.12,      # < 12% drawdown
        "CRITICAL": 1.0,   # >= 12% drawdown
    }
    
    # Risk state multipliers
    RISK_MULTIPLIERS = {
        "SAFE": 1.0,
        "CAUTION": 0.7,
        "RISK": 0.4,
        "CRITICAL": 0.0,  # Trading freeze
    }
    
    def __init__(self):
        """Инициализация Portfolio Risk Engine."""
        self.positions = {}
        self.equity_curve = []
        self.daily_pnl = {}
        self.last_reset = datetime.now()
    
    # ==================== MAIN API ====================
    
    def check_portfolio_risk(
        self,
        symbol: str,
        position_size_usdt: float,
        equity: float,
        regime: str = "normal",
        current_positions: Optional[Dict] = None
    ) -> PortfolioRiskResult:
        """
        Проверить портфельный риск перед входом.
        
        Args:
            symbol: Торговый символ
            position_size_usdt: Размер планируемой позиции
            equity: Текущий equity
            regime: Рыночный режим
            current_positions: Текущие позиции {symbol: {...}}
            
        Returns:
            PortfolioRiskResult с решением о входе
        """
        # ========== STEP 1: UPDATE POSITIONS ==========
        if current_positions:
            self.positions = current_positions
        
        # ========== STEP 2: CORRELATION EXPOSURE ==========
        correlation_exposure = self._calc_correlation_exposure(symbol)
        
        # ========== STEP 3: PORTFOLIO HEAT ==========
        portfolio_heat = self._calc_portfolio_heat(equity)
        
        # ========== STEP 4: REGIME LIMIT ==========
        regime_limit = self.REGIME_LIMITS.get(regime, 0.25)
        
        # ========== STEP 5: DAILY LOSS LIMIT ==========
        daily_pnl = self._get_daily_pnl()
        daily_limit = equity * self.DAILY_LOSS_LIMIT
        
        # ========== STEP 6: RISK STATE ==========
        drawdown = self._calc_drawdown(equity)
        risk_state = self._determine_risk_state(drawdown)
        risk_multiplier = self.RISK_MULTIPLIERS.get(risk_state, 0.4)
        
        # ========== STEP 7: CALCULATE TOTAL EXPOSURE ==========
        total_exposure = self._calc_total_exposure(position_size_usdt, equity)
        
        # ========== STEP 8: DECISION ==========
        can_trade, position_size_multiplier, reason = self._make_decision(
            total_exposure=total_exposure,
            correlation_exposure=correlation_exposure,
            portfolio_heat=portfolio_heat,
            regime_limit=regime_limit,
            daily_pnl=daily_pnl,
            daily_limit=daily_limit,
            risk_state=risk_state,
            regime=regime,
            symbol=symbol
        )
        
        # Apply risk multiplier
        position_size_multiplier *= risk_multiplier
        
        # ========== LOG ==========
        log.debug(
            f"{symbol}: Portfolio Risk — "
            f"Exposure={total_exposure:.1%} | "
            f"Correlation={correlation_exposure:.1%} | "
            f"Heat={portfolio_heat:.2f} | "
            f"Regime={regime} (limit={regime_limit:.1%}) | "
            f"State={risk_state} | "
            f"Size={position_size_multiplier:.0%} | "
            f"{'APPROVED' if can_trade else 'BLOCKED'}"
        )
        
        return PortfolioRiskResult(
            total_exposure=total_exposure,
            correlation_exposure=correlation_exposure,
            portfolio_heat=portfolio_heat,
            regime_limit=regime_limit,
            daily_pnl=daily_pnl,
            daily_limit=daily_limit,
            risk_state=risk_state,
            can_trade=can_trade,
            position_size_multiplier=position_size_multiplier,
            reason=reason
        )
    
    def update_position(self, symbol: str, position: Dict):
        """Обновить позицию в трекере."""
        self.positions[symbol] = position
    
    def close_position(self, symbol: str):
        """Закрыть позицию."""
        if symbol in self.positions:
            del self.positions[symbol]
    
    def update_pnl(self, pnl: float):
        """Обновить дневной PnL."""
        today = datetime.now().date()
        
        if today != self.last_reset.date():
            # Новый день — сброс
            self.daily_pnl = {today: 0.0}
            self.last_reset = datetime.now()
        
        if today not in self.daily_pnl:
            self.daily_pnl[today] = 0.0
        
        self.daily_pnl[today] += pnl
    
    def update_equity(self, equity: float):
        """Обновить equity curve."""
        self.equity_curve.append(equity)
        
        # Храним только последние 100 точек
        if len(self.equity_curve) > 100:
            self.equity_curve = self.equity_curve[-100:]
    
    # ==================== PRIVATE METHODS ====================
    
    def _calc_correlation_exposure(self, symbol: str) -> float:
        """Вычислить корреляционную экспозицию."""
        # Найти группу корреляции для символа
        correlation_group = None
        for group, symbols in CORRELATION_GROUPS.items():
            if symbol in symbols:
                correlation_group = group
                break
        
        if not correlation_group:
            return 0.0
        
        # Сумма позиций в корреляционной группе
        correlated_symbols = CORRELATION_GROUPS[correlation_group]
        correlated_exposure = 0.0
        
        for pos_symbol, pos in self.positions.items():
            if pos_symbol in correlated_symbols:
                correlated_exposure += abs(pos.get("size", 0))
        
        return correlated_exposure
    
    def _calc_portfolio_heat(self, equity: float) -> float:
        """Вычислить портфельный heat."""
        total_exposure = sum(
            abs(pos.get("size", 0)) for pos in self.positions.values()
        )
        
        if equity == 0:
            return 0.0
        
        # Heat = exposure / equity
        heat = total_exposure / equity
        
        # Добавляем корреляционный риск
        correlation_penalty = 0.0
        for group, symbols in CORRELATION_GROUPS.items():
            group_exposure = sum(
                abs(self.positions.get(s, {}).get("size", 0))
                for s in symbols if s in self.positions
            )
            if group_exposure > 0:
                # Чем больше коррелированных позиций, тем выше риск
                correlation_penalty += group_exposure * 0.1
        
        return heat + correlation_penalty / equity
    
    def _calc_total_exposure(self, position_size_usdt: float, equity: float) -> float:
        """Вычислить общую экспозицию."""
        current_exposure = sum(
            abs(pos.get("size", 0)) for pos in self.positions.values()
        )
        
        total = current_exposure + position_size_usdt
        
        return total / equity if equity > 0 else 0.0
    
    def _get_daily_pnl(self) -> float:
        """Получить дневной PnL."""
        today = datetime.now().date()
        return self.daily_pnl.get(today, 0.0)
    
    def _calc_drawdown(self, equity: float) -> float:
        """Вычислить текущую просадку."""
        if not self.equity_curve:
            return 0.0
        
        peak = max(self.equity_curve)
        
        if peak == 0:
            return 0.0
        
        return (peak - equity) / peak
    
    def _determine_risk_state(self, drawdown: float) -> str:
        """Определить состояние риска."""
        for state, threshold in self.RISK_STATES.items():
            if drawdown < threshold:
                return state
        
        return "CRITICAL"
    
    def _make_decision(
        self,
        total_exposure: float,
        correlation_exposure: float,
        portfolio_heat: float,
        regime_limit: float,
        daily_pnl: float,
        daily_limit: float,
        risk_state: str,
        regime: str,
        symbol: str
    ) -> tuple:
        """
        Принять решение о входе.
        
        Returns:
            (can_trade, position_size_multiplier, reason)
        """
        reasons = []
        position_size_multiplier = 1.0
        
        # ========== CHECK 1: TOTAL EXPOSURE ==========
        if total_exposure > regime_limit:
            reasons.append(f"exposure={total_exposure:.1%}>{regime_limit:.1%}")
            position_size_multiplier *= 0.5  # Снижаем размер вдвое
        
        # ========== CHECK 2: CORRELATION ==========
        if correlation_exposure > self.MAX_CORRELATION_EXPOSURE:
            reasons.append(f"correlation={correlation_exposure:.1%}>{self.MAX_CORRELATION_EXPOSURE:.0%}")
            position_size_multiplier *= 0.6
        
        # ========== CHECK 3: PORTFOLIO HEAT ==========
        if portfolio_heat > self.MAX_PORTFOLIO_HEAT:
            reasons.append(f"heat={portfolio_heat:.2f}>{self.MAX_PORTFOLIO_HEAT}")
            position_size_multiplier *= 0.4
        
        # ========== CHECK 4: DAILY LOSS LIMIT ==========
        if daily_pnl < -abs(daily_limit):
            reasons.append(f"daily_loss={daily_pnl:.2f}<{-abs(daily_limit):.2f}")
            return False, 0.0, " | ".join(reasons)
        
        # ========== CHECK 5: RISK STATE ==========
        if risk_state == "CRITICAL":
            reasons.append(f"risk_state={risk_state}")
            return False, 0.0, " | ".join(reasons)
        
        # ========== FINAL DECISION ==========
        can_trade = len(reasons) == 0 or position_size_multiplier > 0.3
        
        reason = " | ".join(reasons) if reasons else "OK"
        
        return can_trade, position_size_multiplier, reason