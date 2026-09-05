"""
Integration & Audit Layer v8.0 — Полная прослеживаемость решений.

Архитектура:
    INPUT DATA
         ↓
    FEATURE ENGINE
         ↓
    DECISION STACK (v7.x)
         ↓
    TRADE RESULT
         ↓
    AUDIT LOGGER (NEW 🔥)
         ↓
    DECISION TRACE BUILDER
         ↓
    REPLAY ENGINE
         ↓
    SYSTEM INSIGHTS DASHBOARD

Философия:
- Система НЕ "верит сама себе"
- Каждое решение объяснимо
- Воспроизводимость решений
- Институциональная прозрачность
"""
import logging
from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
import json
import os

log = logging.getLogger("AuditLayer")


@dataclass
class DecisionTrace:
    """
    Полный след решения.
    
    EDGE SPACE (решает "СТОИТ ЛИ ВХОДИТЬ"):
    - base_probability (чистый edge из scoring)
    - regime_bias (мягкий shift +/- 3-6%)
    - final_probability (edge + regime bias)
    
    RISK SPACE (решает "СКОЛЬКО МОЖНО ВЗЯТЬ"):
    - integrity_multiplier (качество данных → risk)
    - execution_score (качество исполнения → risk)
    - portfolio_risk_multiplier (портфель → risk)
    - meta_risk_multiplier (система → risk)
    - stability_multiplier (поведение → risk)
    
    FINAL:
    - position_size = kelly(edge, equity) * risk_multiplier
    """
    # Metadata
    symbol: str
    direction: str
    timestamp: str
    
    # EDGE SPACE (probability layer)
    base_probability: float  # Чистый edge из scoring
    regime: str  # Рыночный режим
    regime_bias: float  # Мягкий shift +/- 3-6%
    final_probability: float  # base_probability + regime_bias
    
    # RISK SPACE (risk layer)
    integrity_multiplier: float  # Качество данных → risk
    execution_score: float  # Качество исполнения → risk
    portfolio_risk_multiplier: float  # Портфель → risk
    meta_risk_multiplier: float  # Система → risk
    stability_multiplier: float  # Поведение → risk
    total_risk_multiplier: float  # Итоговый risk multiplier
    
    # POSITION SIZING
    base_size_usdt: float  # Kelly position
    final_size_usdt: float  # base_size * risk_multiplier
    position_size_fraction: float  # % от equity
    
    # DECISION
    can_trade: bool
    reject_reasons: list
    approve_reasons: list
    
    # FEATURES (для replay)
    features: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict:
        """Сериализация."""
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "timestamp": self.timestamp,
            "edge_space": {
                "base_probability": self.base_probability,
                "regime": self.regime,
                "regime_bias": self.regime_bias,
                "final_probability": self.final_probability,
            },
            "risk_space": {
                "integrity_multiplier": self.integrity_multiplier,
                "execution_score": self.execution_score,
                "portfolio_risk_multiplier": self.portfolio_risk_multiplier,
                "meta_risk_multiplier": self.meta_risk_multiplier,
                "stability_multiplier": self.stability_multiplier,
                "total_risk_multiplier": self.total_risk_multiplier,
            },
            "position_sizing": {
                "base_size_usdt": self.base_size_usdt,
                "final_size_usdt": self.final_size_usdt,
                "position_size_fraction": self.position_size_fraction,
            },
            "decision": {
                "can_trade": self.can_trade,
                "reject_reasons": self.reject_reasons,
                "approve_reasons": self.approve_reasons,
            },
            "features": self.features,
        }


class AuditLogger:
    """Логирование всех решений."""
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/audit"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        self.log: list = []
        self.max_log_size = 1000
    
    def record(self, trace: DecisionTrace):
        """Записать trace в лог."""
        self.log.append(trace.to_dict())
        
        # Храним только последние N записей в памяти
        if len(self.log) > self.max_log_size:
            self.log = self.log[-self.max_log_size:]
        
        # Сохраняем в файл
        self._save_trace(trace)
    
    def _save_trace(self, trace: DecisionTrace):
        """Сохранить trace в файл."""
        filename = os.path.join(
            self.data_dir,
            f"traces_{datetime.now().strftime('%Y%m')}.jsonl"
        )
        
        try:
            with open(filename, "a") as f:
                f.write(json.dumps(trace.to_dict()) + "\n")
        except Exception as e:
            log.warning(f"Failed to save trace: {e}")
    
    def get_recent_traces(self, count: int = 10) -> list:
        """Получить последние trace."""
        return self.log[-count:]


