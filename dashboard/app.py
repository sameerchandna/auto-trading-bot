"""FastAPI dashboard application."""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from config.assets import ASSETS, ACTIVE_ASSETS, DEFAULT_ASSET, get_asset
from config.settings import PAIR, PAIR_NAME, STARTING_CAPITAL, TIMEFRAMES
from storage.database import (
    CandleRecord, PositionRecord, SignalRecord,
    BacktestRecord, ParameterRecord, get_session,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Auto Trading Bot Dashboard")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Approval routes for one-click email actions (Phase 3)
from notifications.approval_handler import register_approval_routes
register_approval_routes(app)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text()


@app.get("/api/assets")
async def list_assets():
    """List available and active assets."""
    return {
        "assets": {
            name: {
                "name": spec.name,
                "asset_class": spec.asset_class,
                "pip_value": spec.pip_value,
                "price_decimals": spec.price_decimals,
                "active": name in ACTIVE_ASSETS,
            }
            for name, spec in ASSETS.items()
        },
        "active": ACTIVE_ASSETS,
        "default": DEFAULT_ASSET,
    }


@app.get("/api/price")
async def get_live_price(pair: str = PAIR_NAME):
    """Get live price from OANDA."""
    try:
        from data.ingestion import get_live_price
        asset = get_asset(pair)
        return get_live_price(pair=asset.yahoo_ticker)
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
async def get_status(pair: str = ""):
    session = get_session()
    try:
        open_q = session.query(PositionRecord).filter_by(status="open")
        closed_q = session.query(PositionRecord).filter_by(status="closed").filter(
            ~PositionRecord.tags.contains('"backtest"')
        )
        signals_q = session.query(SignalRecord).filter(
            SignalRecord.timestamp >= datetime.utcnow().replace(hour=0, minute=0)
        )

        if pair:
            open_q = open_q.filter_by(pair=pair)
            closed_q = closed_q.filter_by(pair=pair)
            signals_q = signals_q.filter_by(pair=pair)

        open_positions = open_q.count()
        closed_records = closed_q.all()
        total_pnl = sum(r.pnl for r in closed_records if r.pnl)
        signals_today = signals_q.count()

        # Get live price
        live_price = None
        try:
            from data.ingestion import get_live_price as _get_price
            target = pair or DEFAULT_ASSET
            asset = get_asset(target)
            price_data = _get_price(pair=asset.yahoo_ticker)
            live_price = price_data.get("mid")
        except Exception:
            pass

        return {
            "pair": pair or DEFAULT_ASSET,
            "active_assets": ACTIVE_ASSETS,
            "capital": round(STARTING_CAPITAL + total_pnl, 2),
            "starting_capital": STARTING_CAPITAL,
            "open_positions": open_positions,
            "closed_positions": len(closed_records),
            "total_pnl": round(total_pnl, 2),
            "signals_today": signals_today,
            "live_price": live_price,
        }
    finally:
        session.close()


@app.get("/api/trades")
async def get_trades(limit: int = 2000, offset: int = 0, source: str = "live", bt_id: int = 0, pair: str = ""):
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

        if pair:
            query = query.filter_by(pair=pair)

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
                "pair": t.pair or DEFAULT_ASSET,
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
async def get_equity(pair: str = ""):
    session = get_session()
    try:
        query = session.query(PositionRecord).filter_by(status="closed")
        if pair:
            query = query.filter_by(pair=pair)
        trades = query.order_by(PositionRecord.closed_at.asc()).all()
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
async def get_candles(timeframe: str, limit: int = 200, pair: str = PAIR_NAME):
    asset = get_asset(pair)
    session = get_session()
    try:
        records = (
            session.query(CandleRecord)
            .filter_by(pair=asset.yahoo_ticker, timeframe=timeframe)
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
async def get_signals(limit: int = 20, pair: str = ""):
    session = get_session()
    try:
        query = session.query(SignalRecord)
        if pair:
            query = query.filter_by(pair=pair)
        signals = (
            query
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
async def get_backtests(pair: str = ""):
    session = get_session()
    try:
        query = session.query(BacktestRecord)
        if pair:
            query = query.filter_by(pair=pair)
        runs = (
            query
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
                "pair": r.pair or DEFAULT_ASSET,
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
                "long_trades": metrics.get("long_trades", 0),
                "long_wins": metrics.get("long_wins", 0),
                "long_pnl": metrics.get("long_pnl", 0),
                "short_trades": metrics.get("short_trades", 0),
                "short_wins": metrics.get("short_wins", 0),
                "short_pnl": metrics.get("short_pnl", 0),
            })
        return result
    finally:
        session.close()


@app.get("/api/compare")
async def get_comparisons(pair: str = ""):
    """Group backtest runs into baseline/variant pairs for comparison view."""
    session = get_session()
    try:
        query = session.query(BacktestRecord)
        if pair:
            query = query.filter_by(pair=pair)
        runs = query.order_by(BacktestRecord.timestamp.asc()).all()

        # Group by (start_date, end_date) — runs on the same period can be compared
        from collections import defaultdict
        groups: dict = defaultdict(list)
        for r in runs:
            params = json.loads(r.params_json) if r.params_json else {}
            metrics = json.loads(r.results_json) if r.results_json else {}
            period = f"{r.start_date.date()} to {r.end_date.date()}"
            groups[period].append({
                "id": r.id,
                "pair": r.pair or DEFAULT_ASSET,
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


@app.get("/api/research")
async def get_research():
    """Return research-agent history for the dashboard Research tab.

    Reads research/test_history.json (written by scheduler/research_job.py).
    Surfaces baselines, budget, and the 50 most recent test entries.
    """
    history_file = Path(__file__).parent.parent / "research" / "test_history.json"
    if not history_file.exists():
        return {"tests": [], "baselines": {}, "budget": {}}
    try:
        data = json.loads(history_file.read_text())
    except json.JSONDecodeError as e:
        return {"error": f"test_history.json malformed: {e}"}

    tests = data.get("tests", [])[-50:]
    slim = []
    for t in reversed(tests):  # newest first
        agg = t.get("aggregate") or {}
        oos = t.get("oos") or {}
        slim.append({
            "id": t.get("id"),
            "tested_at": t.get("tested_at"),
            "mutation": t.get("mutation"),
            "params_hash": t.get("params_hash"),
            "verdict": t.get("verdict"),
            "verdict_reason": t.get("verdict_reason"),
            "walk_forward_pass_rate": t.get("walk_forward_pass_rate"),
            "median_profit_factor": agg.get("median_profit_factor"),
            "median_win_rate": agg.get("median_win_rate"),
            "median_max_drawdown_pct": agg.get("median_max_drawdown_pct"),
            "median_expectancy_pips": agg.get("median_expectancy_pips"),
            "oos_trades": oos.get("trades"),
            "oos_profit_factor": oos.get("profit_factor"),
            "oos_win_rate": oos.get("win_rate"),
            "windows": [
                {
                    "window_id": w.get("window_id"),
                    "period": w.get("period"),
                    "passed": w.get("passed"),
                    "val_trades": (w.get("val") or {}).get("trades"),
                    "val_profit_factor": (w.get("val") or {}).get("profit_factor"),
                }
                for w in (t.get("windows") or [])
            ],
            "approval": t.get("approval"),
            "params": t.get("params"),
        })

    anchor = (data.get("anchor_baseline") or {}).get("metrics") or {}
    rolling = (data.get("rolling_baseline") or {}).get("metrics") or {}
    return {
        "baselines": {"anchor": anchor, "rolling": rolling},
        "budget": data.get("budget") or {},
        "rotation": data.get("rotation") or {},
        "tests": slim,
        "last_updated": data.get("last_updated"),
    }


@app.get("/api/data-health")
async def get_data_health():
    """Return candle counts and date ranges for all assets/timeframes."""
    import sqlite3
    from config.settings import HISTORY_START
    from config.assets import ASSETS

    db_path = Path(__file__).parent.parent / "storage" / "trading.db"
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    timeframes = ["15m", "1h", "4h", "1d", "1wk"]
    result = []

    for name, spec in ASSETS.items():
        pair = spec.yahoo_ticker
        row = {"asset": name, "asset_class": spec.asset_class, "timeframes": {}}
        for tf in timeframes:
            cur.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM candles WHERE pair=? AND timeframe=?",
                (pair, tf),
            )
            cnt, mn, mx = cur.fetchone()
            expected_start = HISTORY_START[tf].strftime("%Y-%m-%d")
            row["timeframes"][tf] = {
                "count": cnt or 0,
                "from": str(mn)[:10] if mn else None,
                "to": str(mx)[:10] if mx else None,
                "expected_from": expected_start,
                "ok": bool(mn and str(mn)[:10] <= expected_start),
            }
        result.append(row)

    conn.close()
    return {"assets": result, "checked_at": datetime.utcnow().isoformat()}


@app.get("/api/learning")
async def get_learning(pair: str = ""):
    session = get_session()
    try:
        params = (
            session.query(ParameterRecord)
            .order_by(ParameterRecord.version.desc())
            .limit(20)
            .all()
        )

        # Setup stats from closed trades
        trades_q = session.query(PositionRecord).filter_by(status="closed")
        if pair:
            trades_q = trades_q.filter_by(pair=pair)
        trades = trades_q.all()
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
