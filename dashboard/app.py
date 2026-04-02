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


@app.get("/api/price")
async def get_live_price():
    """Get live EUR/USD price from OANDA."""
    try:
        from data.ingestion import get_live_price
        return get_live_price()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/account")
async def get_oanda_account():
    """Get OANDA practice account summary."""
    try:
        from data.oanda import get_account_summary
        acct = get_account_summary()
        return {
            "balance": float(acct["balance"]),
            "unrealized_pnl": float(acct.get("unrealizedPL", 0)),
            "nav": float(acct.get("NAV", acct["balance"])),
            "margin_used": float(acct.get("marginUsed", 0)),
            "margin_available": float(acct.get("marginAvailable", acct["balance"])),
            "open_trades": int(acct.get("openTradeCount", 0)),
            "currency": acct.get("currency", "GBP"),
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/status")
async def get_status():
    session = get_session()
    try:
        open_positions = session.query(PositionRecord).filter_by(status="open").count()
        closed_positions = session.query(PositionRecord).filter_by(status="closed").count()
        total_pnl = sum(
            r.pnl for r in session.query(PositionRecord)
            .filter_by(status="closed")
            .filter(~PositionRecord.tags.contains('"backtest"'))
            .all()
            if r.pnl
        )
        signals_today = session.query(SignalRecord).filter(
            SignalRecord.timestamp >= datetime.utcnow().replace(hour=0, minute=0)
        ).count()

        # Get live price
        live_price = None
        try:
            from data.ingestion import get_live_price
            price_data = get_live_price()
            live_price = price_data.get("mid")
        except Exception:
            pass

        return {
            "pair": PAIR_NAME,
            "capital": round(STARTING_CAPITAL + total_pnl, 2),
            "starting_capital": STARTING_CAPITAL,
            "open_positions": open_positions,
            "closed_positions": closed_positions,
            "total_pnl": round(total_pnl, 2),
            "signals_today": signals_today,
            "live_price": live_price,
        }
    finally:
        session.close()


@app.get("/api/trades")
async def get_trades(limit: int = 2000, offset: int = 0, source: str = "live", bt_id: int = 0):
    session = get_session()
    try:
        from storage.database import TradeJournalRecord

        query = session.query(PositionRecord)
        if bt_id > 0:
            query = query.filter(PositionRecord.tags.contains(f'"bt_{bt_id}"'))
        elif source == "backtest":
            query = query.filter(PositionRecord.tags.contains('"backtest"'))
        else:
            # live: exclude all backtest trades
            query = query.filter(~PositionRecord.tags.contains('"backtest"'))

        trades = (
            query
            .order_by(PositionRecord.opened_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Bulk load journal entries for duration/MFE/MAE
        trade_ids = [t.id for t in trades]
        journals = {
            j.position_id: j
            for j in session.query(TradeJournalRecord)
            .filter(TradeJournalRecord.position_id.in_(trade_ids))
            .all()
        } if trade_ids else {}

        result = []
        for t in trades:
            # Compute win/loss
            if t.status == "closed" and t.pnl is not None:
                outcome = "win" if t.pnl > 0 else ("loss" if t.pnl < 0 else "breakeven")
            else:
                outcome = None

            # Compute planned RR from entry/SL/TP
            planned_rr = None
            if t.entry_price and t.stop_loss and t.take_profit:
                sl_dist = abs(t.entry_price - t.stop_loss)
                tp_dist = abs(t.take_profit - t.entry_price)
                if sl_dist > 0:
                    planned_rr = round(tp_dist / sl_dist, 2)

            # Compute actual RR (how much R was captured)
            actual_rr = None
            if t.status == "closed" and t.pnl is not None and t.risk_amount and t.risk_amount > 0:
                actual_rr = round(t.pnl / t.risk_amount, 2)

            # Duration
            duration_mins = None
            journal = journals.get(t.id)
            if journal and journal.duration_minutes:
                duration_mins = journal.duration_minutes
            elif t.opened_at and t.closed_at:
                duration_mins = int((t.closed_at - t.opened_at).total_seconds() / 60)

            # MFE/MAE from journal
            max_favorable = journal.max_favorable if journal else None
            max_adverse = journal.max_adverse if journal else None

            result.append({
                "id": t.id,
                "direction": t.direction,
                "status": t.status,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "size": t.size,
                "risk_amount": t.risk_amount,
                "pnl": t.pnl,
                "pnl_pips": t.pnl_pips,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                "signal_type": t.signal_type,
                "confluence_score": t.confluence_score,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "outcome": outcome,
                "planned_rr": planned_rr,
                "actual_rr": actual_rr,
                "duration_mins": duration_mins,
                "max_favorable": max_favorable,
                "max_adverse": max_adverse,
                "tags": json.loads(t.tags) if t.tags else [],
            })

        return result
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
            .limit(50)
            .all()
        )
        result = []
        for r in runs:
            params = json.loads(r.params_json) if r.params_json else {}
            metrics = json.loads(r.results_json) if r.results_json else {}
            result.append({
                "id": r.id,
                "timestamp": r.timestamp.isoformat(),
                "start_date": r.start_date.isoformat(),
                "end_date": r.end_date.isoformat(),
                "config": params.get("config", "baseline"),
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "total_pnl": r.total_pnl,
                "max_drawdown": r.max_drawdown,
                "sharpe_ratio": r.sharpe_ratio,
                "wins": metrics.get("wins", 0),
                "losses": metrics.get("losses", 0),
                "profit_factor": metrics.get("profit_factor", 0),
                "expectancy_pips": metrics.get("expectancy_pips", 0),
                "avg_win_pips": metrics.get("avg_win_pips", 0),
                "avg_loss_pips": metrics.get("avg_loss_pips", 0),
                "consecutive_losses": metrics.get("consecutive_losses", 0),
                "sortino_ratio": metrics.get("sortino_ratio", 0),
            })
        return result
    finally:
        session.close()


@app.get("/api/compare")
async def get_comparisons():
    """Group backtest runs into baseline/variant pairs for comparison view."""
    session = get_session()
    try:
        runs = (
            session.query(BacktestRecord)
            .order_by(BacktestRecord.timestamp.asc())
            .all()
        )

        # Group by (start_date, end_date) — runs on the same period can be compared
        from collections import defaultdict
        groups: dict = defaultdict(list)
        for r in runs:
            params = json.loads(r.params_json) if r.params_json else {}
            metrics = json.loads(r.results_json) if r.results_json else {}
            period = f"{r.start_date.date()} to {r.end_date.date()}"
            groups[period].append({
                "id": r.id,
                "timestamp": r.timestamp.isoformat(),
                "config": params.get("config", "baseline"),
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "total_pnl": r.total_pnl,
                "max_drawdown": r.max_drawdown,
                "sharpe_ratio": r.sharpe_ratio,
                "wins": metrics.get("wins", 0),
                "losses": metrics.get("losses", 0),
                "profit_factor": metrics.get("profit_factor", 0),
                "expectancy_pips": metrics.get("expectancy_pips", 0),
                "avg_win_pips": metrics.get("avg_win_pips", 0),
                "avg_loss_pips": metrics.get("avg_loss_pips", 0),
                "consecutive_losses": metrics.get("consecutive_losses", 0),
            })

        # Return groups with >1 run (i.e. have a comparison) first, then singles
        result = []
        for period, group_runs in sorted(groups.items(), key=lambda x: len(x[1]), reverse=True):
            result.append({"period": period, "runs": group_runs})
        return result
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