class ReplayEngine:
    """
    Воспроизведение решений.
    
    Позволяет пересчитать любое решение
    и проверить консистентность.
    """
    
    def replay_decision(self, trace: DecisionTrace) -> Dict:
        """
        Пересчитать решение по trace.
        
        Returns:
            reconstructed decision
        """
        # ========== RECONSTRUCT EDGE ==========
        reconstructed_probability = (
            trace.base_probability + trace.regime_bias
        )
        
        # ========== RECONSTRUCT RISK ==========
        reconstructed_risk = (
            trace.integrity_multiplier *
            trace.execution_score *
            trace.portfolio_risk_multiplier *
            trace.meta_risk_multiplier *
            trace.stability_multiplier
        )
        
        # ========== RECONSTRUCT SIZE ==========
        reconstructed_size = trace.base_size_usdt * reconstructed_risk
        
        # ========== COMPARE ==========
        probability_delta = abs(trace.final_probability - reconstructed_probability)
        size_delta = abs(trace.final_size_usdt - reconstructed_size)
        
        return {
            "original_probability": trace.final_probability,
            "reconstructed_probability": reconstructed_probability,
            "probability_delta": probability_delta,
            "original_size": trace.final_size_usdt,
            "reconstructed_size": reconstructed_size,
            "size_delta": size_delta,
            "consistent": probability_delta < 0.01 and size_delta < 1.0,
        }
    
    def consistency_check(self, trace: DecisionTrace) -> str:
        """
        Проверить консистентность решения.
        
        Returns:
            "CONSISTENT" | "INCONSISTENT"
        """
        replay = self.replay_decision(trace)
        
        if not replay["consistent"]:
            return "INCONSISTENT"
        
        return "CONSISTENT"


class ExplanationEngine:
    """
    Объяснение решений на русском языке.
    
    Почему было принято решение?
    Почему размер позиции такой?
    Почему сделка отклонена?
    """
    
    def explain(self, trace: DecisionTrace) -> str:
        """
        Объяснить решение.
        
        Returns:
            Текстовое объяснение на русском
        """
        lines = []
        
        # ========== EDGE SPACE ==========
        lines.append("=== EDGE SPACE (Стоит ли входить?) ===")
        lines.append(f"Базовая вероятность: {trace.base_probability:.2%}")
        lines.append(f"Режим рынка: {trace.regime}")
        lines.append(f"Смещение режима: {trace.regime_bias:+.2%}")
        lines.append(f"Итоговая вероятность: {trace.final_probability:.2%}")
        
        if trace.final_probability >= 0.55:
            lines.append("✅ Edge достаточный для входа")
        else:
            lines.append("❌ Edge недостаточный")
        
        # ========== RISK SPACE ==========
        lines.append("\n=== RISK SPACE (Сколько можно взять?) ===")
        lines.append(f"Integrity (качество данных): {trace.integrity_multiplier:.0%}")
        lines.append(f"Execution (качество исполнения): {trace.execution_score:.0%}")
        lines.append(f"Portfolio Risk: {trace.portfolio_risk_multiplier:.0%}")
        lines.append(f"Meta Risk: {trace.meta_risk_multiplier:.0%}")
        lines.append(f"Stability: {trace.stability_multiplier:.0%}")
        lines.append(f"Итоговый Risk Multiplier: {trace.total_risk_multiplier:.0%}")
        
        # ========== POSITION SIZING ==========
        lines.append("\n=== POSITION SIZING ===")
        lines.append(f"Базовый размер (Kelly): ${trace.base_size_usdt:.2f}")
        lines.append(f"Финальный размер: ${trace.final_size_usdt:.2f}")
        lines.append(f"Доля от equity: {trace.position_size_fraction:.0%}")
        
        # ========== DECISION ==========
        lines.append("\n=== РЕШЕНИЕ ===")
        if trace.can_trade:
            lines.append("✅ СДЕЛКА ОДОБРЕНА")
            for reason in trace.approve_reasons:
                lines.append(f"  • {reason}")
        else:
            lines.append("❌ СДЕЛКА ОТКЛОНЕНА")
            for reason in trace.reject_reasons:
                lines.append(f"  • {reason}")
        
        return "\n".join(lines)
    
    def explain_rejection(self, trace: DecisionTrace) -> str:
        """Объяснить почему сделка отклонена."""
        if trace.can_trade:
            return "Сделка была одобрена"
        
        reasons = []
        
        # EDGE SPACE
        if trace.final_probability < 0.55:
            reasons.append(
                f"Вероятность {trace.final_probability:.0%} < 55% (недостаточный edge)"
            )
        
        # RISK SPACE
        if trace.total_risk_multiplier < 0.3:
            reasons.append(
                f"Risk multiplier {trace.total_risk_multiplier:.0%} < 30% (слишком рискованно)"
            )
        
        if trace.integrity_multiplier < 0.5:
            reasons.append(
                f"Качество данных {trace.integrity_multiplier:.0%} < 50%"
            )
        
        if trace.execution_score < 0.4:
            reasons.append(
                f"Качество исполнения {trace.execution_score:.0%} < 40%"
            )
        
        if trace.meta_risk_multiplier < 0.4:
            reasons.append(
                f"Системный риск {trace.meta_risk_multiplier:.0%} (просадка/лимиты)"
            )
        
        if trace.stability_multiplier < 0.4:
            reasons.append(
                f"Стабильность системы {trace.stability_multiplier:.0%} (overtrading/дрейф)"
            )
        
        # Добавляем explicit reject reasons
        for reason in trace.reject_reasons:
            if reason not in reasons:
                reasons.append(reason)
        
        return "\n".join(reasons) if reasons else "Причина неизвестна"


