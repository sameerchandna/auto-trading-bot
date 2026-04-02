"""Confluence scoring - rates trade setups across multiple factors."""
import logging
from datetime import datetime

from data.models import (
    PriceContext, Signal, Direction, SignalType, Bias,
    WavePhase, StructureBreak,
)
from analysis.support_resistance import price_at_zone, find_nearest_sr
from analysis.wave_endings import is_wave_exhausted
from config.settings import (
    CONFLUENCE_WEIGHTS, CONFLUENCE_THRESHOLD,
    SL_ATR_MULTIPLIER, TP_RISK_REWARD, PAIR_NAME,
)

logger = logging.getLogger(__name__)


def score_confluence(
    context: PriceContext,
    weights: dict[str, float] | None = None,
) -> list[Signal]:
    """Score all possible setups and return signals that meet threshold.

    Evaluates multiple factors across timeframes and generates
    signals when confluence is strong enough.
    """
    if weights is None:
        weights = CONFLUENCE_WEIGHTS

    signals = []

    # Find the lowest available timeframe as entry TF
    tf_priority = ["15m", "1h", "4h", "1d", "1wk"]
    entry_tf = None
    entry_tf_name = None
    for tf in tf_priority:
        if tf in context.analyses:
            entry_tf = context.analyses[tf]
            entry_tf_name = tf
            break

    if entry_tf is None:
        return signals

    current_price = entry_tf.current_price

    if current_price == 0:
        return signals

    # Evaluate LONG setup
    long_score = _score_direction(context, Direction.LONG, weights)
    if long_score >= CONFLUENCE_THRESHOLD:
        signal = _build_signal(
            context, Direction.LONG, long_score, entry_tf.atr, entry_tf_name
        )
        if signal:
            signals.append(signal)

    # Evaluate SHORT setup
    short_score = _score_direction(context, Direction.SHORT, weights)
    if short_score >= CONFLUENCE_THRESHOLD:
        signal = _build_signal(
            context, Direction.SHORT, short_score, entry_tf.atr, entry_tf_name
        )
        if signal:
            signals.append(signal)

    return signals


def _score_direction(
    context: PriceContext,
    direction: Direction,
    weights: dict[str, float],
) -> float:
    """Score a specific direction (long or short)."""
    scores = {}
    target_bias = Bias.BULLISH if direction == Direction.LONG else Bias.BEARISH
    target_wave = WavePhase.CORRECTION_DOWN if direction == Direction.LONG else WavePhase.CORRECTION_UP

    # 1. HTF Bias alignment
    htf_bias = context.get_htf_bias()
    if htf_bias == target_bias:
        scores["htf_bias"] = 1.0
    elif htf_bias == Bias.RANGING:
        scores["htf_bias"] = 0.3
    else:
        scores["htf_bias"] = 0.0

    # 2. Break of Structure
    bos_score = 0.0
    for tf in ["4h", "1h"]:
        if tf in context.analyses:
            analysis = context.analyses[tf]
            if analysis.structure.last_break == StructureBreak.BOS:
                if analysis.structure.bias == target_bias:
                    bos_score = max(bos_score, 1.0)
            elif analysis.structure.last_break == StructureBreak.CHOCH:
                # CHoCH in our direction = potential reversal entry
                bos_score = max(bos_score, 0.6)
    scores["bos"] = bos_score

    # 3. Wave position (want to enter during correction)
    wave_score = 0.0
    for tf in ["4h", "1h"]:
        if tf in context.analyses:
            wave = context.analyses[tf].wave
            if wave.phase == target_wave:
                # In correction - good entry zone
                if 0.38 <= wave.correction_depth <= 0.78:
                    wave_score = max(wave_score, 1.0)  # Fib sweet spot
                elif wave.correction_depth > 0:
                    wave_score = max(wave_score, 0.6)
    scores["wave_position"] = wave_score

    # 4. Liquidity sweep
    sweep_score = 0.0
    for tf in ["15m", "1h"]:
        if tf in context.analyses:
            for sweep in context.analyses[tf].liquidity_sweeps:
                if sweep.confirmed and sweep.direction == direction:
                    sweep_score = 1.0
                    break
    scores["liquidity_sweep"] = sweep_score

    # 5. S/R reaction
    sr_score = 0.0
    for tf in ["1h", "4h", "1d"]:
        if tf in context.analyses:
            analysis = context.analyses[tf]
            current = analysis.current_price
            zone = price_at_zone(current, analysis.sr_zones)
            if zone:
                if (direction == Direction.LONG and zone.zone_type == "support"):
                    sr_score = min(1.0, zone.strength / 3)
                elif (direction == Direction.SHORT and zone.zone_type == "resistance"):
                    sr_score = min(1.0, zone.strength / 3)
    scores["sr_reaction"] = sr_score

    # 6. Catalyst (placeholder - will be enhanced with news feed)
    scores["catalyst"] = 0.0

    # 7. Wave ending (counter-trend signal)
    wave_end_score = 0.0
    for tf in ["4h", "1h"]:
        if tf in context.analyses:
            # Wave ending supports reversal trades
            wave = context.analyses[tf].wave
            if wave.is_exhausted:
                # Only score if exhaustion is in the OPPOSITE direction
                if (direction == Direction.LONG and
                    wave.phase in [WavePhase.IMPULSE_DOWN, WavePhase.CORRECTION_DOWN]):
                    wave_end_score = 0.8
                elif (direction == Direction.SHORT and
                      wave.phase in [WavePhase.IMPULSE_UP, WavePhase.CORRECTION_UP]):
                    wave_end_score = 0.8
    scores["wave_ending"] = wave_end_score

    # Calculate weighted total
    total = sum(scores.get(k, 0) * weights.get(k, 0) for k in weights)

    logger.debug(
        f"{direction.value} confluence: {total:.2f} "
        f"(htf={scores['htf_bias']:.1f} bos={scores['bos']:.1f} "
        f"wave={scores['wave_position']:.1f} liq={scores['liquidity_sweep']:.1f} "
        f"sr={scores['sr_reaction']:.1f} cat={scores['catalyst']:.1f} "
        f"end={scores['wave_ending']:.1f})"
    )

    return total


def _build_signal(
    context: PriceContext,
    direction: Direction,
    confluence_score: float,
    atr: float,
    entry_tf_name: str = "15m",
) -> Signal | None:
    """Build a trade signal with entry, SL, and TP."""
    entry_tf = context.analyses.get(entry_tf_name)
    if not entry_tf or atr == 0:
        return None

    current_price = entry_tf.current_price

    if direction == Direction.LONG:
        stop_loss = round(current_price - atr * SL_ATR_MULTIPLIER, 5)
        risk = current_price - stop_loss
        take_profit = round(current_price + risk * TP_RISK_REWARD, 5)
    else:
        stop_loss = round(current_price + atr * SL_ATR_MULTIPLIER, 5)
        risk = stop_loss - current_price
        take_profit = round(current_price - risk * TP_RISK_REWARD, 5)

    # Determine signal type based on what scored highest
    signal_type = SignalType.BOS_CONTINUATION  # Default

    return Signal(
        timestamp=datetime.utcnow(),
        pair=PAIR_NAME,
        direction=direction,
        signal_type=signal_type,
        entry_price=current_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confluence_score=confluence_score,
        rationale={
            "overall_bias": context.overall_bias.value,
            "bias_strength": context.bias_strength,
        },
        entry_timeframe=entry_tf_name,
        trigger_timeframe="4h",
    )
