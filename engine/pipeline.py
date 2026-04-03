"""Main trading pipeline - orchestrates the full agent loop."""
import logging
import time
from datetime import datetime

from agents.market_analyzer import MarketAnalyzerAgent
from agents.signal_generator import SignalGeneratorAgent
from agents.risk_manager import RiskManagerAgent
from agents.executor import ExecutorAgent
from agents.learner import LearnerAgent
from data.ingestion import fetch_candles, load_candles, save_candles, get_live_price
from data.models import Candle
from engine.event_bus import bus
from config.settings import TIMEFRAMES, TIMEFRAME_MINUTES, NO_OVERLAP_ENTRIES, BLOCK_HOURS_UTC
from config.assets import ACTIVE_ASSETS, get_asset, resolve_pair_name

logger = logging.getLogger(__name__)


class TradingPipeline:
    """Main orchestration loop for the trading system."""

    def __init__(self, pairs: list[str] | None = None):
        self.pairs = pairs or list(ACTIVE_ASSETS)
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
        """Initialize all agents and load optimized parameters."""
        for agent in [self.analyzer, self.signal_gen, self.risk_mgr,
                      self.executor, self.learner]:
            agent.setup()
        self.learner.load_stats_from_db()

        # Load optimized parameters
        try:
            from backtest.optimizer import apply_optimized_params
            if apply_optimized_params():
                # Update signal generator weights
                from config.settings import CONFLUENCE_WEIGHTS
                self.signal_gen.update_weights(CONFLUENCE_WEIGHTS)
        except Exception as e:
            logger.warning(f"Could not load optimized params: {e}")

        # Sync capital with OANDA account
        try:
            from data.oanda import get_account_summary
            acct = get_account_summary()
            self.risk_mgr.capital = float(acct["balance"])
            logger.info(f"OANDA balance: £{self.risk_mgr.capital:,.2f}")
        except Exception as e:
            logger.warning(f"Could not sync OANDA balance: {e}")

        logger.info("Trading pipeline initialized")

    def run_once(self) -> dict:
        """Run a single cycle of the pipeline for all active pairs.

        Returns summary of what happened.
        """
        summary = {
            "timestamp": datetime.utcnow().isoformat(),
            "signals": 0,
            "positions_opened": 0,
            "positions_closed": 0,
        }

        for pair_name in self.pairs:
            asset = get_asset(pair_name)
            pair_summary = self._run_pair(asset)
            summary["signals"] += pair_summary["signals"]
            summary["positions_opened"] += pair_summary["positions_opened"]

        return summary

    def _run_pair(self, asset) -> dict:
        """Run analysis and trading for a single pair."""
        pair_summary = {"signals": 0, "positions_opened": 0}
        pair = asset.yahoo_ticker

        # 1. Fetch/load data
        candles = self._get_candles(pair)
        if not candles:
            logger.warning(f"No candle data available for {asset.name}")
            return pair_summary

        # 2. Analyze
        analysis_result = self.analyzer.process({"candles": candles})
        context = analysis_result.get("context")
        if not context:
            return pair_summary

        # 3. Check existing positions against current live price
        live = get_live_price(pair=pair)
        current_price = live.get("mid", 0) if live else 0

        if current_price == 0:
            for tf in ["15m", "1h", "4h", "1d"]:
                if tf in context.analyses:
                    current_price = context.analyses[tf].current_price
                    break

        if current_price > 0:
            self.risk_mgr.update_positions(current_price, pair=pair)

        # 4. Generate signals
        signal_result = self.signal_gen.process({"context": context})
        signals = signal_result.get("signals", [])
        pair_summary["signals"] = len(signals)

        # 4b. Apply trading rules
        signals = self._filter_signals(signals, current_price)
        pair_summary["signals"] = len(signals)

        # 5. Risk check and size
        if signals:
            risk_result = self.risk_mgr.process({"signals": signals})
            positions = risk_result.get("positions", [])
            pair_summary["positions_opened"] = len(positions)

            # 6. Execute
            if positions:
                self.executor.process({"positions": positions})

        return pair_summary

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

    def _filter_signals(self, signals: list, current_price: float) -> list:
        """Apply evidence-based trading rules before risk processing."""
        if not signals:
            return signals

        now = datetime.utcnow()

        # Block signals during low-quality hours
        if BLOCK_HOURS_UTC and now.hour in BLOCK_HOURS_UTC:
            logger.info(f"Skipping {len(signals)} signal(s) — blocked hour {now.hour:02d}:00 UTC")
            return []

        filtered = []
        for signal in signals:
            # No-overlap: skip if a position in the same direction is already open
            if NO_OVERLAP_ENTRIES:
                same_dir_open = any(
                    p.signal.direction == signal.direction
                    and p.signal.pair == signal.pair
                    for p in self.risk_mgr.open_positions
                )
                if same_dir_open:
                    logger.debug(f"Skipping {signal.direction.value} signal — position already open in same direction")
                    continue
            filtered.append(signal)

        skipped = len(signals) - len(filtered)
        if skipped:
            logger.info(f"Trading rules filtered {skipped}/{len(signals)} signal(s)")
        return filtered

    def _get_candles(self, pair: str) -> dict[str, list[Candle]]:
        """Get candles for all timeframes for a specific pair."""
        all_candles = {}
        now = datetime.utcnow()

        for tf in TIMEFRAMES:
            # Check if we need to re-fetch (keyed by pair+tf)
            fetch_key = f"{pair}:{tf}"
            last = self._last_fetch.get(fetch_key)
            interval_min = TIMEFRAME_MINUTES.get(tf, 60)

            needs_fetch = (
                last is None or
                (now - last).total_seconds() > interval_min * 60
            )

            if needs_fetch:
                try:
                    candles = fetch_candles(pair=pair, timeframe=tf)
                    if candles:
                        save_candles(candles, pair)
                        all_candles[tf] = candles
                        self._last_fetch[fetch_key] = now
                    else:
                        all_candles[tf] = load_candles(pair=pair, timeframe=tf)
                except Exception as e:
                    logger.warning(f"Fetch failed for {pair} {tf}: {e}, using cache")
                    all_candles[tf] = load_candles(pair=pair, timeframe=tf)
            else:
                all_candles[tf] = load_candles(pair=pair, timeframe=tf)

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
