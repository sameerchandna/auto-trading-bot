"""Liquidity analysis: equal highs/lows, stop hunts, sweeps."""
import logging
from datetime import datetime

from data.models import Candle, SwingPoint, LiquiditySweep, Direction

logger = logging.getLogger(__name__)

# Price tolerance for "equal" levels (as fraction of price)
EQUAL_LEVEL_TOLERANCE = 0.0003  # ~3 pips for EURUSD


def find_equal_levels(
    swing_points: list[SwingPoint],
    tolerance: float = EQUAL_LEVEL_TOLERANCE,
) -> list[tuple[float, int, bool]]:
    """Find clusters of equal highs or equal lows.

    Returns list of (price_level, count, is_high).
    Equal levels represent liquidity pools where stops accumulate.
    """
    if not swing_points:
        return []

    highs = sorted([s for s in swing_points if s.is_high], key=lambda s: s.price)
    lows = sorted([s for s in swing_points if not s.is_high], key=lambda s: s.price)

    levels = []

    for points, is_high in [(highs, True), (lows, False)]:
        if not points:
            continue

        clusters = []
        current_cluster = [points[0]]

        for i in range(1, len(points)):
            if abs(points[i].price - current_cluster[0].price) / current_cluster[0].price <= tolerance:
                current_cluster.append(points[i])
            else:
                if len(current_cluster) >= 2:
                    avg_price = sum(p.price for p in current_cluster) / len(current_cluster)
                    clusters.append((avg_price, len(current_cluster)))
                current_cluster = [points[i]]

        if len(current_cluster) >= 2:
            avg_price = sum(p.price for p in current_cluster) / len(current_cluster)
            clusters.append((avg_price, len(current_cluster)))

        for price, count in clusters:
            levels.append((price, count, is_high))

    return levels


def detect_liquidity_sweeps(
    candles: list[Candle],
    swing_points: list[SwingPoint],
    tolerance: float = EQUAL_LEVEL_TOLERANCE,
) -> list[LiquiditySweep]:
    """Detect when price sweeps past a key level then reverses.

    A sweep occurs when:
    1. Price pushes past a swing high/low or equal level
    2. Then closes back below/above it in the same or next candle
    This indicates stop hunting / liquidity grab.
    """
    if len(candles) < 3 or not swing_points:
        return []

    sweeps = []
    tf = candles[0].timeframe

    # Get significant levels
    equal_levels = find_equal_levels(swing_points, tolerance)

    # Also use individual swing points as levels
    all_levels = []
    for price, count, is_high in equal_levels:
        all_levels.append((price, is_high, count))

    for sp in swing_points[-20:]:  # Recent swing points
        all_levels.append((sp.price, sp.is_high, sp.strength))

    # Check recent candles for sweeps
    for i in range(2, min(len(candles), 10)):
        candle = candles[-i]
        prev_candle = candles[-i - 1] if i + 1 <= len(candles) else None

        for level_price, is_high_level, strength in all_levels:
            if is_high_level:
                # Sweep above: wick goes above level but close stays below
                if (candle.high > level_price and
                    candle.close < level_price and
                    (prev_candle is None or prev_candle.high <= level_price)):
                    sweeps.append(LiquiditySweep(
                        timestamp=candle.timestamp,
                        price=candle.high,
                        swept_level=level_price,
                        direction=Direction.SHORT,  # Bearish sweep
                        timeframe=tf,
                        confirmed=candle.close < level_price,
                    ))
            else:
                # Sweep below: wick goes below level but close stays above
                if (candle.low < level_price and
                    candle.close > level_price and
                    (prev_candle is None or prev_candle.low >= level_price)):
                    sweeps.append(LiquiditySweep(
                        timestamp=candle.timestamp,
                        price=candle.low,
                        swept_level=level_price,
                        direction=Direction.LONG,  # Bullish sweep
                        timeframe=tf,
                        confirmed=candle.close > level_price,
                    ))

    return sweeps
