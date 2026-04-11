"""Signal Generator Agent - produces trade signals from analysis."""
import logging
from datetime import datetime

from agents.base import BaseAgent
from data.models import PriceContext, Signal
from analysis.confluence import score_confluence
from engine.event_bus import bus
from config.settings import CONFLUENCE_WEIGHTS
from config.params import load_strategy_params

logger = logging.getLogger(__name__)


class SignalGeneratorAgent(BaseAgent):
    """Generates BUY/SELL signals from PriceContext using confluence scoring."""

    def __init__(self):
        super().__init__("signal_generator")
        self.active_signals: list[Signal] = []
        self.signal_history: list[Signal] = []
        self.weights = CONFLUENCE_WEIGHTS.copy()
        self._regime_filter_enabled: bool = False
        self._signal_model_enabled: bool = False
        self._signal_model_min_confidence: float = 0.5
        self._regime_params_enabled: bool = False
        self._regime_params: dict[str, dict] = {}
        self._news_filter_enabled: bool = False
        self._news_block_before_mins: int = 30
        self._news_block_after_mins: int = 15

    def process(self, data: dict) -> dict:
        """Generate signals from price context.

        Input: {"context": PriceContext}
        Output: {"signals": [Signal, ...]}
        """
        context: PriceContext | None = data.get("context")
        if context is None:
            return {"signals": []}

        signals = score_confluence(
            context, self.weights,
            regime_filter_enabled=self._regime_filter_enabled,
            regime_params_enabled=self._regime_params_enabled,
            regime_params=self._regime_params,
            signal_model_enabled=self._signal_model_enabled,
            signal_model_min_confidence=self._signal_model_min_confidence,
            news_filter_enabled=self._news_filter_enabled,
            news_block_before_mins=self._news_block_before_mins,
            news_block_after_mins=self._news_block_after_mins,
        )

        for signal in signals:
            self.logger.info(
                f"SIGNAL: {signal.direction.value} {signal.pair} | "
                f"score={signal.confluence_score:.2f} | "
                f"entry={signal.entry_price:.5f} | "
                f"SL={signal.stop_loss:.5f} | TP={signal.take_profit:.5f}"
            )
            self.signal_history.append(signal)

        self.active_signals = signals
        bus.publish("signals_generated", signals)

        if signals:
            from storage.database import log_audit
            log_audit("signal_generator", "signals_generated", pair=context.pair, details={
                "count": len(signals),
                "directions": [s.direction.value for s in signals],
                "scores": [round(s.confluence_score, 3) for s in signals],
                "types": [s.signal_type.value for s in signals],
            })

        return {"signals": signals}

    def update_weights(self, new_weights: dict[str, float]):
        """Update confluence weights from the learning system."""
        self.weights.update(new_weights)
        self.logger.info(f"Updated confluence weights: {self.weights}")
