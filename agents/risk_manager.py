"""Risk Manager Agent - position sizing and risk validation."""
import logging
from datetime import datetime, timedelta

from agents.base import BaseAgent
from data.models import Signal, Position, Direction, TradeStatus
from engine.event_bus import bus
from config.settings import (
    STARTING_CAPITAL, MAX_RISK_PER_TRADE, MAX_CONCURRENT_POSITIONS,
    MAX_PORTFOLIO_RISK, DAILY_LOSS_LIMIT, WEEKLY_LOSS_LIMIT,
)
from config.assets import get_asset, resolve_pair_name

logger = logging.getLogger(__name__)


class RiskManagerAgent(BaseAgent):
    """Validates signals against risk rules and sizes positions."""

    def __init__(self):
        super().__init__("risk_manager")
        self.capital = STARTING_CAPITAL
        self.open_positions: list[Position] = []
        self.closed_positions: list[Position] = []
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.last_daily_reset = datetime.utcnow().date()
        self.last_weekly_reset = datetime.utcnow().date()
        self.reduced_size = False  # True after weekly loss limit hit
        self._simulated_time: datetime | None = None  # Set by backtest engine

    @property
    def now(self) -> datetime:
        """Current time — simulated during backtest, real otherwise."""
        return self._simulated_time or datetime.utcnow()

    def process(self, data: dict) -> dict:
        """Validate signals and create sized positions.

        Input: {"signals": [Signal, ...]}
        Output: {"positions": [Position, ...]}
        """
        signals = data.get("signals", [])
        if not signals:
            return {"positions": []}

        self._reset_daily_weekly()
        approved_positions = []

        for signal in signals:
            position = self._validate_and_size(signal)
            if position:
                approved_positions.append(position)
                self.open_positions.append(position)
                bus.publish("position_opened", position)

        return {"positions": approved_positions}

    def _validate_and_size(self, signal: Signal) -> Position | None:
        """Validate signal against risk rules and calculate position size."""

        # Check daily loss limit (only triggers on losses, not profits)
        if self.daily_pnl < 0 and abs(self.daily_pnl) >= self.capital * DAILY_LOSS_LIMIT:
            self.logger.warning("DAILY LOSS LIMIT reached - rejecting signal")
            return None

        # Check weekly loss limit
        if self.weekly_pnl < 0 and abs(self.weekly_pnl) >= self.capital * WEEKLY_LOSS_LIMIT:
            self.logger.warning("WEEKLY LOSS LIMIT reached - reducing size")
            self.reduced_size = True

        # Check max concurrent positions
        if len(self.open_positions) >= MAX_CONCURRENT_POSITIONS:
            self.logger.warning(
                f"Max concurrent positions ({MAX_CONCURRENT_POSITIONS}) reached"
            )
            return None

        # Check total portfolio risk
        total_risk = sum(p.risk_amount for p in self.open_positions)
        if total_risk >= self.capital * MAX_PORTFOLIO_RISK:
            self.logger.warning("Max portfolio risk reached")
            return None

        # Calculate position size
        risk_per_trade = self.capital * MAX_RISK_PER_TRADE
        if self.reduced_size:
            risk_per_trade *= 0.5

        # Distance to stop loss in price
        if signal.direction == Direction.LONG:
            sl_distance = signal.entry_price - signal.stop_loss
        else:
            sl_distance = signal.stop_loss - signal.entry_price

        if sl_distance <= 0:
            self.logger.warning("Invalid SL distance")
            return None

        # Position size: risk_amount / sl_distance = units
        # We want: N * sl_distance = risk_per_trade (in account currency)
        # Simplified: assume GBP ~ USD for now (will add conversion later)
        size_units = risk_per_trade / sl_distance

        # Cap at reasonable size (2 standard lots max for this asset)
        asset = get_asset(resolve_pair_name(signal.pair))
        size_units = min(size_units, asset.lot_size * 2)

        position = Position(
            signal=signal,
            status=TradeStatus.OPEN,
            entry_price=signal.entry_price,
            size=round(size_units, 0),
            risk_amount=round(risk_per_trade, 2),
            opened_at=self.now,
            tags=[signal.signal_type.value],
        )

        sl_pips = sl_distance / asset.pip_value
        self.logger.info(
            f"APPROVED: {signal.direction.value} | "
            f"size={size_units:.0f} units | "
            f"risk=£{risk_per_trade:.2f} | "
            f"SL={sl_pips:.1f} pips"
        )

        return position

    def close_position(self, position: Position, exit_price: float):
        """Close a position and update P&L."""
        position.status = TradeStatus.CLOSED
        position.exit_price = exit_price
        position.closed_at = self.now

        asset = get_asset(resolve_pair_name(position.signal.pair))
        if position.signal.direction == Direction.LONG:
            pnl_pips = (exit_price - position.entry_price) / asset.pip_value
        else:
            pnl_pips = (position.entry_price - exit_price) / asset.pip_value

        position.pnl_pips = round(pnl_pips, 1)
        # P&L = price_change * position_size (matches our sizing: size = risk / sl_distance)
        price_change = (exit_price - position.entry_price) if position.signal.direction == Direction.LONG else (position.entry_price - exit_price)
        position.pnl = round(price_change * position.size, 2)

        self.capital += position.pnl
        self.daily_pnl += position.pnl
        self.weekly_pnl += position.pnl

        self.open_positions = [p for p in self.open_positions if p != position]
        self.closed_positions.append(position)

        self.logger.info(
            f"CLOSED: {position.signal.direction.value} | "
            f"pnl={position.pnl_pips:+.1f} pips (£{position.pnl:+.2f}) | "
            f"capital=£{self.capital:.2f}"
        )

        bus.publish("position_closed", position)

    def update_positions(self, current_price: float, pair: str | None = None):
        """Check SL/TP for open positions, optionally filtered by pair."""
        for position in self.open_positions[:]:
            if pair and position.signal.pair != pair:
                continue
            signal = position.signal

            if signal.direction == Direction.LONG:
                if current_price <= signal.stop_loss:
                    self.close_position(position, signal.stop_loss)
                elif current_price >= signal.take_profit:
                    self.close_position(position, signal.take_profit)
            else:
                if current_price >= signal.stop_loss:
                    self.close_position(position, signal.stop_loss)
                elif current_price <= signal.take_profit:
                    self.close_position(position, signal.take_profit)

    def _reset_daily_weekly(self):
        today = self.now.date()
        if today != self.last_daily_reset:
            self.daily_pnl = 0.0
            self.last_daily_reset = today
            self.logger.debug("Daily P&L reset")

        # Reset weekly on Monday
        if today.weekday() == 0 and today != self.last_weekly_reset:
            self.weekly_pnl = 0.0
            self.reduced_size = False
            self.last_weekly_reset = today
            self.logger.debug("Weekly P&L reset")

    def get_stats(self) -> dict:
        """Get current risk stats."""
        return {
            "capital": self.capital,
            "open_positions": len(self.open_positions),
            "total_open_risk": sum(p.risk_amount for p in self.open_positions),
            "daily_pnl": self.daily_pnl,
            "weekly_pnl": self.weekly_pnl,
            "total_trades": len(self.closed_positions),
            "reduced_size": self.reduced_size,
        }
