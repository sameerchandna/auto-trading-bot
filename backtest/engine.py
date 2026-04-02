"""Backtesting engine - replay historical data through the pipeline."""
import logging
from datetime import datetime, timedelta

from data.models import Candle, Position, Direction, TradeStatus
from data.ingestion import fetch_candles, save_candles, load_candles
from analysis.context import build_price_context
from analysis.confluence import score_confluence
from agents.risk_manager import RiskManagerAgent, PIP_VALUE
from backtest.metrics import calculate_metrics
from config.settings import PAIR, TIMEFRAMES, CONFLUENCE_WEIGHTS

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Replays historical data through the analysis pipeline."""

    def __init__(
        self,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10_000,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.risk_mgr = RiskManagerAgent()
        self.risk_mgr.capital = initial_capital

        self.equity_curve: list[tuple[datetime, float]] = []
        self.all_trades: list[Position] = []

    def run(self, weights: dict | None = None) -> dict:
        """Run the backtest.

        Returns dict with metrics and trade history.
        """
        if weights is None:
            weights = CONFLUENCE_WEIGHTS

        logger.info(
            f"Backtest: {self.start_date.date()} to {self.end_date.date()} | "
            f"Capital: £{self.initial_capital}"
        )

        # Fetch historical data for all timeframes
        all_candles = {}
        for tf in TIMEFRAMES:
            candles = fetch_candles(
                pair=PAIR, timeframe=tf,
                start=self.start_date, end=self.end_date,
            )
            if candles:
                save_candles(candles, PAIR)
                all_candles[tf] = candles
                logger.info(f"  {tf}: {len(candles)} candles loaded")

        # Use the lowest available timeframe for iteration (prefer 1h > 4h > 1d)
        iter_tf = None
        for tf in ["1h", "4h", "1d"]:
            if tf in all_candles and all_candles[tf]:
                iter_tf = tf
                break

        if iter_tf is None:
            logger.error("No candle data available for backtest")
            return {"error": "No data"}

        iter_candles = all_candles[iter_tf]
        logger.info(f"Iterating on {iter_tf} ({len(iter_candles)} candles)")

        # Walk forward through each candle
        step = max(1, len(iter_candles) // 500)  # Sample ~500 points for speed
        for i in range(50, len(iter_candles), step):
            current_date = iter_candles[i].timestamp
            current_price = iter_candles[i].close

            # Build candle windows for each timeframe
            candle_windows = {}
            for tf, candles in all_candles.items():
                window = [c for c in candles if c.timestamp <= current_date]
                if window:
                    candle_windows[tf] = window[-200:]  # Keep last 200

            if not candle_windows:
                continue

            # Run analysis
            try:
                context = build_price_context(candle_windows)
            except Exception as e:
                logger.debug(f"Analysis error at {current_date}: {e}")
                continue

            # Update existing positions
            self.risk_mgr.update_positions(current_price)

            # Generate signals
            signals = score_confluence(context, weights)

            # Risk manage and "execute"
            for signal in signals:
                result = self.risk_mgr.process({"signals": [signal]})
                positions = result.get("positions", [])
                self.all_trades.extend(positions)

            # Record equity
            unrealized = 0
            for pos in self.risk_mgr.open_positions:
                if pos.signal.direction == Direction.LONG:
                    unrealized += (current_price - pos.entry_price) / PIP_VALUE * (pos.size / 100_000) * 10
                else:
                    unrealized += (pos.entry_price - current_price) / PIP_VALUE * (pos.size / 100_000) * 10

            self.equity_curve.append((
                current_date,
                self.risk_mgr.capital + unrealized,
            ))

        # Collect results
        closed = self.risk_mgr.closed_positions
        self.all_trades.extend(closed)

        metrics = calculate_metrics(
            closed,
            self.initial_capital,
            self.equity_curve,
        )

        logger.info("=== Backtest Results ===")
        logger.info(f"  Total trades: {metrics['total_trades']}")
        logger.info(f"  Win rate: {metrics['win_rate']:.1%}")
        logger.info(f"  Profit factor: {metrics['profit_factor']:.2f}")
        logger.info(f"  Total P&L: £{metrics['total_pnl']:.2f}")
        logger.info(f"  Max drawdown: {metrics['max_drawdown_pct']:.1%}")
        logger.info(f"  Sharpe ratio: {metrics['sharpe_ratio']:.2f}")
        logger.info(f"  Final capital: £{self.risk_mgr.capital:.2f}")

        return {
            "metrics": metrics,
            "equity_curve": [(t.isoformat(), v) for t, v in self.equity_curve],
            "trades": len(closed),
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
        }
