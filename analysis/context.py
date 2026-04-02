"""Multi-timeframe context builder - aggregates all analysis."""
import logging
from datetime import datetime

import numpy as np

from data.models import Candle, TimeframeAnalysis, PriceContext, Bias
from analysis.market_structure import analyze_structure
from analysis.wave_analysis import detect_waves
from analysis.liquidity import detect_liquidity_sweeps
from analysis.support_resistance import detect_sr_zones
from analysis.fractals import calculate_fractal_alignment
from config.settings import PAIR_NAME, ATR_PERIOD

logger = logging.getLogger(__name__)


def calculate_atr(candles: list[Candle], period: int = ATR_PERIOD) -> float:
    """Calculate Average True Range."""
    if len(candles) < period + 1:
        return 0.0

    true_ranges = []
    for i in range(1, len(candles)):
        tr = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return np.mean(true_ranges) if true_ranges else 0.0

    return np.mean(true_ranges[-period:])


def analyze_timeframe(candles: list[Candle]) -> TimeframeAnalysis:
    """Run full analysis on a single timeframe's candle data."""
    if not candles:
        return TimeframeAnalysis(
            timeframe="unknown",
            structure=__import__("data.models", fromlist=["MarketStructure"]).MarketStructure(timeframe="unknown"),
            wave=__import__("data.models", fromlist=["WaveState"]).WaveState(timeframe="unknown"),
        )

    tf = candles[0].timeframe

    # Market structure
    structure = analyze_structure(candles)

    # Wave analysis
    wave = detect_waves(candles)

    # S/R zones
    sr_zones = detect_sr_zones(candles, structure.swing_points)

    # Liquidity sweeps
    sweeps = detect_liquidity_sweeps(candles, structure.swing_points)

    # ATR
    atr = calculate_atr(candles)

    return TimeframeAnalysis(
        timeframe=tf,
        structure=structure,
        wave=wave,
        sr_zones=sr_zones,
        liquidity_sweeps=sweeps,
        atr=atr,
        current_price=candles[-1].close,
    )


def build_price_context(
    all_candles: dict[str, list[Candle]],
    pair: str = PAIR_NAME,
) -> PriceContext:
    """Build complete multi-timeframe price context.

    This is the central analysis output that feeds into signal generation.
    """
    analyses = {}

    for tf, candles in all_candles.items():
        if candles:
            analysis = analyze_timeframe(candles)
            analyses[tf] = analysis
            logger.info(
                f"  {tf:>4s}: bias={analysis.structure.bias.value:>8s} | "
                f"wave={analysis.wave.phase.value:>16s} | "
                f"ATR={analysis.atr:.5f} | "
                f"break={analysis.structure.last_break.value}"
            )

    # Calculate fractal alignment
    overall_bias, bias_strength = calculate_fractal_alignment(analyses)

    context = PriceContext(
        pair=pair,
        timestamp=datetime.utcnow(),
        analyses=analyses,
        overall_bias=overall_bias,
        bias_strength=bias_strength,
    )

    logger.info(
        f"Overall: bias={overall_bias.value}, strength={bias_strength:.2f}"
    )

    return context
