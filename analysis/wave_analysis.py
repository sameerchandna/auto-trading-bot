"""Wave analysis: impulse/correction detection and wave counting."""
import logging
import numpy as np

from data.models import Candle, WaveState, WavePhase, SwingPoint
from analysis.market_structure import detect_swing_points

logger = logging.getLogger(__name__)


def calculate_move_strength(candles: list[Candle]) -> float:
    """Calculate the strength of a price move (0-1).

    Strong moves: large bodies, small wicks, consistent direction.
    Weak moves: small bodies, large wicks, overlapping candles.
    """
    if len(candles) < 2:
        return 0.0

    total_range = abs(candles[-1].close - candles[0].open)
    total_candle_ranges = sum(c.range for c in candles)

    if total_candle_ranges == 0:
        return 0.0

    # Efficiency: how much of the candle ranges contributed to the net move
    efficiency = total_range / total_candle_ranges

    # Body ratio: average body size vs total range
    avg_body_ratio = np.mean([c.body_size / c.range if c.range > 0 else 0 for c in candles])

    # Direction consistency: how many candles moved in the same direction
    if total_range > 0:
        bullish_count = sum(1 for c in candles if c.is_bullish)
        direction_consistency = bullish_count / len(candles)
    else:
        bearish_count = sum(1 for c in candles if not c.is_bullish)
        direction_consistency = bearish_count / len(candles)

    strength = (efficiency * 0.4 + avg_body_ratio * 0.3 + direction_consistency * 0.3)
    return min(1.0, max(0.0, strength))


def detect_waves(candles: list[Candle], lookback: int = 5) -> WaveState:
    """Detect current wave phase and count.

    Waves alternate between impulse (strong, directional) and correction
    (weaker, overlapping, retracing part of the impulse).
    """
    tf = candles[0].timeframe if candles else "unknown"

    if len(candles) < 20:
        return WaveState(timeframe=tf)

    swings = detect_swing_points(candles, lookback)
    if len(swings) < 3:
        return WaveState(timeframe=tf)

    # Build wave segments between swing points
    segments = []
    for i in range(len(swings) - 1):
        start_swing = swings[i]
        end_swing = swings[i + 1]

        # Find candles in this segment
        seg_candles = [
            c for c in candles
            if start_swing.timestamp <= c.timestamp <= end_swing.timestamp
        ]
        if not seg_candles:
            continue

        move = end_swing.price - start_swing.price
        strength = calculate_move_strength(seg_candles)

        segments.append({
            "start": start_swing,
            "end": end_swing,
            "move": move,
            "abs_move": abs(move),
            "strength": strength,
            "is_up": move > 0,
            "candle_count": len(seg_candles),
        })

    if not segments:
        return WaveState(timeframe=tf)

    # Classify segments as impulse or correction
    # Impulse: stronger, larger moves. Correction: weaker, smaller moves.
    avg_move = np.mean([s["abs_move"] for s in segments])
    avg_strength = np.mean([s["strength"] for s in segments])

    for seg in segments:
        seg["is_impulse"] = (
            seg["abs_move"] >= avg_move * 0.8 and
            seg["strength"] >= avg_strength * 0.7
        )

    # Determine current phase from the last segment
    last_seg = segments[-1]
    prev_seg = segments[-2] if len(segments) >= 2 else None

    if last_seg["is_impulse"]:
        phase = WavePhase.IMPULSE_UP if last_seg["is_up"] else WavePhase.IMPULSE_DOWN
    else:
        phase = WavePhase.CORRECTION_UP if last_seg["is_up"] else WavePhase.CORRECTION_DOWN

    # Count waves (alternating impulse/correction)
    wave_count = 0
    for seg in segments:
        if seg["is_impulse"]:
            wave_count += 1

    # Calculate correction depth relative to previous impulse
    correction_depth = 0.0
    if prev_seg and prev_seg["is_impulse"] and not last_seg["is_impulse"]:
        if prev_seg["abs_move"] > 0:
            correction_depth = last_seg["abs_move"] / prev_seg["abs_move"]

    # Check for exhaustion (diminishing impulse strength)
    impulse_segments = [s for s in segments if s["is_impulse"]]
    is_exhausted = False
    if len(impulse_segments) >= 3:
        recent_strengths = [s["strength"] for s in impulse_segments[-3:]]
        if all(recent_strengths[i] < recent_strengths[i - 1] for i in range(1, len(recent_strengths))):
            is_exhausted = True

    return WaveState(
        timeframe=tf,
        phase=phase,
        wave_count=wave_count,
        impulse_strength=last_seg["strength"],
        correction_depth=correction_depth,
        is_exhausted=is_exhausted,
    )
