"""Multi-timeframe context builder - aggregates all analysis."""
import logging
from datetime import datetime

import numpy as np

from data.models import Candle, TimeframeAnalysis, PriceContext, Bias, Regime
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


def calculate_adx(candles: list[Candle], period: int = ATR_PERIOD) -> float:
    """Calculate Average Directional Index (ADX).

    Measures trend strength regardless of direction.
    ADX > 25 = trending, ADX < 20 = ranging (Wilder's classic thresholds).
    """
    if len(candles) < period * 2 + 1:
        return 0.0

    plus_dm_list = []
    minus_dm_list = []
    tr_list = []

    for i in range(1, len(candles)):
        high_diff = candles[i].high - candles[i - 1].high
        low_diff = candles[i - 1].low - candles[i].low

        plus_dm = high_diff if high_diff > low_diff and high_diff > 0 else 0.0
        minus_dm = low_diff if low_diff > high_diff and low_diff > 0 else 0.0

        tr = max(
            candles[i].high - candles[i].low,
            abs(candles[i].high - candles[i - 1].close),
            abs(candles[i].low - candles[i - 1].close),
        )
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    if len(tr_list) < period * 2:
        return 0.0

    # Wilder's smoothing (exponential with alpha = 1/period)
    smoothed_plus_dm = float(np.mean(plus_dm_list[:period]))
    smoothed_minus_dm = float(np.mean(minus_dm_list[:period]))
    smoothed_tr = float(np.mean(tr_list[:period]))

    dx_values = []
    for i in range(period, len(tr_list)):
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]

        if smoothed_tr == 0:
            continue

        plus_di = 100 * smoothed_plus_dm / smoothed_tr
        minus_di = 100 * smoothed_minus_dm / smoothed_tr
        di_sum = plus_di + minus_di
        if di_sum == 0:
            continue

        dx = 100 * abs(plus_di - minus_di) / di_sum
        dx_values.append(dx)

    if len(dx_values) < period:
        return float(np.mean(dx_values)) if dx_values else 0.0

    # Smooth DX into ADX using Wilder's method
    adx = float(np.mean(dx_values[:period]))
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period

    return round(adx, 2)


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

    # ATR & ADX
    atr = calculate_atr(candles)
    adx = calculate_adx(candles)

    return TimeframeAnalysis(
        timeframe=tf,
        structure=structure,
        wave=wave,
        sr_zones=sr_zones,
        liquidity_sweeps=sweeps,
        atr=atr,
        adx=adx,
        current_price=candles[-1].close,
    )


def calculate_atr_percentile(candles: list[Candle], period: int = ATR_PERIOD, lookback: int = 100) -> float:
    """Calculate where current ATR sits relative to recent ATR history (0-100).

    Returns the percentile rank of the latest ATR within its rolling window.
    High percentile (e.g. >80) = unusually volatile conditions.
    """
    if len(candles) < period + lookback:
        return 50.0  # neutral default when insufficient data

    # Compute ATR for each bar in the lookback window
    atr_values = []
    for end in range(period + 1, min(len(candles) + 1, period + lookback + 1)):
        window = candles[end - period - 1:end]
        atr_val = calculate_atr(window, period)
        if atr_val > 0:
            atr_values.append(atr_val)

    if len(atr_values) < 10:
        return 50.0

    current_atr = atr_values[-1]
    rank = sum(1 for v in atr_values if v <= current_atr)
    return round(100.0 * rank / len(atr_values), 1)


def classify_regime(
    analyses: dict[str, TimeframeAnalysis],
    adx_threshold: float = 25.0,
    atr_percentile: float | None = None,
    atr_volatility_threshold: float = 80.0,
) -> Regime:
    """Classify market regime from ADX + ATR percentile.

    Three regimes:
      VOLATILE  — ATR percentile above atr_volatility_threshold (very high volatility)
      TRENDING  — ADX >= adx_threshold (strong directional movement)
      RANGING   — ADX < adx_threshold and not volatile (low-energy, sideways)

    VOLATILE takes priority over TRENDING/RANGING — a strongly trending AND
    volatile market should use volatile params (wider SL to survive whipsaws).
    """
    # Volatile check first (if ATR percentile is provided)
    if atr_percentile is not None and atr_percentile >= atr_volatility_threshold:
        return Regime.VOLATILE

    for tf in ("4h", "1d", "1h"):
        analysis = analyses.get(tf)
        if analysis and analysis.adx > 0:
            return Regime.TRENDING if analysis.adx >= adx_threshold else Regime.RANGING
    return Regime.TRENDING  # safe default — don't filter when data is missing


def build_price_context(
    all_candles: dict[str, list[Candle]],
    pair: str = PAIR_NAME,
    adx_threshold: float = 25.0,
    atr_volatility_threshold: float = 80.0,
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
                f"ADX={analysis.adx:5.1f} | "
                f"break={analysis.structure.last_break.value}"
            )

    # Calculate fractal alignment
    overall_bias, bias_strength = calculate_fractal_alignment(analyses)

    # ATR percentile for volatile detection (prefer 4H, fallback 1D -> 1H)
    atr_percentile = None
    for tf in ("4h", "1d", "1h"):
        tf_candles = all_candles.get(tf)
        if tf_candles and len(tf_candles) > 30:
            atr_percentile = calculate_atr_percentile(tf_candles)
            break

    # Classify market regime
    regime = classify_regime(
        analyses, adx_threshold,
        atr_percentile=atr_percentile,
        atr_volatility_threshold=atr_volatility_threshold,
    )

    context = PriceContext(
        pair=pair,
        timestamp=datetime.utcnow(),
        analyses=analyses,
        overall_bias=overall_bias,
        bias_strength=bias_strength,
        regime=regime,
    )

    logger.info(
        f"Overall: bias={overall_bias.value}, strength={bias_strength:.2f}, "
        f"regime={regime.value}"
        + (f", atr_pctl={atr_percentile:.0f}" if atr_percentile is not None else "")
    )

    return context