class IntegrationAuditLayer:
    """
    Integration & Audit Layer v8.0.
    
    Единая точка для:
    1. Построения Decision Trace
    2. Логирования всех решений
    3. Replay Engine (воспроизведение)
    4. Explanation Engine (объяснения)
    5. System State Snapshot
    
    КРИТИЧЕСКИ ВАЖНО:
    - Разделение EDGE SPACE и RISK SPACE
    - Полная прослеживаемость
    - Институциональная прозрачность
    """
    
    def __init__(self, data_dir: str = "/opt/scalper_v6/data/audit"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)
        
        # Компоненты
        self.audit_logger = AuditLogger(data_dir)
        self.replay_engine = ReplayEngine()
        self.explanation_engine = ExplanationEngine()
        
        # Счётчики
        self.traces_created = 0
        self.trades_executed = 0
        self.trades_rejected = 0
    
    # ==================== MAIN API ====================
    
    def build_trace(
        self,
        # EDGE SPACE
        symbol: str,
        direction: str,
        base_probability: float,
        regime: str,
        regime_bias: float,
        # RISK SPACE
        integrity_multiplier: float,
        execution_score: float,
        portfolio_risk_multiplier: float,
        meta_risk_multiplier: float,
        stability_multiplier: float,
        # POSITION SIZING
        base_size_usdt: float,
        final_size_usdt: float,
        position_size_fraction: float,
        # DECISION
        can_trade: bool,
        reject_reasons: list,
        approve_reasons: list,
        # FEATURES
        features: Optional[Dict] = None
    ) -> DecisionTrace:
        """
        Построить Decision Trace.
        
        Args:
            symbol: Символ
            direction: BUY/SELL
            base_probability: Чистый edge из scoring
            regime: Рыночный режим
            regime_bias: Мягкий shift +/- 3-6%
            integrity_multiplier: Качество данных → risk
            execution_score: Качество исполнения → risk
            portfolio_risk_multiplier: Портфель → risk
            meta_risk_multiplier: Система → risk
            stability_multiplier: Поведение → risk
            base_size_usdt: Kelly position
            final_size_usdt: base_size * risk_multiplier
            position_size_fraction: % от equity
            can_trade: Одобрено/отклонено
            reject_reasons: Причины отклонения
            approve_reasons: Причины одобрения
            features: Признаки (для replay)
            
        Returns:
            DecisionTrace
        """
        # ========== CALCULATE DERIVED ==========
        final_probability = base_probability + regime_bias
        total_risk_multiplier = (
            integrity_multiplier *
            execution_score *
            portfolio_risk_multiplier *
            meta_risk_multiplier *
            stability_multiplier
        )
        
        # ========== BUILD TRACE ==========
        trace = DecisionTrace(
            symbol=symbol,
            direction=direction,
            timestamp=datetime.now().isoformat(),
            # EDGE SPACE
            base_probability=base_probability,
            regime=regime,
            regime_bias=regime_bias,
            final_probability=final_probability,
            # RISK SPACE
            integrity_multiplier=integrity_multiplier,
            execution_score=execution_score,
            portfolio_risk_multiplier=portfolio_risk_multiplier,
            meta_risk_multiplier=meta_risk_multiplier,
            stability_multiplier=stability_multiplier,
            total_risk_multiplier=total_risk_multiplier,
            # POSITION SIZING
            base_size_usdt=base_size_usdt,
            final_size_usdt=final_size_usdt,
            position_size_fraction=position_size_fraction,
            # DECISION
            can_trade=can_trade,
            reject_reasons=reject_reasons,
            approve_reasons=approve_reasons,
            # FEATURES
            features=features or {}
        )
        
        # ========== RECORD ==========
        self.audit_logger.record(trace)
        self.traces_created += 1
        
        if can_trade:
            self.trades_executed += 1
        else:
            self.trades_rejected += 1
        
        # ========== LOG ==========
        log.info(
            f"{'✅' if can_trade else '❌'} {symbol} {direction} | "
            f"Edge={final_probability:.2%} | "
            f"Risk={total_risk_multiplier:.0%} | "
            f"Size=${final_size_usdt:.2f} | "
            f"{', '.join(reject_reasons) if reject_reasons else 'APPROVED'}"
        )
        
        return trace
    
    def replay_trace(self, trace: DecisionTrace) -> Dict:
        """Пересчитать решение."""
        return self.replay_engine.replay_decision(trace)
    
    def explain_trace(self, trace: DecisionTrace) -> str:
        """Объяснить решение."""
        return self.explanation_engine.explain(trace)
    
    def explain_rejection(self, trace: DecisionTrace) -> str:
        """Объяснить отклонение."""
        return self.explanation_engine.explain_rejection(trace)
    
    def get_stats(self) -> Dict:
        """Получить статистику."""
        return {
            "traces_created": self.traces_created,
            "trades_executed": self.trades_executed,
            "trades_rejected": self.trades_rejected,
            "approval_rate": (
                self.trades_executed / self.traces_created
                if self.traces_created > 0 else 0
            ),
        }
    
    def get_recent_traces(self, count: int = 10) -> list:
        """Получить последние trace."""
        return self.audit_logger.get_recent_traces(count)