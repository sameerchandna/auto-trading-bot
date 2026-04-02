"""FastAPI dashboard application."""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from config.settings import PAIR, PAIR_NAME, STARTING_CAPITAL, TIMEFRAMES
from storage.database import (
    CandleRecord, PositionRecord, SignalRecord,
    BacktestRecord, ParameterRecord, get_session,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="EURUSD Trading Bot Dashboard")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


@app.get("/api/status")
async def get_status():
    session = get_session()
    try:
        open_positions = session.query(PositionRecord).filter_by(status="open").count()
        closed_positions = session.query(PositionRecord).filter_by(status="closed").count()
        total_pnl = sum(
            r.pnl for r in session.query(PositionRecord).filter_by(status="closed").all()
            if r.pnl
        )
        signals_today = session.query(SignalRecord).filter(
            SignalRecord.timestamp >= datetime.utcnow().replace(hour=0, minute=0)
        ).count()

        return {
            "pair": PAIR_NAME,
            "capital": round(STARTING_CAPITAL + total_pnl, 2),
            "starting_capital": STARTING_CAPITAL,
            "open_positions": open_positions,
            "closed_positions": closed_positions,
            "total_pnl": round(total_pnl, 2),
            "signals_today": signals_today,
        }
    finally:
        session.close()


@app.get("/api/trades")
async def get_trades(limit: int = 50, offset: int = 0):
    session = get_session()
    try:
        trades = (
            session.query(PositionRecord)
            .order_by(PositionRecord.opened_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return [
            {
                "id": t.id,
                "direction": t.direction,
                "status": t.status,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "size": t.size,
                "pnl": t.pnl,
                "pnl_pips": t.pnl_pips,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "signal_type": t.signal_type,
                "confluence_score": t.confluence_score,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
            }
            for t in trades
        ]
    finally:
        session.close()


@app.get("/api/equity")
async def get_equity():
    session = get_session()
    try:
        trades = (
            session.query(PositionRecord)
            .filter_by(status="closed")
            .order_by(PositionRecord.closed_at.asc())
            .all()
        )
        equity = STARTING_CAPITAL
        curve = [{"date": None, "equity": equity}]

        for t in trades:
            if t.pnl:
                equity += t.pnl
                curve.append({
                    "date": t.closed_at.isoformat() if t.closed_at else None,
                    "equity": round(equity, 2),
                })

        return curve
    finally:
        session.close()


@app.get("/api/candles/{timeframe}")
async def get_candles(timeframe: str, limit: int = 200):
    session = get_session()
    try:
        records = (
            session.query(CandleRecord)
            .filter_by(pair=PAIR, timeframe=timeframe)
            .order_by(CandleRecord.timestamp.desc())
            .limit(limit)
            .all()
        )
        records.reverse()
        return [
            {
                "timestamp": r.timestamp.isoformat(),
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in records
        ]
    finally:
        session.close()


@app.get("/api/signals")
async def get_signals(limit: int = 20):
    session = get_session()
    try:
        signals = (
            session.query(SignalRecord)
            .order_by(SignalRecord.timestamp.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": s.id,
                "timestamp": s.timestamp.isoformat(),
                "direction": s.direction,
                "signal_type": s.signal_type,
                "entry_price": s.entry_price,
                "stop_loss": s.stop_loss,
                "take_profit": s.take_profit,
                "confluence_score": s.confluence_score,
                "timeframe": s.entry_timeframe,
            }
            for s in signals
        ]
    finally:
        session.close()


@app.get("/api/backtests")
async def get_backtests():
    session = get_session()
    try:
        runs = (
            session.query(BacktestRecord)
            .order_by(BacktestRecord.timestamp.desc())
            .limit(10)
            .all()
        )
        return [
            {
                "id": r.id,
                "timestamp": r.timestamp.isoformat(),
                "start_date": r.start_date.isoformat(),
                "end_date": r.end_date.isoformat(),
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "total_pnl": r.total_pnl,
                "max_drawdown": r.max_drawdown,
                "sharpe_ratio": r.sharpe_ratio,
            }
            for r in runs
        ]
    finally:
        session.close()


@app.get("/api/learning")
async def get_learning():
    session = get_session()
    try:
        params = (
            session.query(ParameterRecord)
            .order_by(ParameterRecord.version.desc())
            .limit(20)
            .all()
        )

        # Setup stats from closed trades
        trades = session.query(PositionRecord).filter_by(status="closed").all()
        stats = {}
        for t in trades:
            st = t.signal_type or "unknown"
            if st not in stats:
                stats[st] = {"total": 0, "wins": 0, "total_pnl": 0}
            stats[st]["total"] += 1
            if t.pnl and t.pnl > 0:
                stats[st]["wins"] += 1
            if t.pnl:
                stats[st]["total_pnl"] += t.pnl

        for st in stats:
            stats[st]["win_rate"] = (
                stats[st]["wins"] / stats[st]["total"]
                if stats[st]["total"] > 0 else 0
            )

        return {
            "parameter_history": [
                {
                    "version": p.version,
                    "timestamp": p.timestamp.isoformat(),
                    "score": p.performance_score,
                }
                for p in params
            ],
            "setup_stats": stats,
        }
    finally:
        session.close()
