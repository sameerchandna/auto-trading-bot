"""Market Analyzer Agent - orchestrates all analysis modules."""
import logging

from agents.base import BaseAgent
from data.models import PriceContext, Candle
from analysis.context import build_price_context
from engine.event_bus import bus

logger = logging.getLogger(__name__)


class MarketAnalyzerAgent(BaseAgent):
    """Runs multi-timeframe analysis and produces PriceContext."""

    def __init__(self):
        super().__init__("market_analyzer")
        self.last_context: PriceContext | None = None
        self.adx_threshold: float = 25.0
        self.atr_volatility_threshold: float = 80.0

    def process(self, data: dict) -> dict:
        """Analyze candles across all timeframes.

        Input: {"candles": {timeframe: [Candle, ...]}}
        Output: {"context": PriceContext}
        """
        candles = data.get("candles", {})
        if not candles:
            self.logger.warning("No candle data provided")
            return {"context": None}

        self.logger.info("Running multi-timeframe analysis...")
        context = build_price_context(
            candles,
            adx_threshold=self.adx_threshold,
            atr_volatility_threshold=self.atr_volatility_threshold,
        )
        self.last_context = context

        bus.publish("analysis_complete", context)

        from storage.database import log_audit
        log_audit("market_analyzer", "analysis_complete", pair=context.pair, details={
            "regime": context.regime.value if context.regime else None,
            "bias": context.overall_bias.value,
            "bias_strength": round(context.bias_strength, 3),
        })

        return {"context": context}
