"""Signal Generator Agent - produces trade signals from analysis."""
import logging
from datetime import datetime

from agents.base import BaseAgent
from data.models import PriceContext, Signal
from analysis.confluence import score_confluence
from engine.event_bus import bus
from config.settings import CONFLUENCE_WEIGHTS

logger = logging.getLogger(__name__)


class SignalGeneratorAgent(BaseAgent):
    """Generates BUY/SELL signals from PriceContext using confluence scoring."""

    def __init__(self):
        super().__init__("signal_generator")
        self.active_signals: list[Signal] = []
        self.signal_history: list[Signal] = []
        self.weights = CONFLUENCE_WEIGHTS.copy()

    def process(self, data: dict) -> dict:
        """Generate signals from price context.

        Input: {"context": PriceContext}
        Output: {"signals": [Signal, ...]}
        """
        context: PriceContext | None = data.get("context")
        if context is None:
            return {"signals": []}

        signals = score_confluence(context, self.weights)

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

        return {"signals": signals}

    def update_weights(self, new_weights: dict[str, float]):
        """Update confluence weights from the learning system."""
        self.weights.update(new_weights)
        self.logger.info(f"Updated confluence weights: {self.weights}")
