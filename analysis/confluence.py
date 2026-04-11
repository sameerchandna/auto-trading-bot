"""Confluence scoring - rates trade setups across multiple factors."""
import logging
from datetime import datetime

from data.models import (
    PriceContext, Signal, Direction, SignalType, Bias,
    WavePhase, StructureBreak, Regime,
)
from analysis.support_resistance import price_at_zone, find_nearest_sr
from analysis.wave_endings import is_wave_exhausted
from config.settings import (
    CONFLUENCE_WEIGHTS, CONFLUENCE_THRESHOLD, SIGNAL_DOMINANCE_MARGIN,
    SL_ATR_MULTIPLIER, TP_RISK_REWARD,
)

logger = logging.getLogger(__name__)


def score_confluence(
    context: PriceContext,
    weights: dict[str, float] | None = None,
    threshold: float | None = None,
    dominance_margin: float | None = None,
    sl_atr_mult: float | None = None,
    tp_rr: float | None = None,
    sl_method: str = "atr",
    regime_filter_enabled: bool = False,
    regime_params_enabled: bool = False,
    regime_params: dict[str, dict] | None = None,
    signal_model_enabled: bool = False,
    signal_model_min_confidence: float = 0.5,
    news_filter_enabled: bool = False,
    news_block_before_mins: int = 30,
    news_block_after_mins: int = 15,
    current_time: datetime | None = None,
) -> list[Signal]:
    """Score all possible setups and return signals that meet threshold.

    Evaluates multiple factors across timeframes and generates
    signals when confluence is strong enough.

    When regime_filter_enabled is True, signals are blocked if the market
    regime is RANGING (ADX below threshold on the reference TF).

    When regime_params_enabled is True, per-regime overrides (threshold,
    sl_multiplier, tp_risk_reward) are merged on top of base params.
    """
    if weights is None:
        weights = CONFLUENCE_WEIGHTS
    if threshold is None:
        threshold = CONFLUENCE_THRESHOLD
    if dominance_margin is None:
        dominance_margin = SIGNAL_DOMINANCE_MARGIN
    if sl_atr_mult is None:
        sl_atr_mult = SL_ATR_MULTIPLIER
    if tp_rr is None:
        tp_rr = TP_RISK_REWARD

    # Apply regime-specific param overrides
    if regime_params_enabled and regime_params:
        regime_key = context.regime.value  # "trending", "ranging", or "volatile"
        overrides = regime_params.get(regime_key, {})
        if overrides:
            threshold = overrides.get("threshold", threshold)
            sl_atr_mult = overrides.get("sl_multiplier", sl_atr_mult)
            tp_rr = overrides.get("tp_risk_reward", tp_rr)
            logger.debug(
                f"Regime override ({regime_key}): threshold={threshold}, "
                f"sl_mult={sl_atr_mult}, tp_rr={tp_rr}"
            )

    signals = []

    # Regime filter — skip signal generation entirely when market is ranging
    if regime_filter_enabled and context.regime == Regime.RANGING:
        logger.debug("Regime filter: RANGING — skipping signal generation")
        return signals

    # News filter — skip signal generation during high-impact news windows
    if news_filter_enabled:
        from data.economic_calendar import is_news_blocked
        check_time = current_time or context.timestamp
        if is_news_blocked(check_time, news_block_before_mins, news_block_after_mins):
            logger.debug("News filter: high-impact event window — skipping")
            return signals

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

    long_score, long_trigger_tf, long_scores = _score_direction(context, Direction.LONG, weights)
    short_score, short_trigger_tf, short_scores = _score_direction(context, Direction.SHORT, weights)

    best_score = max(long_score, short_score)
    worst_score = min(long_score, short_score)

    # Only emit a signal if best clears threshold AND dominates the other direction.
    # If both are high (contested), emit nothing — market is ambiguous.
    if best_score >= threshold and (best_score - worst_score) >= dominance_margin:
        direction = Direction.LONG if long_score > short_score else Direction.SHORT
        winning_scores = long_scores if long_score > short_score else short_scores
        winning_trigger_tf = long_trigger_tf if long_score > short_score else short_trigger_tf
        signal = _build_signal(
            context, direction, best_score, entry_tf.atr, entry_tf_name,
            trigger_tf=winning_trigger_tf, scores=winning_scores,
            sl_atr_mult=sl_atr_mult, tp_rr=tp_rr, sl_method=sl_method,
        )
        if signal:
            # Model-based confidence filter
            if signal_model_enabled:
                from analysis.signal_model import predict_win_probability
                prob = predict_win_probability(signal)
                if prob is not None:
                    signal.rationale["model_confidence"] = prob
                    if prob < signal_model_min_confidence:
                        logger.debug(
                            f"Model filter: P(win)={prob:.3f} < {signal_model_min_confidence} — "
                            f"rejecting {signal.direction.value} signal"
                        )
                        return signals  # empty
            signals.append(signal)

    return signals


