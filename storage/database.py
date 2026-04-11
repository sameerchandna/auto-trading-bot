"""SQLite database setup and session management."""
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Column, DateTime, Enum, Float, Integer, String, Text,
    create_engine, Index,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config.settings import DB_PATH

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class CandleRecord(Base):
    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pair = Column(String(20), nullable=False)
    timeframe = Column(String(10), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(Float, default=0.0)

    __table_args__ = (
        Index("ix_candles_lookup", "pair", "timeframe", "timestamp", unique=True),
    )


class SignalRecord(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    pair = Column(String(20), nullable=False)
    direction = Column(String(10), nullable=False)
    signal_type = Column(String(30), nullable=False)
    entry_price = Column(Float, nullable=False)
    stop_loss = Column(Float, nullable=False)
    take_profit = Column(Float, nullable=False)
    confluence_score = Column(Float, nullable=False)
    rationale = Column(Text, default="{}")
    entry_timeframe = Column(String(10), default="15m")
    trigger_timeframe = Column(String(10), default="4h")
    # Feature vector (Priority 5 — episodic memory enrichment)
    score_htf_bias = Column(Float, nullable=True)
    score_bos = Column(Float, nullable=True)
    score_wave_position = Column(Float, nullable=True)
    score_liquidity_sweep = Column(Float, nullable=True)
    score_sr_reaction = Column(Float, nullable=True)
    score_wave_ending = Column(Float, nullable=True)
    score_catalyst = Column(Float, nullable=True)
    regime = Column(String(20), nullable=True)
    adx = Column(Float, nullable=True)
    atr = Column(Float, nullable=True)


class PositionRecord(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    signal_id = Column(Integer, nullable=True)
    pair = Column(String(20), default="EURUSD")
    status = Column(String(20), default="open")
    direction = Column(String(10), nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    size = Column(Float, default=0.0)
    risk_amount = Column(Float, default=0.0)
    opened_at = Column(DateTime, nullable=True)
    closed_at = Column(DateTime, nullable=True)
    pnl = Column(Float, default=0.0)
    pnl_pips = Column(Float, default=0.0)
    tags = Column(Text, default="[]")
    signal_type = Column(String(30), default="")
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    confluence_score = Column(Float, default=0.0)
    oanda_trade_id = Column(String(50), nullable=True)


class TradeJournalRecord(Base):
    __tablename__ = "trade_journal"

    id = Column(Integer, primary_key=True, autoincrement=True)
    position_id = Column(Integer, nullable=False)
    pair = Column(String(20), default="EURUSD")
    analysis_snapshot = Column(Text, default="{}")
    max_favorable = Column(Float, default=0.0)
    max_adverse = Column(Float, default=0.0)
    duration_minutes = Column(Integer, default=0)
    notes = Column(Text, default="")


class ParameterRecord(Base):
    __tablename__ = "parameters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    params_json = Column(Text, nullable=False)
    performance_score = Column(Float, default=0.0)


class BacktestRecord(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pair = Column(String(20), default="EURUSD")
    timestamp = Column(DateTime, default=datetime.utcnow)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    params_json = Column(Text, default="{}")
    results_json = Column(Text, default="{}")
    total_trades = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    sharpe_ratio = Column(Float, default=0.0)
    fold_parent_id = Column(Integer, nullable=True, index=True)


class BacktestFoldsRun(Base):
    __tablename__ = "backtest_folds_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pair = Column(String(20), default="EURUSD")
    scheme = Column(String(30), default="walkforward")
    num_folds = Column(Integer, default=0)
    start_date = Column(String(20), default="")
    end_date = Column(String(20), default="")
    params_json = Column(Text, default="{}")
    summary_json = Column(Text, default="{}")
    combined_metrics_json = Column(Text, default="{}")
    combined_equity_curve_json = Column(Text, default="[]")
    per_fold_json = Column(Text, default="[]")
    label = Column(String(100), default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class EquitySnapshotRecord(Base):
    """End-of-cycle equity snapshot for rolling performance analysis."""
    __tablename__ = "equity_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    pair = Column(String(20), nullable=True)        # NULL = account-level
    equity = Column(Float, nullable=False)
    cash = Column(Float, nullable=False)
    unrealized_pnl = Column(Float, default=0.0)
    daily_return = Column(Float, default=0.0)
    drawdown_pct = Column(Float, default=0.0)
    regime = Column(String(20), default="")
    open_positions = Column(Integer, default=0)

    __table_args__ = (
        Index("ix_equity_snap_ts", "timestamp"),
    )


# --- Audit Log (separate DB — never blocks trading) ---

AUDIT_DB_PATH = DB_PATH.parent / "audit_log.db"


class AuditBase(DeclarativeBase):
    pass


class AuditLogRecord(AuditBase):
    """Write-only audit trail of every agent decision."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)
    agent = Column(String(30), nullable=False)
    action = Column(String(50), nullable=False)
    pair = Column(String(20), default="")
    details_json = Column(Text, default="{}")

    __table_args__ = (
        Index("ix_audit_ts", "timestamp"),
        Index("ix_audit_agent", "agent"),
    )


_audit_engine = None
_AuditSessionFactory = None


def get_audit_session() -> Session:
    """Get a session for the audit log DB (separate from main DB)."""
    global _audit_engine, _AuditSessionFactory
    if _audit_engine is None:
        AUDIT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _audit_engine = create_engine(f"sqlite:///{AUDIT_DB_PATH}", echo=False)
        AuditBase.metadata.create_all(_audit_engine)
    if _AuditSessionFactory is None:
        _AuditSessionFactory = sessionmaker(bind=_audit_engine)
    return _AuditSessionFactory()


def log_audit(agent: str, action: str, pair: str = "", details: dict | None = None):
    """Fire-and-forget audit log entry. Swallows exceptions — audit must never crash the pipeline."""
    try:
        session = get_audit_session()
        session.add(AuditLogRecord(
            timestamp=datetime.utcnow(),
            agent=agent,
            action=action,
            pair=pair,
            details_json=json.dumps(details or {}, default=str),
        ))
        session.commit()
        session.close()
    except Exception as e:
        logger.debug(f"Audit log write failed (non-fatal): {e}")


# --- Main Engine and session factory ---
_engine = None
_SessionFactory = None


def _migrate_add_pair_columns():
    """Add missing columns to tables (backward-compatible)."""
    conn = sqlite3.connect(str(DB_PATH))
    for table in ["positions", "backtest_runs", "trade_journal"]:
        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if "pair" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN pair VARCHAR(20) DEFAULT 'EURUSD'")
    pos_cols = [row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()]
    if "oanda_trade_id" not in pos_cols:
        conn.execute("ALTER TABLE positions ADD COLUMN oanda_trade_id VARCHAR(50)")
    bt_cols = [row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()]
    if "fold_parent_id" not in bt_cols:
        conn.execute("ALTER TABLE backtest_runs ADD COLUMN fold_parent_id INTEGER")
    # Signal feature vector columns (Priority 5)
    sig_cols = [row[1] for row in conn.execute("PRAGMA table_info(signals)").fetchall()]
    for col_name, col_type in [
        ("score_htf_bias", "FLOAT"), ("score_bos", "FLOAT"),
        ("score_wave_position", "FLOAT"), ("score_liquidity_sweep", "FLOAT"),
        ("score_sr_reaction", "FLOAT"), ("score_wave_ending", "FLOAT"),
        ("score_catalyst", "FLOAT"), ("regime", "VARCHAR(20)"),
        ("adx", "FLOAT"), ("atr", "FLOAT"),
    ]:
        if col_name not in sig_cols:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {col_name} {col_type}")
    conn.commit()
    conn.close()


def get_engine():
    global _engine
    if _engine is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
        Base.metadata.create_all(_engine)
        _migrate_add_pair_columns()
    return _engine


def get_session() -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory()


def query_recent_closed_positions(n: int = 50) -> list[PositionRecord]:
    """Return the last *n* closed positions ordered by closed_at DESC."""
    session = get_session()
    try:
        return (
            session.query(PositionRecord)
            .filter_by(status="closed")
            .order_by(PositionRecord.closed_at.desc())
            .limit(n)
            .all()
        )
    finally:
        session.close()
