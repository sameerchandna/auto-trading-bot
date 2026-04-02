"""Wave ending/exhaustion pattern detection."""
import logging
import numpy as np

from data.models import Candle, WaveState

logger = logging.getLogger(__name__)


def calculate_rsi(candles: list[Candle], period: int = 14) -> list[float]:
    """Calculate RSI values."""
    if len(candles) < period + 1:
        return []

    closes = [c.close for c in candles]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

    gains = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_values = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi_values.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(100 - (100 / (1 + rs)))

    return rsi_values


def detect_divergence(
    candles: list[Candle],
    period: int = 14,
    lookback: int = 20,
) -> str:
    """Detect price/RSI divergence as wave exhaustion signal.

    Returns: 'bullish_divergence', 'bearish_divergence', or 'none'
    """
    rsi_values = calculate_rsi(candles, period)
    if len(rsi_values) < lookback:
        return "none"

    recent_candles = candles[-(lookback):]
    recent_rsi = rsi_values[-(lookback):]

    # Find recent swing highs and lows in price and RSI
    price_closes = [c.close for c in recent_candles]

    # Bearish divergence: price makes higher high, RSI makes lower high
    price_max_idx = np.argmax(price_closes[-10:])
    price_prev_max_idx = np.argmax(price_closes[:10])

    if (price_closes[-10:][price_max_idx] > price_closes[:10][price_prev_max_idx] and
        recent_rsi[-10:][price_max_idx] < recent_rsi[:10][price_prev_max_idx]):
        return "bearish_divergence"

    # Bullish divergence: price makes lower low, RSI makes higher low
    price_min_idx = np.argmin(price_closes[-10:])
    price_prev_min_idx = np.argmin(price_closes[:10])

    if (price_closes[-10:][price_min_idx] < price_closes[:10][price_prev_min_idx] and
        recent_rsi[-10:][price_min_idx] > recent_rsi[:10][price_prev_min_idx]):
        return "bullish_divergence"

    return "none"


def is_wave_exhausted(
    candles: list[Candle],
    wave: WaveState,
) -> tuple[bool, dict]:
    """Check multiple exhaustion signals for wave ending.

    Returns (is_exhausted, details_dict).
    """
    details = {
        "divergence": "none",
        "rsi_extreme": False,
        "diminishing_momentum": wave.is_exhausted,
        "overextended": False,
        "exhaustion_score": 0.0,
    }

    if len(candles) < 30:
        return False, details

    # 1. RSI divergence
    divergence = detect_divergence(candles)
    details["divergence"] = divergence
    score = 0.0

    if divergence != "none":
        score += 0.35

    # 2. RSI extremes
    rsi_values = calculate_rsi(candles)
    if rsi_values:
        current_rsi = rsi_values[-1]
        if current_rsi > 75 or current_rsi < 25:
            details["rsi_extreme"] = True
            score += 0.20

    # 3. Diminishing momentum (from wave analysis)
    if wave.is_exhausted:
        score += 0.25

    # 4. Overextension from mean
    closes = [c.close for c in candles[-50:]]
    if len(closes) >= 20:
        sma20 = np.mean(closes[-20:])
        current = closes[-1]
        deviation = abs(current - sma20) / sma20

        if deviation > 0.01:  # More than 1% from 20-period mean
            details["overextended"] = True
            score += 0.20

    details["exhaustion_score"] = min(1.0, score)
    is_exhausted = score >= 0.50

    return is_exhausted, details
