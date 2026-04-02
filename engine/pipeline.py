"""Main trading pipeline - orchestrates the full agent loop."""
import logging
import time
from datetime import datetime

from agents.market_analyzer import MarketAnalyzerAgent
from agents.signal_generator import SignalGeneratorAgent
from agents.risk_manager import RiskManagerAgent
from agents.executor import ExecutorAgent
from agents.learner import LearnerAgent
from data.ingestion import fetch_candles, load_candles, save_candles
from data.models import Candle
from engine.event_bus import bus
from config.settings import PAIR, TIMEFRAMES, TIMEFRAME_MINUTES

logger = logging.getLogger(__name__)


class TradingPipeline:
    """Main orchestration loop for the trading system."""

    def __init__(self):
        self.analyzer = MarketAnalyzerAgent()
        self.signal_gen = SignalGeneratorAgent()
        self.risk_mgr = RiskManagerAgent()
        self.executor = ExecutorAgent()
        self.learner = LearnerAgent()

        self._running = False
        self._last_fetch: dict[str, datetime] = {}

        # Wire up events
        bus.subscribe("position_closed", self._on_position_closed)

    def setup(self):
        """Initialize all agents."""
        for agent in [self.analyzer, self.signal_gen, self.risk_mgr,
                      self.executor, self.learner]:
            agent.setup()
        self.learner.load_stats_from_db()
        logger.info("Trading pipeline initialized")

    def run_once(self) -> dict:
        """Run a single cycle of the pipeline.

        Returns summary of what happened.
        """
        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "signals": 0,
            "positions_opened": 0,
            "positions_closed": 0,
        }

        # 1. Fetch/load data
        candles = self._get_candles()
        if not candles:
            logger.warning("No candle data available")
            return summary

        # 2. Analyze
        analysis_result = self.analyzer.process({"candles": candles})
        context = analysis_result.get("context")
        if not context:
            return summary

        # 3. Check existing positions against current price
        current_price = 0
        for tf in ["15m", "1h", "4h", "1d"]:
            if tf in context.analyses:
                current_price = context.analyses[tf].current_price
                break

        if current_price > 0:
            self.risk_mgr.update_positions(current_price)

        # 4. Generate signals
        signal_result = self.signal_gen.process({"context": context})
        signals = signal_result.get("signals", [])
        summary["signals"] = len(signals)

        # 5. Risk check and size
        if signals:
            risk_result = self.risk_mgr.process({"signals": signals})
            positions = risk_result.get("positions", [])
            summary["positions_opened"] = len(positions)

            # 6. Execute
            if positions:
                self.executor.process({"positions": positions})

        return summary

    def run_loop(self, interval_seconds: int = 60):
        """Run the pipeline in a loop."""
        self._running = True
        logger.info(f"Starting trading loop (interval={interval_seconds}s)")
        logger.info(f"Capital: £{self.risk_mgr.capital:.2f}")

        try:
            while self._running:
                try:
                    summary = self.run_once()
                    logger.info(
                        f"Cycle complete: {summary['signals']} signals, "
                        f"{summary['positions_opened']} opened | "
                        f"Capital: £{self.risk_mgr.capital:.2f}"
                    )
                except Exception as e:
                    logger.error(f"Pipeline error: {e}", exc_info=True)

                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("Pipeline stopped by user")
        finally:
            self._running = False
            self.teardown()

    def _get_candles(self) -> dict[str, list[Candle]]:
        """Get candles for all timeframes, fetching new data as needed."""
        all_candles = {}
        now = datetime.utcnow()

        for tf in TIMEFRAMES:
            # Check if we need to re-fetch
            last = self._last_fetch.get(tf)
            interval_min = TIMEFRAME_MINUTES.get(tf, 60)

            needs_fetch = (
                last is None or
                (now - last).total_seconds() > interval_min * 60
            )

            if needs_fetch:
                try:
                    candles = fetch_candles(pair=PAIR, timeframe=tf)
                    if candles:
                        save_candles(candles, PAIR)
                        all_candles[tf] = candles
                        self._last_fetch[tf] = now
                    else:
                        # Fall back to cached
                        all_candles[tf] = load_candles(pair=PAIR, timeframe=tf)
                except Exception as e:
                    logger.warning(f"Fetch failed for {tf}: {e}, using cache")
                    all_candles[tf] = load_candles(pair=PAIR, timeframe=tf)
            else:
                all_candles[tf] = load_candles(pair=PAIR, timeframe=tf)

        return all_candles

    def _on_position_closed(self, position):
        """Handle closed position - feed to learner."""
        self.learner.process({"closed_position": position})
        self.executor.record_close(position)

    def teardown(self):
        """Shutdown all agents."""
        for agent in [self.analyzer, self.signal_gen, self.risk_mgr,
                      self.executor, self.learner]:
            agent.teardown()
        logger.info("Trading pipeline shut down")

    def get_status(self) -> dict:
        """Get current pipeline status."""
        return {
            "running": self._running,
            "risk": self.risk_mgr.get_stats(),
            "learning": self.learner.get_learning_summary(),
            "last_context": (
                self.analyzer.last_context.model_dump()
                if self.analyzer.last_context else None
            ),
            "active_signals": [
                s.model_dump() for s in self.signal_gen.active_signals
            ],
        }
