"""
Signal Scoring Engine — Оценка качества сигнала.
Вместо boolean BUY/SELL → score 0-100.
"""
import logging
from collections import deque
import time

log = logging.getLogger("SignalScorer")


class SignalScorer:
    """
    Оценка силы сигнала по нескольким факторам.
    Score > 70 = сильный сигнал, можно торговать.
    """
    
    def __init__(self):
        # История сигналов для learning
        self.signal_history = {}  # symbol -> deque of {signal, outcome}
        
        # Веса факторов (можно тюнить)
        self.weights = {
            'rsi': 0.25,
            'ema_cross': 0.20,
            'volume': 0.15,
            'momentum': 0.15,
            'volatility': 0.10,
            'trend': 0.15,
        }
    
    def score_signal(self, symbol: str, features: dict, regime: dict) -> dict:
        """
        Вычислить score для сигнала.
        Возвращает: {score: 0-100, direction: 'long'|'short'|'none', reasons: []}
        """
        score = 0
        direction = 'none'
        reasons = []
        
        # 1. RSI Score (0-25)
        rsi = features.get('rsi', 50)
        rsi_score = 0
        if rsi < 30:
            rsi_score = 25  # Oversold = strong buy
            direction = 'long'
            reasons.append(f"RSI oversold ({rsi:.1f})")
        elif rsi > 70:
            rsi_score = 25  # Overbought = strong sell
            direction = 'short'
            reasons.append(f"RSI overbought ({rsi:.1f})")
        elif 40 <= rsi <= 60:
            rsi_score = 5  # Neutral zone
            reasons.append(f"RSI neutral ({rsi:.1f})")
        else:
            rsi_score = 10
        score += rsi_score * self.weights['rsi'] / 0.25
        
        # 2. EMA Cross Score (0-20)
        ema_fast = features.get('ema_fast', 0)
        ema_slow = features.get('ema_slow', 0)
        ema_score = 0
        if ema_fast > 0 and ema_slow > 0:
            cross_pct = abs(ema_fast - ema_slow) / ema_slow * 100
            if ema_fast > ema_slow and direction != 'short':
                ema_score = min(20, cross_pct * 2)
                if direction == 'none':
                    direction = 'long'
                reasons.append(f"EMA bullish cross ({cross_pct:.2f}%)")
            elif ema_fast < ema_slow and direction != 'long':
                ema_score = min(20, cross_pct * 2)
                if direction == 'none':
                    direction = 'short'
                reasons.append(f"EMA bearish cross ({cross_pct:.2f}%)")
        score += ema_score * self.weights['ema_cross'] / 0.20
        
        # 3. Volume Score (0-15)
        volume_ratio = features.get('volume_ratio', 1)
        vol_score = 0
        if volume_ratio > 2:
            vol_score = 15
            reasons.append(f"High volume ({volume_ratio:.1f}x)")
        elif volume_ratio > 1.5:
            vol_score = 10
            reasons.append(f"Good volume ({volume_ratio:.1f}x)")
        elif volume_ratio < 0.5:
            vol_score = 2
            reasons.append(f"Low volume ({volume_ratio:.1f}x)")
        else:
            vol_score = 5
        score += vol_score * self.weights['volume'] / 0.15
        
        # 4. Momentum Score (0-15)
        momentum = features.get('momentum', 0)
        mom_score = min(15, abs(momentum) * 10)
        if momentum > 0 and direction == 'long':
            reasons.append(f"Positive momentum ({momentum:.3f})")
        elif momentum < 0 and direction == 'short':
            reasons.append(f"Negative momentum ({momentum:.3f})")
        score += mom_score * self.weights['momentum'] / 0.15
        
        # 5. Volatility Score (0-10) — подходит для скальпинга?
        volatility = features.get('volatility', 0)
        vol_score = 0
        if 0.01 <= volatility <= 0.05:  # 1-5% — идеально для скальпинга
            vol_score = 10
            reasons.append(f"Good volatility ({volatility*100:.1f}%)")
        elif volatility > 0.05:  # >5% — слишком волатильно
            vol_score = 5
            reasons.append(f"High volatility ({volatility*100:.1f}%)")
        else:  # <1% — низкая волатильность
            vol_score = 2
            reasons.append(f"Low volatility ({volatility*100:.1f}%)")
        score += vol_score * self.weights['volatility'] / 0.10
        
        # 6. Trend Score (0-15) — ADX или тренд
        trend = regime.get('trend', 'none')
        trend_strength = regime.get('strength', 0)
        trend_score = 0
        if trend == 'up' and direction == 'long':
            trend_score = min(15, trend_strength * 15)
            reasons.append(f"Uptrend ({trend_strength:.2f})")
        elif trend == 'down' and direction == 'short':
            trend_score = min(15, trend_strength * 15)
            reasons.append(f"Downtrend ({trend_strength:.2f})")
        elif trend == 'chop':
            trend_score = 3
            reasons.append("Choppy market")
        score += trend_score * self.weights['trend'] / 0.15
        
        # Финальный score
        final_score = min(100, max(0, int(score)))
        
        # Если regime плохой — снижаем score
        if not regime.get('tradeable', True):
            final_score = int(final_score * 0.3)  # Снижаем на 70%
            reasons.append("⚠️ Market regime NOT tradeable")
        
        return {
            'score': final_score,
            'direction': direction,
            'reasons': reasons,
            'should_trade': final_score >= 70 and direction != 'none',
            'confidence': 'high' if final_score >= 85 else 'medium' if final_score >= 70 else 'low',
        }
    
    def record_outcome(self, symbol: str, signal_id: str, pnl: float):
        """Записать исход сигнала для обучения."""
        if symbol not in self.signal_history:
            self.signal_history[symbol] = deque(maxlen=100)
        
        self.signal_history[symbol].append({
            'id': signal_id,
            'pnl': pnl,
            'timestamp': time.time(),
        })
    
    def get_win_rate(self, symbol: str) -> float:
        """Получить win rate для символа."""
        if symbol not in self.signal_history:
            return 0.5
        
        history = list(self.signal_history[symbol])
        if not history:
            return 0.5
        
        wins = sum(1 for h in history if h['pnl'] > 0)
        return wins / len(history) if history else 0.5