def _derive_signal_type(scores: dict) -> SignalType:
    """Derive signal type from which factor scored highest."""
    if scores.get("liquidity_sweep", 0) >= 1.0:
        return SignalType.LIQUIDITY_SWEEP
    if scores.get("bos", 0) >= 1.0:
        return SignalType.BOS_CONTINUATION
    if scores.get("wave_ending", 0) >= 0.8:
        return SignalType.WAVE_ENDING
    return SignalType.BOS_CONTINUATION


def _score_direction(
    context: PriceContext,
    direction: Direction,
    weights: dict[str, float],
) -> tuple[float, str, dict]:
    """Score a specific direction. Returns (score, trigger_timeframe, scores)."""
    scores = {}
    trigger_tf = "4h"  # default
    target_bias = Bias.BULLISH if direction == Direction.LONG else Bias.BEARISH
    target_wave = WavePhase.CORRECTION_DOWN if direction == Direction.LONG else WavePhase.CORRECTION_UP

    # 1. HTF Bias alignment (score daily + weekly individually)
    daily = context.analyses.get("1d")
    weekly = context.analyses.get("1wk")
    d_bias = daily.structure.bias if daily else Bias.RANGING
    w_bias = weekly.structure.bias if weekly else Bias.RANGING

    if d_bias == target_bias and w_bias == target_bias:
        scores["htf_bias"] = 1.0   # Full agreement
    elif d_bias == target_bias and w_bias == Bias.RANGING:
        scores["htf_bias"] = 0.8   # Daily leading, weekly not opposing
    elif d_bias == Bias.RANGING and w_bias == target_bias:
        scores["htf_bias"] = 0.7   # Weekly confirms, daily undecided
    elif d_bias == target_bias and w_bias != target_bias:
        scores["htf_bias"] = 0.5   # Daily leads but weekly opposes — possible reversal
    elif d_bias == Bias.RANGING and w_bias == Bias.RANGING:
        scores["htf_bias"] = 0.3   # No directional info
    elif d_bias != target_bias and w_bias == Bias.RANGING:
        scores["htf_bias"] = 0.1   # Daily opposes, weekly neutral
    else:
        scores["htf_bias"] = 0.0   # Both oppose or weekly opposes with no daily support

    # 2. Break of Structure
    bos_score = 0.0
    for tf in ["4h", "1h"]:
        if tf in context.analyses:
            analysis = context.analyses[tf]
            if analysis.structure.last_break == StructureBreak.BOS:
                if analysis.structure.bias == target_bias:
                    if bos_score < 1.0:
                        bos_score = 1.0
                        trigger_tf = tf
            elif analysis.structure.last_break == StructureBreak.CHOCH:
                # CHoCH in our direction = potential reversal entry
                if bos_score < 0.6:
                    bos_score = 0.6
                    trigger_tf = tf
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

    # 4. Liquidity sweep (overrides BOS as trigger TF when present)
    sweep_score = 0.0
    for tf in ["15m", "1h"]:
        if tf in context.analyses:
            for sweep in context.analyses[tf].liquidity_sweeps:
                if sweep.confirmed and sweep.direction == direction:
                    sweep_score = 1.0
                    trigger_tf = tf
                    break
        if sweep_score == 1.0:
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
        f"{direction.value} confluence: {total:.2f} trigger={trigger_tf} "
        f"(htf={scores['htf_bias']:.1f} bos={scores['bos']:.1f} "
        f"wave={scores['wave_position']:.1f} liq={scores['liquidity_sweep']:.1f} "
        f"sr={scores['sr_reaction']:.1f} end={scores['wave_ending']:.1f})"
    )

    return total, trigger_tf, scores


