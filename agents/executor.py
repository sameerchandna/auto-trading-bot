"""Executor Agent - paper trading execution."""
import json
import logging
from datetime import datetime

from agents.base import BaseAgent
from data.models import Position, TradeStatus
from storage.database import PositionRecord, SignalRecord, get_session
from engine.event_bus import bus

logger = logging.getLogger(__name__)


class ExecutorAgent(BaseAgent):
    """Paper trading executor - logs trades to database."""

    def __init__(self):
        super().__init__("executor")
        self.execution_log: list[dict] = []

    def process(self, data: dict) -> dict:
        """Execute approved positions (paper trading).

        Input: {"positions": [Position, ...]}
        Output: {"executed": [Position, ...]}
        """
        positions = data.get("positions", [])
        executed = []

        for position in positions:
            self._record_trade(position)
            executed.append(position)
            self.execution_log.append({
                "timestamp": datetime.utcnow().isoformat(),
                "direction": position.signal.direction.value,
                "entry_price": position.entry_price,
                "size": position.size,
                "stop_loss": position.signal.stop_loss,
                "take_profit": position.signal.take_profit,
                "confluence": position.signal.confluence_score,
            })

        return {"executed": executed}

    def _record_trade(self, position: Position):
        """Save trade to database."""
        session = get_session()
        try:
            # Save signal
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

            # Save position
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
        """Update position record when closed."""
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
