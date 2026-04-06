"""Backtesting engine - replay historical data through the pipeline."""
import logging
from bisect import bisect_right
from datetime import datetime, timedelta

from data.models import Candle, Position, Direction, TradeStatus
from data.ingestion import fetch_candles, save_candles, load_candles
from analysis.context import build_price_context
from analysis.confluence import score_confluence
from agents.risk_manager import RiskManagerAgent
from backtest.metrics import calculate_metrics
from backtest.config import BacktestConfig, BASELINE
from config.settings import TIMEFRAMES
from config.assets import get_asset
from config.params import load_strategy_params

logger = logging.getLogger(__name__)


def _bisect_candles(candles: list, target_date: datetime) -> int:
    """Find index of last candle with timestamp <= target_date."""
    timestamps = [c.timestamp for c in candles]
    return bisect_right(timestamps, target_date)


class BacktestEngine:
    """Replays historical data through the analysis pipeline."""

    def __init__(
        self,
        start_date: datetime,
        end_date: datetime,
        initial_capital: float = 10_000,
        config: BacktestConfig | None = None,
        pair: str = "EURUSD",
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.initial_capital = initial_capital
        self.config = config or BASELINE
        self.pair = pair
        self.asset = get_asset(pair)
        self.params = load_strategy_params()
        self.risk_mgr = RiskManagerAgent()
        self.risk_mgr.capital = initial_capital

        self.equity_curve: list[tuple[datetime, float]] = []
        self.all_trades: list[Position] = []
        self._consecutive_losses: int = 0
        self._cooldown_remaining: int = 0

    def run(self, weights: dict | None = None) -> dict:
        """Run the backtest.

        Returns dict with metrics and trade history.
        """
        if weights is None:
            weights = self.params["weights"]

        logger.info(
            f"Backtest: {self.start_date.date()} to {self.end_date.date()} | "
            f"Capital: £{self.initial_capital}"
        )

        # Load historical data from cache first, fetch only if missing
        active_tfs = [tf for tf in TIMEFRAMES if tf not in self.config.exclude_timeframes]
        all_candles = {}
        for tf in active_tfs:
            candles = load_candles(
                pair=self.asset.yahoo_ticker, timeframe=tf, limit=10000,
                start=self.start_date, end=self.end_date,
            )
            if not candles:
                try:
                    candles = fetch_candles(
                        pair=self.asset.yahoo_ticker, timeframe=tf,
                        start=self.start_date, end=self.end_date,
                    )
                    if candles:
                        save_candles(candles, self.asset.yahoo_ticker)
                except Exception as e:
                    logger.debug(f"Fetch failed for {tf}: {e}")
            if candles:
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

        # Pre-sort all candles by timestamp for faster windowing
        sorted_candles = {}
        for tf, candles in all_candles.items():
            sorted_candles[tf] = sorted(candles, key=lambda c: c.timestamp)

        # Walk forward through every candle (must match PineScript 1H evaluation)
        step = 1
        last_date = None
        for i in range(50, len(iter_candles), step):
            current_date = iter_candles[i].timestamp
            current_price = iter_candles[i].close

            # Set simulated time so positions get correct timestamps
            self.risk_mgr._simulated_time = current_date

            # Reset daily P&L on new day
            if last_date is None or current_date.date() != last_date.date():
                self.risk_mgr._reset_daily_weekly()
                self.risk_mgr.last_daily_reset = current_date.date()
            last_date = current_date

            # Build candle windows for each timeframe using binary search
            candle_windows = {}
            for tf, candles in sorted_candles.items():
                idx = _bisect_candles(candles, current_date)
                if idx > 0:
                    window = candles[max(0, idx - 200):idx]
                    if window:
                        candle_windows[tf] = window

            if not candle_windows:
                continue

            # Run analysis
            try:
                context = build_price_context(candle_windows, pair=self.pair)
            except Exception as e:
                logger.debug(f"Analysis error at {current_date}: {e}")
                continue

            # Update existing positions
            self.risk_mgr.update_positions(current_price)

            # Track consecutive losses for cooldown
            if not hasattr(self, '_tracked_closed_count'):
                self._tracked_closed_count = 0
            prev_count = self._tracked_closed_count
            current_closed = self.risk_mgr.closed_positions
            for p in current_closed[prev_count:]:
                if p.pnl < 0:
                    self._consecutive_losses += 1
                    if self.config.cooldown_after_losses and self._consecutive_losses >= self.config.cooldown_after_losses:
                        self._cooldown_remaining = self.config.cooldown_after_losses
                else:
                    self._consecutive_losses = 0
            self._tracked_closed_count = len(current_closed)

            # Generate signals
            signals = score_confluence(
                context, weights,
                threshold=self.params["threshold"],
                sl_atr_mult=self.params["sl_multiplier"],
                tp_rr=self.params["tp_risk_reward"],
                sl_method=self.config.sl_method,
            )

            # Apply config filters before execution
            signals = self._apply_filters(signals, current_date)

            # Risk manage and "execute"
            for signal in signals:
                result = self.risk_mgr.process({"signals": [signal]})
                positions = result.get("positions", [])
                self.all_trades.extend(positions)

            # Record equity (price_change * size, matching close_position() formula)
            unrealized = 0
            for pos in self.risk_mgr.open_positions:
                if pos.signal.direction == Direction.LONG:
                    unrealized += (current_price - pos.entry_price) * pos.size
                else:
                    unrealized += (pos.entry_price - current_price) * pos.size

            self.equity_curve.append((
                current_date,
                self.risk_mgr.capital + unrealized,
            ))

        # Collect results
        closed = self.risk_mgr.closed_positions
        self.all_trades.extend(closed)
        self.risk_mgr._simulated_time = None

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
            "pair": self.pair,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "config_label": self.config.label(),
        }

    def _apply_filters(self, signals: list, current_date: datetime) -> list:
        """Apply BacktestConfig rules to filter signals before execution."""
        cfg = self.config
        if not signals:
            return signals

        # Cooldown: skip signals after too many consecutive losses
        if cfg.cooldown_after_losses and self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            logger.debug(f"Cooldown active ({self._cooldown_remaining} remaining) — skipping {len(signals)} signal(s)")
            return []

        # Time filters
        if cfg.block_hours and current_date.hour in cfg.block_hours:
            return []
        if cfg.block_days and current_date.weekday() in cfg.block_days:
            return []

        filtered = []
        for signal in signals:
            # Confluence score threshold
            if signal.confluence_score < cfg.min_score:
                continue

            # No-overlap: block same-direction entry if already in a position
            if cfg.no_overlap:
                same_dir_open = any(
                    p.signal.direction == signal.direction
                    for p in self.risk_mgr.open_positions
                )
                if same_dir_open:
                    continue

            filtered.append(signal)

        return filtered
