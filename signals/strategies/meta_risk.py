"""
Meta Risk Controller v7.7 — Контроль выживаемости системы.

Архитектура:
    FEATURES → Probability → Regime → Integrity → Execution → Portfolio
                    ↓
            META RISK CONTROLLER (NEW 🔥)
                    ↓
            EQUITY CURVE TRACKER
            DRAWDOWN STATE
            DAILY LOSS LIMIT
            SYSTEM HEAT
                    ↓
            FINAL APPROVAL
                    ↓
            LEVERAGE CONTROL

Философия:
- Signal decides trade, но Risk layer решает, имеет ли система вообще право торговать
- Защита от "отыграюсь"
- Контроль просадки
- Системный heat management
"""
import logging
from typing import Dict, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os

log = logging.getLogger("MetaRisk")


@dataclass
class MetaRiskState:
    """Состояние мета-риска."""
    drawdown: float
    risk_state: str  # SAFE, CAUTION, RISK, CRITICAL
    leverage_multiplier: float
    daily_pnl: float
    daily_limit_used: float
    system_heat: float
    can_trade: bool
    reason: str


class MetaRiskController:
    """
    Meta Risk Controller v7.7.
    
    Контролирует "выживаемость системы":
    1. Equity Curve Tracker — отслеживание просадки
    2. Risk State Engine — состояние риска (SAFE/CAUTION/RISK/CRITICAL)
    3. Leverage Control — управление плечом по состоянию
    4. Daily Loss Kill Switch — защита от "отыграюсь"
    5. System Heat Model — системный перегрев
    
    Результат:
    - can_trade = True/False
    - leverage_multiplier = 0.0-1.0
    """
    
    # ==================== RISK STATES ====================
    
    RISK_STATES = {
        "SAFE": {
            "threshold": 0.03,  # < 3% drawdown
            "leverage": 1.0,
            "description": "Нормальная торговля"
        },
        "CAUTION": {
            "threshold": 0.07,  # < 7% drawdown
            "leverage": 0.7,
            "description": "Сниженный риск"
        },
        "RISK": {
            "threshold": 0.12,  # < 12% drawdown
            "leverage": 0.4,
            "description": "Минимальный риск"
        },
        "CRITICAL": {
            "threshold": 1.0,  # >= 12% drawdown
            "leverage": 0.0,  # Trading freeze
            "description": "Торговля остановлена"
        }
    }
    
    # ==================== LIMITS ====================
    
    DAILY_LOSS_LIMIT = 0.05  # 5% от equity
    MAX_SYSTEM_HEAT = 2.0  # Максимальный heat
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/meta_risk"):
        """Инициализация Meta Risk Controller."""
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # Equity curve
        self.equity_curve: List[float] = []
        self.peak_equity: float = 0.0
        
        # Daily PnL tracking
        self.daily_pnl: Dict[str, float] = {}
        self.last_reset: datetime = datetime.now()
        
        # System heat
        self.open_positions: Dict[str, Dict] = {}
        
        # Load state
        self._load_state()
    
    # ==================== MAIN API ====================
    
    def check_meta_risk(
        self,
        current_equity: float,
        open_positions: Optional[Dict] = None
    ) -> MetaRiskState:
        """
        Проверить мета-риск перед торговлей.
        
        Args:
            current_equity: Текущий equity
            open_positions: Открытые позиции
            
        Returns:
            MetaRiskState с решением о торговле
        """
        # ========== UPDATE STATE ==========
        self._update_equity_curve(current_equity)
        
        if open_positions:
            self.open_positions = open_positions
        
        # ========== CALCULATE DRAWDOWN ==========
        drawdown = self._calculate_drawdown(current_equity)
        
        # ========== DETERMINE RISK STATE ==========
        risk_state = self._determine_risk_state(drawdown)
        state_config = self.RISK_STATES[risk_state]
        leverage_multiplier = state_config["leverage"]
        
        # ========== CHECK DAILY LOSS LIMIT ==========
        daily_pnl = self._get_daily_pnl()
        daily_limit = current_equity * self.DAILY_LOSS_LIMIT
        daily_limit_used = abs(daily_pnl) / daily_limit if daily_limit > 0 else 0.0
        
        # ========== CHECK SYSTEM HEAT ==========
        system_heat = self._calculate_system_heat(current_equity)
        
        # ========== MAKE DECISION ==========
        can_trade, reason = self._make_decision(
            risk_state=risk_state,
            daily_pnl=daily_pnl,
            daily_limit=daily_limit,
            system_heat=system_heat,
            drawdown=drawdown
        )
        
        # ========== LOG ==========
        log.debug(
            f"Meta Risk: "
            f"Drawdown={drawdown:.2%} | "
            f"State={risk_state} ({state_config['description']}) | "
            f"Leverage={leverage_multiplier:.0%} | "
            f"DailyPnL={daily_pnl:+.2%} | "
            f"Heat={system_heat:.2f} | "
            f"{'APPROVED' if can_trade else 'BLOCKED'}"
        )
        
        # ========== SAVE STATE ==========
        self._save_state()
        
        return MetaRiskState(
            drawdown=drawdown,
            risk_state=risk_state,
            leverage_multiplier=leverage_multiplier,
            daily_pnl=daily_pnl,
            daily_limit_used=daily_limit_used,
            system_heat=system_heat,
            can_trade=can_trade,
            reason=reason
        )
    
    def record_trade_result(self, pnl: float):
        """Записать результат сделки."""
        today = datetime.now().date().isoformat()
        
        if today not in self.daily_pnl:
            self.daily_pnl[today] = 0.0
        
        self.daily_pnl[today] += pnl
        
        # Сохраняем
        self._save_state()
    
    # ==================== PRIVATE METHODS ====================
    
    def _update_equity_curve(self, equity: float):
        """Обновить equity curve."""
        self.equity_curve.append(equity)
        
        # Обновляем пик
        if equity > self.peak_equity:
            self.peak_equity = equity
        
        # Храним только последние 100 точек
        if len(self.equity_curve) > 100:
            self.equity_curve = self.equity_curve[-100:]
    
    def _calculate_drawdown(self, current_equity: float) -> float:
        """Вычислить текущую просадку."""
        if self.peak_equity == 0:
            return 0.0
        
        return (self.peak_equity - current_equity) / self.peak_equity
    
    def _determine_risk_state(self, drawdown: float) -> str:
        """Определить состояние риска."""
        for state_name, config in self.RISK_STATES.items():
            if drawdown < config["threshold"]:
                return state_name
        
        return "CRITICAL"
    
    def _get_daily_pnl(self) -> float:
        """Получить дневной PnL."""
        today = datetime.now().date().isoformat()
        
        # Проверяем, нужно ли сбросить старые записи
        if self.last_reset.date() < datetime.now().date():
            # Новый день — сброс
            old_dates = [
                date for date in self.daily_pnl.keys()
                if date < today
            ]
            for date in old_dates:
                del self.daily_pnl[date]
            
            self.last_reset = datetime.now()
        
        return self.daily_pnl.get(today, 0.0)
    
    def _calculate_system_heat(self, equity: float) -> float:
        """Вычислить системный heat."""
        # Общая экспозиция
        total_exposure = sum(
            abs(pos.get("size", 0))
            for pos in self.open_positions.values()
        )
        
        # Base heat
        heat = total_exposure / equity if equity > 0 else 0.0
        
        # Correlation penalty
        correlation_penalty = self._calculate_correlation_penalty()
        
        return heat + correlation_penalty
    
    def _calculate_correlation_penalty(self) -> float:
        """Вычислить штраф за корреляцию."""
        # Группы коррелированных активов
        CORRELATION_GROUPS = {
            "BTC_CRYPTOS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"],
            "LAYER1": ["ADAUSDT", "AVAXUSDT", "MATICUSDT"],
            "MEME": ["DOGEUSDT", "SHIBUSDT", "PEPEUSDT"],
        }
        
        penalty = 0.0
        
        for group, symbols in CORRELATION_GROUPS.items():
            group_positions = sum(
                1 for s in symbols if s in self.open_positions
            )
            
            # Каждая коррелированная позиция добавляет риск
            if group_positions > 1:
                penalty += group_positions * 0.1
        
        return penalty
    
    def _make_decision(
        self,
        risk_state: str,
        daily_pnl: float,
        daily_limit: float,
        system_heat: float,
        drawdown: float
    ) -> tuple:
        """
        Принять решение о торговле.
        
        Returns:
            (can_trade, reason)
        """
        reasons = []
        
        # ========== CHECK 1: CRITICAL STATE ==========
        if risk_state == "CRITICAL":
            reasons.append(f"risk_state=CRITICAL (drawdown={drawdown:.1%})")
            return False, " | ".join(reasons)
        
        # ========== CHECK 2: DAILY LOSS LIMIT ==========
        if daily_pnl < -abs(daily_limit):
            reasons.append(f"daily_loss={daily_pnl:.2%}<limit")
            return False, " | ".join(reasons)
        
        # ========== CHECK 3: SYSTEM HEAT ==========
        if system_heat > self.MAX_SYSTEM_HEAT:
            reasons.append(f"system_heat={system_heat:.2f}>{self.MAX_SYSTEM_HEAT}")
            # НЕ блокируем полностью, но снижаем leverage
        
        # ========== ALLOWED ==========
        can_trade = risk_state in ["SAFE", "CAUTION", "RISK"]
        
        reason = "OK" if not reasons else " | ".join(reasons)
        
        return can_trade, reason
    
    def _save_state(self):
        """Сохранить состояние."""
        state_file = os.path.join(self.data_dir, "meta_risk_state.json")
        
        state = {
            "peak_equity": self.peak_equity,
            "equity_curve": self.equity_curve[-100:],  # Последние 100
            "daily_pnl": self.daily_pnl,
            "last_reset": self.last_reset.isoformat(),
        }
        
        try:
            with open(state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            log.warning(f"Failed to save state: {e}")
    
    def _load_state(self):
        """Загрузить состояние."""
        state_file = os.path.join(self.data_dir, "meta_risk_state.json")
        
        try:
            if os.path.exists(state_file):
                with open(state_file, "r") as f:
                    state = json.load(f)
                
                self.peak_equity = state.get("peak_equity", 0.0)
                self.equity_curve = state.get("equity_curve", [])
                self.daily_pnl = state.get("daily_pnl", {})
                
                if "last_reset" in state:
                    self.last_reset = datetime.fromisoformat(state["last_reset"])
                
                log.info(f"Loaded meta risk state: peak={self.peak_equity:.2f}, "
                        f"equity_points={len(self.equity_curve)}")
        except Exception as e:
            log.warning(f"Failed to load state: {e}")


# Импорт для typing
from typing import List