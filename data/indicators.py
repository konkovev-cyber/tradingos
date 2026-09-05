"""
IndicatorCalculator — расчёт индикаторов по свечам.

Все индикаторы считаются здесь и ТОЛЬКО здесь.
Стратегии НЕ считают индикаторы — они получают готовые значения.

Supported indicators:
    EMA (9, 20, 50, 200)
    RSI (14)
    ADX (14)
    ATR (14)
    MACD (12, 26, 9)
    Bollinger Bands (20, 2)
    VWAP
    Momentum (10)
    Volume Ratio (20)
"""
import logging
from typing import List, Dict, Any, Optional
from tradingos.data.models.candle import Candle

log = logging.getLogger("IndicatorCalculator")


class IndicatorCalculator:
    """
    Stateless indicator calculator.

    Takes candles, returns indicator values.
    No internal state — all state lives in FeatureStore.
    """

    # ══════════════════════════════════════════════════════════
    #  EMA
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def ema(prices: List[float], period: int) -> float:
        """
        Calculate EMA (Exponential Moving Average).

        Args:
            prices: List of prices (oldest first)
            period: EMA period

        Returns:
            Current EMA value
        """
        if len(prices) < period:
            return prices[-1] if prices else 0.0

        k = 2.0 / (period + 1)
        ema_val = sum(prices[:period]) / period

        for price in prices[period:]:
            ema_val = price * k + ema_val * (1 - k)

        return ema_val

    @staticmethod
    def ema_series(prices: List[float], period: int) -> List[float]:
        """
        Calculate EMA series (for each price).

        Returns:
            List of EMA values (same length as prices, first (period-1) are NaN)
        """
        if len(prices) < period:
            return [0.0] * len(prices)

        k = 2.0 / (period + 1)
        result = [0.0] * (period - 1)

        # Start with SMA
        sma = sum(prices[:period]) / period
        result.append(sma)

        # EMA
        ema_val = sma
        for price in prices[period:]:
            ema_val = price * k + ema_val * (1 - k)
            result.append(ema_val)

        return result

    # ══════════════════════════════════════════════════════════
    #  RSI
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def rsi(prices: List[float], period: int = 14) -> float:
        """
        Calculate RSI (Relative Strength Index) using Wilder's smoothing.

        Args:
            prices: List of closing prices (oldest first)
            period: RSI period (default 14)

        Returns:
            RSI value (0-100)
        """
        if len(prices) < period + 1:
            return 50.0

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        # First average (SMA)
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        # Wilder's smoothing
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def stochastic(
        candles: List["Candle"],
        k_period: int = 14,
        d_period: int = 3,
        smooth: int = 3,
    ) -> Dict[str, float]:
        """Stochastic Oscillator %K/%D на закрытых барах (без lookahead: бар -2).

        %K = (close - lowest_low) / (highest_high - lowest_low) * 100 за k_period.
        Возвращает {"k": float, "d": float, "cross_up": bool, "cross_down": bool}.
        cross_* — сигнал на последнем ЗАКРЫТОМ баре (пересечение %K через %D)."""
        n = len(candles)
        if n < k_period + d_period + smooth + 2:
            return {"k": 50.0, "d": 50.0, "cross_up": False, "cross_down": False}
        # работаем по закрытым барам: исключаем последний (ещё формируется)
        idx = -2  # последний закрытый бар
        ks = []
        for i in range(idx - smooth + 1, idx + 1):
            lo = min(c.low for c in candles[i - k_period + 1: i + 1])
            hi = max(c.high for c in candles[i - k_period + 1: i + 1])
            rng = hi - lo
            ks.append((candles[i].close - lo) / rng * 100 if rng > 0 else 50.0)
        k = sum(ks) / len(ks)
        # %D = SMA(%K, d_period) — берём предыдущие точки %K
        prev_ks = []
        for i in range(idx - d_period - smooth + 1, idx - smooth + 1):
            lo = min(c.low for c in candles[i - k_period + 1: i + 1])
            hi = max(c.high for c in candles[i - k_period + 1: i + 1])
            rng = hi - lo
            prev_ks.append((candles[i].close - lo) / rng * 100 if rng > 0 else 50.0)
        d = sum(prev_ks) / len(prev_ks) if prev_ks else k
        # %K до последнего закрытого (для пересечения)
        prev_lo = min(c.low for c in candles[idx - 1 - k_period + 1: idx])
        prev_hi = max(c.high for c in candles[idx - 1 - k_period + 1: idx])
        prev_rng = prev_hi - prev_lo
        k_prev = (candles[idx - 1].close - prev_lo) / prev_rng * 100 if prev_rng > 0 else 50.0
        d_prev = sum(prev_ks[:-1]) / max(len(prev_ks) - 1, 1) if len(prev_ks) > 1 else d
        return {
            "k": round(k, 1),
            "d": round(d, 1),
            "cross_up": k_prev <= d_prev and k > d,
            "cross_down": k_prev >= d_prev and k < d,
        }

    @staticmethod
    def rsi_squeeze(
        candles: List["Candle"],
        bb_period: int = 20,
        bb_mult: float = 2.0,
        keltner_mult: float = 1.5,
        atr_period: int = 20,
    ) -> Dict[str, Any]:
        """RSI-squeeze (TTM Squeeze style): Bollinger(20,2) vs Keltner(20,1.5×ATR).

        Squeeze ВКЛ = Bollinger-ширина < Keltner-ширина (сжатие волатильности).
        momentum = close - mid(Bollinger) — знак/рост = направление выхода.
        Возвращает {"squeeze_on": bool, "momentum": float, "bb_width": float,
        "kc_width": float, "fired": bool}. fired: squeeze_on переключился True→False
        на последнем закрытом баре (потенциальный breakout из сжатия)."""
        n = len(candles)
        if n < bb_period + atr_period + 2:
            return {"squeeze_on": False, "momentum": 0.0,
                    "bb_width": 0.0, "kc_width": 0.0, "fired": False}
        idx = -2  # последний закрытый бар
        prices = [c.close for c in candles]
        mid = sum(prices[idx - bb_period + 1: idx + 1]) / bb_period
        var = sum((p - mid) ** 2 for p in prices[idx - bb_period + 1: idx + 1]) / bb_period
        sd = var ** 0.5
        bb_upper = mid + bb_mult * sd
        bb_lower = mid - bb_mult * sd
        bb_width = bb_upper - bb_lower
        atr_v = IndicatorCalculator.atr(candles, atr_period)
        kc_width = 2 * keltner_mult * atr_v
        squeeze_on = bb_width < kc_width
        # предыдущий закрытый бар — для детекта 'fired'
        mid_p = sum(prices[idx - bb_period: idx]) / bb_period
        var_p = sum((p - mid_p) ** 2 for p in prices[idx - bb_period: idx]) / bb_period
        bb_width_p = 2 * bb_mult * (var_p ** 0.5)
        squeeze_prev = bb_width_p < kc_width
        return {
            "squeeze_on": squeeze_on,
            "momentum": round(prices[idx] - mid, 8),
            "bb_width": round(bb_width, 8),
            "kc_width": round(kc_width, 8),
            "fired": squeeze_prev and not squeeze_on,
        }

    # ══════════════════════════════════════════════════════════
    #  ADX
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def adx(candles: List[Candle], period: int = 14) -> float:
        """
        Calculate ADX (Average Directional Index).

        ADX measures trend strength:
        - < 20: no trend (ranging)
        - 20-40: moderate trend
        - > 40: strong trend

        Args:
            candles: List of Candle objects
            period: ADX period (default 14)

        Returns:
            ADX value (0-100)
        """
        if len(candles) < period + 1:
            return 0.0

        plus_dm = []
        minus_dm = []
        tr = []

        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_high = candles[i - 1].high
            prev_low = candles[i - 1].low
            prev_close = candles[i - 1].close

            up_move = high - prev_high
            down_move = prev_low - low

            plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0)
            minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0)

            tr.append(max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            ))

        if len(plus_dm) < period:
            return 0.0

        # Wilder's smoothing
        smoothed_plus = sum(plus_dm[:period]) / period
        smoothed_minus = sum(minus_dm[:period]) / period
        smoothed_tr = sum(tr[:period]) / period

        dx_values = []

        for i in range(period, len(plus_dm)):
            smoothed_plus = (smoothed_plus * (period - 1) + plus_dm[i]) / period
            smoothed_minus = (smoothed_minus * (period - 1) + minus_dm[i]) / period
            smoothed_tr = (smoothed_tr * (period - 1) + tr[i]) / period

            if smoothed_tr > 0:
                plus_di = 100 * smoothed_plus / smoothed_tr
                minus_di = 100 * smoothed_minus / smoothed_tr
            else:
                plus_di = 0
                minus_di = 0

            di_sum = plus_di + minus_di
            dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0
            dx_values.append(dx)

        if len(dx_values) < period:
            return 0.0

        # ADX = smoothed DX
        adx_val = sum(dx_values[:period]) / period
        for dx in dx_values[period:]:
            adx_val = (adx_val * (period - 1) + dx) / period

        return adx_val

    # ══════════════════════════════════════════════════════════
    #  ATR
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def atr(candles: List[Candle], period: int = 14) -> float:
        """
        Calculate ATR (Average True Range).

        Args:
            candles: List of Candle objects
            period: ATR period (default 14)

        Returns:
            ATR value (absolute, not percentage)
        """
        if len(candles) < 2:
            return 0.0

        true_ranges = []
        for i in range(1, len(candles)):
            high = candles[i].high
            low = candles[i].low
            prev_close = candles[i - 1].close

            tr = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close)
            )
            true_ranges.append(tr)

        if len(true_ranges) < period:
            return sum(true_ranges) / len(true_ranges) if true_ranges else 0.0

        # First ATR = SMA
        atr_val = sum(true_ranges[:period]) / period

        # Wilder's smoothing
        for tr in true_ranges[period:]:
            atr_val = (atr_val * (period - 1) + tr) / period

        return atr_val

    # ══════════════════════════════════════════════════════════
    #  MACD
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def macd(
        prices: List[float],
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Dict[str, float]:
        """
        Calculate MACD (Moving Average Convergence Divergence).

        Args:
            prices: List of closing prices
            fast: Fast EMA period (default 12)
            slow: Slow EMA period (default 26)
            signal: Signal EMA period (default 9)

        Returns:
            {"macd": float, "signal": float, "histogram": float}
        """
        if len(prices) < slow + signal:
            return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

        # Calculate EMA series
        ema_fast = IndicatorCalculator.ema_series(prices, fast)
        ema_slow = IndicatorCalculator.ema_series(prices, slow)

        # MACD line = EMA_fast - EMA_slow
        macd_line = []
        for i in range(len(prices)):
            if ema_fast[i] != 0 and ema_slow[i] != 0:
                macd_line.append(ema_fast[i] - ema_slow[i])
            else:
                macd_line.append(0.0)

        # Signal line = EMA of MACD
        valid_macd = [m for m in macd_line if m != 0]
        if len(valid_macd) < signal:
            return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

        signal_line = IndicatorCalculator.ema(valid_macd, signal)

        current_macd = macd_line[-1]
        histogram = current_macd - signal_line

        return {
            "macd": current_macd,
            "signal": signal_line,
            "histogram": histogram,
        }

    # ══════════════════════════════════════════════════════════
    #  Bollinger Bands
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def bollinger(
        prices: List[float],
        period: int = 20,
        std_dev: float = 2.0,
    ) -> Dict[str, float]:
        """
        Calculate Bollinger Bands.

        Args:
            prices: List of closing prices
            period: SMA period (default 20)
            std_dev: Standard deviation multiplier (default 2.0)

        Returns:
            {"upper": float, "middle": float, "lower": float, "width": float}
        """
        if len(prices) < period:
            p = prices[-1] if prices else 0.0
            return {"upper": p, "middle": p, "lower": p, "width": 0.0}

        recent = prices[-period:]
        middle = sum(recent) / period

        variance = sum((p - middle) ** 2 for p in recent) / period
        std = variance ** 0.5

        upper = middle + std_dev * std
        lower = middle - std_dev * std
        width = (upper - lower) / middle if middle > 0 else 0.0

        return {
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "width": width,
        }

    # ══════════════════════════════════════════════════════════
    #  VWAP
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def vwap(candles: List[Candle]) -> float:
        """
        Calculate VWAP (Volume Weighted Average Price).

        Uses all provided candles for calculation.

        Args:
            candles: List of Candle objects

        Returns:
            VWAP value
        """
        if not candles:
            return 0.0

        total_pv = 0.0
        total_volume = 0.0

        for c in candles:
            typical = (c.high + c.low + c.close) / 3
            total_pv += typical * c.volume
            total_volume += c.volume

        return total_pv / total_volume if total_volume > 0 else 0.0

    # ══════════════════════════════════════════════════════════
    #  Momentum
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def momentum(prices: List[float], period: int = 10) -> float:
        """
        Calculate momentum as percentage change.

        momentum = (current - prev) / prev

        Args:
            prices: List of closing prices
            period: Lookback period

        Returns:
            Momentum as fraction (e.g. 0.03 = 3%)
        """
        if len(prices) < period + 1:
            return 0.0

        old_price = prices[-(period + 1)]
        new_price = prices[-1]

        if old_price == 0:
            return 0.0

        return (new_price - old_price) / old_price

    # ══════════════════════════════════════════════════════════
    #  Volume Ratio
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def volume_ratio(volumes: List[float], period: int = 20) -> float:
        """
        Calculate volume ratio (current vs average).

        Args:
            volumes: List of volumes
            period: Average period

        Returns:
            Volume ratio (1.0 = average, >1.0 = above average)
        """
        if not volumes:
            return 1.0

        current = volumes[-1]
        avg = sum(volumes[-period:]) / min(len(volumes), period)

        return current / avg if avg > 0 else 1.0

    # ══════════════════════════════════════════════════════════
    #  Volatility
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def volatility(candles: List[Candle], period: int = 20) -> float:
        """
        Calculate average volatility as (high-low)/open.

        Args:
            candles: List of Candle objects
            period: Number of candles to average

        Returns:
            Average volatility as fraction
        """
        if not candles:
            return 0.001

        recent = candles[-period:]
        vols = []
        for c in recent:
            if c.open > 0:
                vols.append((c.high - c.low) / c.open)

        return sum(vols) / len(vols) if vols else 0.001

    # ══════════════════════════════════════════════════════════
    #  Trend Detection
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def trend_direction(ema_fast: float, ema_slow: float) -> str:
        """
        Determine trend direction from EMAs.

        Returns:
            "up", "down", or "neutral"
        """
        if ema_fast > ema_slow * 1.001:  # 0.1% threshold
            return "up"
        elif ema_fast < ema_slow * 0.999:
            return "down"
        return "neutral"

    # ══════════════════════════════════════════════════════════
    #  Batch Calculate
    # ══════════════════════════════════════════════════════════

    @classmethod
    def calculate_all(cls, candles: List[Candle]) -> Dict[str, Any]:
        """
        Calculate all indicators from candles.

        This is the main entry point for FeatureStore.

        Args:
            candles: List of Candle objects (oldest first)

        Returns:
            Dict with all indicator values
        """
        if len(candles) < 35:
            return {}

        prices = [c.close for c in candles]
        volumes = [c.volume for c in candles]

        # EMAs
        ema_9 = cls.ema(prices, 9)
        ema_20 = cls.ema(prices, 20)
        ema_50 = cls.ema(prices, 50) if len(prices) >= 50 else prices[-1]
        ema_200 = cls.ema(prices, 200) if len(prices) >= 200 else prices[-1]

        # RSI
        rsi = cls.rsi(prices, 14)

        # ADX
        adx = cls.adx(candles, 14)

        # ATR
        atr_val = cls.atr(candles, 14)
        atr_pct = atr_val / prices[-1] if prices[-1] > 0 else 0.0

        # MACD
        macd_data = cls.macd(prices, 12, 26, 9)

        # Bollinger
        bollinger = cls.bollinger(prices, 20, 2.0)

        # VWAP
        vwap = cls.vwap(candles)

        # Momentum
        mom = cls.momentum(prices, 10)

        # Volume ratio
        vol_ratio = cls.volume_ratio(volumes, 20)

        # Volatility
        vol = cls.volatility(candles, 20)

        # Trend
        trend = cls.trend_direction(ema_9, ema_20)

        return {
            "price": prices[-1],
            "ema_9": ema_9,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "ema_200": ema_200,
            "rsi": rsi,
            "adx": adx,
            "atr": atr_val,
            "atr_pct": atr_pct,
            "macd": macd_data["macd"],
            "macd_signal": macd_data["signal"],
            "macd_histogram": macd_data["histogram"],
            "bollinger_upper": bollinger["upper"],
            "bollinger_middle": bollinger["middle"],
            "bollinger_lower": bollinger["lower"],
            "bollinger_width": bollinger["width"],
            "vwap": vwap,
            "momentum": mom,
            "volume_ratio": vol_ratio,
            "volatility": vol,
            "trend_direction": trend,
            "candle_count": len(candles),
        }
