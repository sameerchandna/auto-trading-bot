"""Market structure detection: swing points, BOS, CHoCH."""
import logging
from typing import Optional

from data.models import (
    Candle, SwingPoint, MarketStructure, Bias, StructureBreak,
)
from config.settings import SWING_LOOKBACK

logger = logging.getLogger(__name__)


def detect_swing_points(
    candles: list[Candle],
    lookback: int = SWING_LOOKBACK,
) -> list[SwingPoint]:
    """Detect swing highs and lows using Williams fractals.

    A swing high: bar whose high is the highest of (lookback) bars on each side.
    A swing low: bar whose low is the lowest of (lookback) bars on each side.
    """
    if len(candles) < lookback * 2 + 1:
        return []

    swings = []
    for i in range(lookback, len(candles) - lookback):
        candle = candles[i]

        # Check swing high
        is_high = True
        for j in range(1, lookback + 1):
            if candles[i - j].high >= candle.high or candles[i + j].high >= candle.high:
                is_high = False
                break

        if is_high:
            swings.append(SwingPoint(
                timestamp=candle.timestamp,
                price=candle.high,
                is_high=True,
                timeframe=candle.timeframe,
                confirmed=True,
                strength=lookback,
            ))

        # Check swing low
        is_low = True
        for j in range(1, lookback + 1):
            if candles[i - j].low <= candle.low or candles[i + j].low <= candle.low:
                is_low = False
                break

        if is_low:
            swings.append(SwingPoint(
                timestamp=candle.timestamp,
                price=candle.low,
                is_high=False,
                timeframe=candle.timeframe,
                confirmed=True,
                strength=lookback,
            ))

    # Sort by timestamp
    swings.sort(key=lambda s: s.timestamp)
    return swings


def analyze_structure(
    candles: list[Candle],
    lookback: int = SWING_LOOKBACK,
) -> MarketStructure:
    """Analyze market structure from candle data.

    Determines bias (bullish/bearish/ranging) by tracking:
    - HH + HL = bullish
    - LH + LL = bearish
    - Mixed = ranging

    Detects BOS (trend continuation) and CHoCH (reversal).
    """
    if len(candles) < lookback * 2 + 1:
        return MarketStructure(timeframe=candles[0].timeframe if candles else "unknown")

    tf = candles[0].timeframe
    swings = detect_swing_points(candles, lookback)

    if len(swings) < 4:
        return MarketStructure(timeframe=tf, swing_points=swings)

    # Separate highs and lows
    highs = [s for s in swings if s.is_high]
    lows = [s for s in swings if not s.is_high]

    if len(highs) < 2 or len(lows) < 2:
        return MarketStructure(timeframe=tf, swing_points=swings)

    # Determine structure from recent swings
    last_high = highs[-1]
    prev_high = highs[-2]
    last_low = lows[-1]
    prev_low = lows[-2]

    higher_high = last_high.price > prev_high.price
    higher_low = last_low.price > prev_low.price
    lower_high = last_high.price < prev_high.price
    lower_low = last_low.price < prev_low.price

    # Determine bias
    if higher_high and higher_low:
        bias = Bias.BULLISH
    elif lower_high and lower_low:
        bias = Bias.BEARISH
    else:
        bias = Bias.RANGING

    # Detect BOS and CHoCH
    current_price = candles[-1].close
    last_break = StructureBreak.NONE
    break_price = None
    break_timestamp = None

    if bias == Bias.BULLISH:
        # BOS = price breaks above last swing high
        if current_price > last_high.price:
            last_break = StructureBreak.BOS
            break_price = last_high.price
            break_timestamp = candles[-1].timestamp
        # CHoCH = price breaks below last swing low (reversal signal)
        elif current_price < last_low.price:
            last_break = StructureBreak.CHOCH
            break_price = last_low.price
            break_timestamp = candles[-1].timestamp

    elif bias == Bias.BEARISH:
        # BOS = price breaks below last swing low
        if current_price < last_low.price:
            last_break = StructureBreak.BOS
            break_price = last_low.price
            break_timestamp = candles[-1].timestamp
        # CHoCH = price breaks above last swing high (reversal signal)
        elif current_price > last_high.price:
            last_break = StructureBreak.CHOCH
            break_price = last_high.price
            break_timestamp = candles[-1].timestamp

    return MarketStructure(
        timeframe=tf,
        bias=bias,
        last_swing_high=last_high,
        last_swing_low=last_low,
        last_break=last_break,
        break_price=break_price,
        break_timestamp=break_timestamp,
        swing_points=swings,
    )
