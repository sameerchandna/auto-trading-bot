"""Multi-timeframe fractal alignment analysis."""
import logging

from data.models import Bias, MarketStructure, TimeframeAnalysis
from config.settings import TIMEFRAME_WEIGHT

logger = logging.getLogger(__name__)


def calculate_fractal_alignment(
    analyses: dict[str, TimeframeAnalysis],
) -> tuple[Bias, float]:
    """Calculate how aligned the timeframes are.

    Returns (overall_bias, alignment_strength 0-1).

    Higher timeframes carry more weight. Perfect alignment = 1.0.
    """
    if not analyses:
        return Bias.RANGING, 0.0

    bullish_weight = 0.0
    bearish_weight = 0.0
    total_weight = 0.0

    for tf, analysis in analyses.items():
        weight = TIMEFRAME_WEIGHT.get(tf, 1)
        total_weight += weight

        if analysis.structure.bias == Bias.BULLISH:
            bullish_weight += weight
        elif analysis.structure.bias == Bias.BEARISH:
            bearish_weight += weight

    if total_weight == 0:
        return Bias.RANGING, 0.0

    bull_ratio = bullish_weight / total_weight
    bear_ratio = bearish_weight / total_weight

    if bull_ratio > bear_ratio and bull_ratio > 0.4:
        return Bias.BULLISH, bull_ratio
    elif bear_ratio > bull_ratio and bear_ratio > 0.4:
        return Bias.BEARISH, bear_ratio
    else:
        return Bias.RANGING, max(bull_ratio, bear_ratio)


def check_htf_ltf_alignment(
    htf_bias: Bias,
    ltf_structure: MarketStructure,
) -> bool:
    """Check if lower timeframe structure aligns with higher timeframe bias.

    For a valid setup: HTF sets direction, LTF provides entry.
    """
    if htf_bias == Bias.RANGING:
        return False

    if htf_bias == Bias.BULLISH:
        # LTF should show bullish BOS or bullish bias
        return (
            ltf_structure.bias == Bias.BULLISH or
            (ltf_structure.last_break == "bos" and
             ltf_structure.last_swing_high is not None)
        )
    elif htf_bias == Bias.BEARISH:
        return (
            ltf_structure.bias == Bias.BEARISH or
            (ltf_structure.last_break == "bos" and
             ltf_structure.last_swing_low is not None)
        )
    return False
