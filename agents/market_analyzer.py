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
        context = build_price_context(candles)
        self.last_context = context

        bus.publish("analysis_complete", context)

        return {"context": context}
