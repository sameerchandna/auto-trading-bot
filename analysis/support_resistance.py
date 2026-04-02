"""Support and resistance zone detection."""
import logging
from collections import defaultdict
from datetime import datetime

import numpy as np

from data.models import Candle, SwingPoint, SRZone

logger = logging.getLogger(__name__)

# Zone width as fraction of price
ZONE_WIDTH = 0.0005  # ~5 pips for EURUSD


def detect_sr_zones(
    candles: list[Candle],
    swing_points: list[SwingPoint],
    zone_width: float = ZONE_WIDTH,
) -> list[SRZone]:
    """Detect support and resistance zones.

    Zones form where price repeatedly reacts. More touches = stronger zone.
    Zones are treated as magnets: price tends to move toward them,
    but can also break through to make new highs/lows.
    """
    if not swing_points:
        return []

    tf = candles[0].timeframe if candles else "unknown"
    current_price = candles[-1].close if candles else 0

    # Cluster swing points into zones
    all_prices = sorted([sp.price for sp in swing_points])
    zones = []
    used = set()

    for i, price in enumerate(all_prices):
        if i in used:
            continue

        # Find all swing points within zone_width of this price
        cluster_prices = [price]
        cluster_indices = [i]

        for j in range(i + 1, len(all_prices)):
            if j in used:
                continue
            if abs(all_prices[j] - price) / price <= zone_width:
                cluster_prices.append(all_prices[j])
                cluster_indices.append(j)

        if len(cluster_prices) >= 2:
            for idx in cluster_indices:
                used.add(idx)

            zone_low = min(cluster_prices)
            zone_high = max(cluster_prices)
            mid = (zone_low + zone_high) / 2

            # Find the most recent touch
            touches = [
                sp for sp in swing_points
                if zone_low <= sp.price <= zone_high
            ]
            last_touch = max(t.timestamp for t in touches) if touches else None

            zone_type = "support" if mid < current_price else "resistance"

            zones.append(SRZone(
                price_low=round(zone_low, 5),
                price_high=round(zone_high, 5),
                timeframe=tf,
                strength=len(cluster_prices),
                zone_type=zone_type,
                last_touch=last_touch,
            ))

    # Sort by strength (most touches first)
    zones.sort(key=lambda z: z.strength, reverse=True)
    return zones[:20]  # Keep top 20 zones


def find_nearest_sr(
    price: float,
    zones: list[SRZone],
) -> tuple[SRZone | None, SRZone | None]:
    """Find nearest support and resistance zones to current price.

    Returns (nearest_support, nearest_resistance).
    """
    supports = [z for z in zones if z.price_high < price]
    resistances = [z for z in zones if z.price_low > price]

    nearest_support = None
    nearest_resistance = None

    if supports:
        nearest_support = min(supports, key=lambda z: price - z.price_high)
    if resistances:
        nearest_resistance = min(resistances, key=lambda z: z.price_low - price)

    return nearest_support, nearest_resistance


def price_at_zone(price: float, zones: list[SRZone], tolerance: float = 0.0002) -> SRZone | None:
    """Check if price is at/near any S/R zone."""
    for zone in zones:
        zone_mid = (zone.price_low + zone.price_high) / 2
        if abs(price - zone_mid) / zone_mid <= tolerance:
            return zone
    return None
