"""Learner Agent - tracks performance and adjusts parameters."""
import json
import logging
from datetime import datetime
from collections import defaultdict

from agents.base import BaseAgent
from data.models import Position, SetupStats, ParameterSet, TradeStatus
from storage.database import PositionRecord, ParameterRecord, get_session
from config.settings import (
    MIN_TRADES_FOR_STATS, OPTIMIZATION_INTERVAL,
    PARAM_BOUNDS, CONFLUENCE_WEIGHTS,
)
from engine.event_bus import bus

logger = logging.getLogger(__name__)


class LearnerAgent(BaseAgent):
    """Tracks what works and adjusts parameters."""

    def __init__(self):
        super().__init__("learner")
        self.trade_count = 0
        self.setup_stats: dict[str, SetupStats] = {}
        self.current_params = ParameterSet(
            confluence_weights=CONFLUENCE_WEIGHTS.copy()
        )

    def process(self, data: dict) -> dict:
        """Process closed trades and potentially adjust parameters.

        Input: {"closed_position": Position}
        Output: {"stats": dict, "params_updated": bool}
        """
        position = data.get("closed_position")
        if not position:
            return {"stats": {}, "params_updated": False}

        self.trade_count += 1
        self._update_stats(position)

        params_updated = False
        if self.trade_count % OPTIMIZATION_INTERVAL == 0:
            params_updated = self._optimize_parameters()

        return {
            "stats": {k: v.model_dump() for k, v in self.setup_stats.items()},
            "params_updated": params_updated,
        }

    def _update_stats(self, position: Position):
        """Update win/loss stats per setup type."""
        setup_type = position.signal.signal_type.value

        if setup_type not in self.setup_stats:
            self.setup_stats[setup_type] = SetupStats(setup_type=setup_type)

        stats = self.setup_stats[setup_type]
        stats.total_trades += 1

        if position.pnl > 0:
            stats.wins += 1
            stats.avg_win_pips = (
                (stats.avg_win_pips * (stats.wins - 1) + position.pnl_pips) / stats.wins
            )
        else:
            stats.losses += 1
            stats.avg_loss_pips = (
                (stats.avg_loss_pips * (stats.losses - 1) + abs(position.pnl_pips)) / stats.losses
            )

        if stats.total_trades > 0:
            stats.win_rate = stats.wins / stats.total_trades

        if stats.avg_loss_pips > 0:
            stats.profit_factor = (
                (stats.avg_win_pips * stats.wins) /
                (stats.avg_loss_pips * stats.losses) if stats.losses > 0 else float("inf")
            )

        stats.expectancy = (
            stats.win_rate * stats.avg_win_pips -
            (1 - stats.win_rate) * stats.avg_loss_pips
        )

        self.logger.info(
            f"Stats [{setup_type}]: {stats.total_trades} trades | "
            f"WR={stats.win_rate:.1%} | E={stats.expectancy:+.1f} pips"
        )

    def _optimize_parameters(self) -> bool:
        """Log current performance stats. Weight adjustment is not yet implemented.

        NOTE: Do NOT implement live weight adjustment here without a holdout set.
        All parameter optimization runs through Optuna (backtest/optimizer.py) on
        historical data. This method is stats-only until OOS validation is in place.
        """
        # Only optimize if we have enough data
        total_trades = sum(s.total_trades for s in self.setup_stats.values())
        if total_trades < MIN_TRADES_FOR_STATS:
            self.logger.info(
                f"Not enough trades for optimization ({total_trades}/{MIN_TRADES_FOR_STATS})"
            )
            return False

        self.logger.info(f"Performance snapshot at trade #{self.trade_count}:")

        best_setups = sorted(
            self.setup_stats.values(),
            key=lambda s: s.expectancy,
            reverse=True,
        )

        # Log current performance
        for stats in best_setups:
            self.logger.info(
                f"  {stats.setup_type}: WR={stats.win_rate:.1%} "
                f"E={stats.expectancy:+.1f} PF={stats.profit_factor:.2f}"
            )

        # Save parameter snapshot
        self._save_params()

        bus.publish("params_updated", self.current_params)
        return True

    def _save_params(self):
        """Save current parameters to database."""
        session = get_session()
        try:
            self.current_params.version += 1
            self.current_params.timestamp = datetime.utcnow()

            rec = ParameterRecord(
                version=self.current_params.version,
                timestamp=self.current_params.timestamp,
                params_json=self.current_params.model_dump_json(),
                performance_score=self.current_params.performance_score,
            )
            session.add(rec)
            session.commit()
            self.logger.info(f"Saved parameter set v{self.current_params.version}")
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to save params: {e}")
        finally:
            session.close()

    def load_stats_from_db(self):
        """Load historical trade stats from database."""
        session = get_session()
        try:
            positions = session.query(PositionRecord).filter_by(
                status="closed"
            ).all()

            for rec in positions:
                setup_type = rec.signal_type or "unknown"
                if setup_type not in self.setup_stats:
                    self.setup_stats[setup_type] = SetupStats(setup_type=setup_type)

                stats = self.setup_stats[setup_type]
                stats.total_trades += 1

                if rec.pnl and rec.pnl > 0:
                    stats.wins += 1

            # Recalculate rates
            for stats in self.setup_stats.values():
                stats.losses = stats.total_trades - stats.wins
                if stats.total_trades > 0:
                    stats.win_rate = stats.wins / stats.total_trades

            self.trade_count = sum(s.total_trades for s in self.setup_stats.values())
            self.logger.info(f"Loaded {self.trade_count} historical trades")
        finally:
            session.close()

    def get_learning_summary(self) -> dict:
        """Get summary of learning progress."""
        return {
            "total_trades_analyzed": self.trade_count,
            "setup_stats": {
                k: v.model_dump() for k, v in self.setup_stats.items()
            },
            "current_params_version": self.current_params.version,
            "next_optimization_at": (
                (self.trade_count // OPTIMIZATION_INTERVAL + 1) * OPTIMIZATION_INTERVAL
            ),
        }
