"""Executor Agent - executes trades on OANDA practice account."""
import json
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

from agents.base import BaseAgent
from data.models import Position, TradeStatus, Direction
from storage.database import PositionRecord, SignalRecord, get_session
from engine.event_bus import bus

load_dotenv()
logger = logging.getLogger(__name__)

USE_OANDA = bool(os.getenv("OANDA_API_KEY"))


class ExecutorAgent(BaseAgent):
    """Executes trades on OANDA practice account and logs to database."""

    def __init__(self):
        super().__init__("executor")
        self.execution_log: list[dict] = []
        self.oanda_trade_map: dict[int, str] = {}  # position_id -> oanda_trade_id

    def process(self, data: dict) -> dict:
        """Execute approved positions.

        Input: {"positions": [Position, ...]}
        Output: {"executed": [Position, ...]}
        """
        positions = data.get("positions", [])
        executed = []

        for position in positions:
            # Execute on OANDA
            oanda_trade_id = None
            if USE_OANDA:
                oanda_trade_id = self._execute_oanda(position)

            # Record to database
            self._record_trade(position)

            if oanda_trade_id and position.id:
                self.oanda_trade_map[position.id] = oanda_trade_id

            executed.append(position)
            self.execution_log.append({
                "timestamp": datetime.utcnow().isoformat(),
                "direction": position.signal.direction.value,
                "entry_price": position.entry_price,
                "size": position.size,
                "stop_loss": position.signal.stop_loss,
                "take_profit": position.signal.take_profit,
                "confluence": position.signal.confluence_score,
                "oanda_trade_id": oanda_trade_id,
            })

        return {"executed": executed}

    def _execute_oanda(self, position: Position) -> str | None:
        """Place order on OANDA practice account."""
        try:
            from data.oanda import place_order

            units = int(position.size)
            result = place_order(
                direction=position.signal.direction.value,
                units=units,
                stop_loss=position.signal.stop_loss,
                take_profit=position.signal.take_profit,
            )

            # Extract trade ID from response
            fill = result.get("orderFillTransaction", {})
            trade_id = None
            if fill:
                trades_opened = fill.get("tradeOpened", {})
                trade_id = trades_opened.get("tradeID")
                actual_price = float(fill.get("price", 0))
                self.logger.info(
                    f"OANDA EXECUTED: {position.signal.direction.value} "
                    f"{units} units @ {actual_price:.5f} | "
                    f"Trade ID: {trade_id}"
                )
                # Update position with actual fill price
                if actual_price > 0:
                    position.entry_price = actual_price

            return trade_id

        except Exception as e:
            self.logger.error(f"OANDA execution failed: {e}")
            return None

    def _record_trade(self, position: Position):
        """Save trade to database."""
        session = get_session()
        try:
            signal_rec = SignalRecord(
                timestamp=position.signal.timestamp,
                pair=position.signal.pair,
                direction=position.signal.direction.value,
                signal_type=position.signal.signal_type.value,
                entry_price=position.signal.entry_price,
                stop_loss=position.signal.stop_loss,
                take_profit=position.signal.take_profit,
                confluence_score=position.signal.confluence_score,
                rationale=json.dumps(position.signal.rationale),
                entry_timeframe=position.signal.entry_timeframe,
                trigger_timeframe=position.signal.trigger_timeframe,
            )
            session.add(signal_rec)
            session.flush()

            pos_rec = PositionRecord(
                signal_id=signal_rec.id,
                status=position.status.value,
                direction=position.signal.direction.value,
                entry_price=position.entry_price,
                size=position.size,
                risk_amount=position.risk_amount,
                opened_at=position.opened_at,
                signal_type=position.signal.signal_type.value,
                stop_loss=position.signal.stop_loss,
                take_profit=position.signal.take_profit,
                confluence_score=position.signal.confluence_score,
            )
            session.add(pos_rec)
            session.commit()

            position.id = pos_rec.id
            self.logger.info(f"Trade #{pos_rec.id} recorded to database")

        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to record trade: {e}")
        finally:
            session.close()

    def record_close(self, position: Position):
        """Update position record when closed, and close on OANDA."""
        # Close on OANDA if we have a trade ID
        if USE_OANDA and position.id in self.oanda_trade_map:
            try:
                from data.oanda import close_trade
                trade_id = self.oanda_trade_map[position.id]
                close_trade(trade_id)
                self.logger.info(f"OANDA trade {trade_id} closed")
                del self.oanda_trade_map[position.id]
            except Exception as e:
                self.logger.error(f"OANDA close failed: {e}")

        # Update database
        session = get_session()
        try:
            rec = session.query(PositionRecord).filter_by(id=position.id).first()
            if rec:
                rec.status = position.status.value
                rec.exit_price = position.exit_price
                rec.closed_at = position.closed_at
                rec.pnl = position.pnl
                rec.pnl_pips = position.pnl_pips
                session.commit()
        except Exception as e:
            session.rollback()
            self.logger.error(f"Failed to update closed trade: {e}")
        finally:
            session.close()