def _structure_sl(
    context: PriceContext,
    direction: Direction,
    trigger_tf: str,
    atr: float,
) -> float | None:
    """Return a structure-based stop-loss price, or None if no level found.

    Priority: liquidity sweep level > swing point from trigger TF structure.
    Buffer of 0.3 * ATR is applied beyond the structure level.
    """
    buffer = 0.3 * atr

    # 1. Prefer sweep level — it's the most specific invalidation point
    for tf in [trigger_tf, "15m", "1h"]:
        analysis = context.analyses.get(tf)
        if not analysis:
            continue
        for sweep in analysis.liquidity_sweeps:
            if sweep.confirmed and sweep.direction == direction:
                if direction == Direction.LONG:
                    return round(sweep.swept_level - buffer, 5)
                else:
                    return round(sweep.swept_level + buffer, 5)

    # 2. Fall back to last swing point on trigger TF structure
    trigger_analysis = context.analyses.get(trigger_tf)
    if trigger_analysis:
        if direction == Direction.LONG and trigger_analysis.structure.last_swing_low:
            return round(trigger_analysis.structure.last_swing_low.price - buffer, 5)
        if direction == Direction.SHORT and trigger_analysis.structure.last_swing_high:
            return round(trigger_analysis.structure.last_swing_high.price + buffer, 5)

    return None


def _build_signal(
    context: PriceContext,
    direction: Direction,
    confluence_score: float,
    atr: float,
    entry_tf_name: str = "15m",
    trigger_tf: str = "4h",
    scores: dict | None = None,
    sl_atr_mult: float = SL_ATR_MULTIPLIER,
    tp_rr: float = TP_RISK_REWARD,
    sl_method: str = "atr",
) -> Signal | None:
    """Build a trade signal with entry, SL, and TP."""
    entry_tf = context.analyses.get(entry_tf_name)
    if not entry_tf or atr == 0:
        return None

    current_price = entry_tf.current_price

    if sl_method == "structure":
        stop_loss = _structure_sl(context, direction, trigger_tf, atr)
        if stop_loss is None:
            return None  # No valid structure level — skip signal
        # Enforce minimum stop distance of 0.5 * ATR
        dist = abs(current_price - stop_loss)
        if dist < 0.5 * atr:
            return None
    else:
        if direction == Direction.LONG:
            stop_loss = round(current_price - atr * sl_atr_mult, 5)
        else:
            stop_loss = round(current_price + atr * sl_atr_mult, 5)

    if direction == Direction.LONG:
        risk = current_price - stop_loss
        take_profit = round(current_price + risk * tp_rr, 5)
    else:
        risk = stop_loss - current_price
        take_profit = round(current_price - risk * tp_rr, 5)

    signal_type = _derive_signal_type(scores or {})

    return Signal(
        timestamp=datetime.utcnow(),
        pair=context.pair,
        direction=direction,
        signal_type=signal_type,
        entry_price=current_price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        confluence_score=confluence_score,
        rationale={
            "overall_bias": context.overall_bias.value,
            "bias_strength": context.bias_strength,
            "regime": context.regime.value,
            "scores": scores or {},
            "adx": (context.analyses.get(trigger_tf) or context.analyses.get(entry_tf_name, type("", (), {"adx": 0})())).adx,
            "atr": atr,
        },
        entry_timeframe=entry_tf_name,
        trigger_timeframe=trigger_tf,
    )
